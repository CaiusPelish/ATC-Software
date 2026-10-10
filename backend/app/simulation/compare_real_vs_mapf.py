# backend/app/simulation/compare_real_vs_mapf.py

"""Real taxi movements at KEWR next to the same aircraft routed by our MAPF planner.

Every aircraft in the 2-hour Amelia recording with a complete taxi is used:
departures from first appearing on the ground until their takeoff roll,
arrivals from vacating the runway until their last ground point. Each one's
first and last taxi points are snapped to the taxiway graph, and all of them
are planned together with prioritized space-time A*, released at the moment
they really started to taxi.

The planner gets the real runway schedule (runway_schedule.py):
- every real landing and takeoff closes its runway (RunwayGate), so MAPF
  aircraft can't cross in front of them;
- each departure holds at its line-up point until its real takeoff slot,
  so departures still queue for the runway the way they really did.
Both sides are therefore timed to the same events: a departure is done when
its real takeoff roll starts, an arrival when it reaches its last ground point.

Outputs (in app/simulation/data/plans/):
  real_vs_mapf.gif        left: real ADS-B positions, right: MAPF plan, same clock
  real_vs_mapf_stats.png  taxi time, speed, moving vs holding, distance
  real_vs_mapf.csv        one row per aircraft

Not modelled on the MAPF side: pushback and gates, speed changes (it taxis at
a constant 8 m/s), and ATC workload. A departure that reaches the runway
after its real slot takes off when it gets there; the runway schedule isn't
re-sequenced around it.

Run from backend/:
    python3 -m app.simulation.compare_real_vs_mapf [--minutes 120] [--stride 10]
"""

import argparse
import csv
import math
import statistics
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

from ..routing.airport import DT, get_airport  # noqa: E402
from ..routing.mapf import AircraftRequest, plan_batch  # noqa: E402
from ..routing.osm_taxiways import edge_points, haversine_m  # noqa: E402
from ..routing.reservations import check_paths  # noqa: E402
from ..routing.runways import RunwayGate, load_runways, runway_conflicts  # noqa: E402
from ..routing.visualize_plan import OUT_DIR, position_at  # noqa: E402
from .runway_schedule import real_runway_uses  # noqa: E402
from .traffic import (  # noqa: E402
    MIN_TAXI_SECONDS,
    STOPPED_SPEED,
    NotEnoughHistory,
    qualify_departure,
    read_traffic,
    snap_to_graph_node,
    taxi_rows,
)

KT = 0.5144  # m/s per knot
DEPARTURE_HOLD = 60 // DT   # line-up and takeoff roll, when there is no slot to wait for
LINE_UP_STEPS = 1           # minimum time on the line-up point before the slot
ARRIVAL_HOLD = 20 // DT     # handed to ramp control
# A takeoff closes its runway to crossings from just before the roll until lift-off.
TAKEOFF_CLOSE_BEFORE_S = 10

# Reference palette: categorical slots 1 and 2, text and surface tokens.
REAL = "#2a78d6"
MAPF = "#eb6834"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"
TAXIWAY = "#c9ccd1"
RUNWAY = "#8a8f98"


# ---------------------------------------------------------------- data

def load_flights(df, uses):
    """[{id, kind, rows, start, goal, slot_s}] for every departure and arrival with a complete taxi.

    rows run until the real takeoff roll (departures) or from vacating the
    runway (arrivals); slot_s is a departure's real takeoff roll start.
    """
    ap = get_airport()
    nodes, graph, geometry = ap.nodes, ap.graph, ap.geometry
    t0 = int(df["UnixTime"].min())
    use_of = {u.id: u for u in uses}
    flights = []
    for flight_id, flight in df.groupby("ID"):
        use = use_of.get(str(flight_id))
        if use is None:
            continue
        flight = flight.sort_values("UnixTime")
        rel = flight["UnixTime"] - t0
        if use.kind == "takeoff":
            try:
                qualify_departure(flight, t0, nodes, graph, geometry)
            except NotEnoughHistory:
                continue
            line_up = taxi_rows(flight).iloc[-1]
            rows = flight[rel <= use.roll_start]
            end_point = (line_up["Lat"], line_up["Lon"])
            kind, slot = "departure", use.roll_start
        else:
            rows = flight[rel >= use.busy_until]
            if len(rows) < MIN_TAXI_SECONDS:
                continue
            end_point = (rows.iloc[-1]["Lat"], rows.iloc[-1]["Lon"])
            kind, slot = "arrival", None
        start = snap_to_graph_node(nodes, graph, geometry, rows.iloc[0]["Lat"], rows.iloc[0]["Lon"])
        goal = snap_to_graph_node(nodes, graph, geometry, *end_point)
        if start is None or goal is None or start[0].id == goal[0].id:
            continue
        flights.append({
            "id": str(flight_id), "kind": kind, "rows": rows, "pair": use.pair,
            "start": start[0].id, "goal": goal[0].id, "slot_s": slot,
        })
    return flights


def build_gate(uses):
    """RunwayGate closed for every real landing (2 nm final to vacated) and takeoff roll."""
    runways = load_runways()
    gate = RunwayGate([runways["4L"], runways["4R"]])
    for u in uses:
        start = u.busy_from if u.kind == "landing" else u.roll_start - TAKEOFF_CLOSE_BEFORE_S
        gate.add_window(u.pair, int(start // DT), math.ceil(u.busy_until / DT), u.id, u.kind)
    return gate


def make_requests(flights, t0, with_schedule=True):
    requests = []
    for f in flights:
        ready = (int(f["rows"]["UnixTime"].iloc[0]) - t0) // DT
        if f["kind"] == "arrival":
            requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready, goal_hold=ARRIVAL_HOLD))
        elif with_schedule:
            requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready, goal_hold=LINE_UP_STEPS,
                                            hold_until=math.ceil(f["slot_s"] / DT)))
        else:
            requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready, goal_hold=DEPARTURE_HOLD))
    return requests


def slot_order(airport, requests):
    """Departures in real takeoff order, arrivals by when they vacated, interleaved by time."""
    return [r.id for r in sorted(requests, key=lambda r: (
        r.hold_until if r.hold_until is not None else r.ready_t, r.id))]


def real_metrics(rows):
    t = rows["UnixTime"].to_numpy()
    lat, lon, speed = rows["Lat"].to_numpy(), rows["Lon"].to_numpy(), rows["Speed"].to_numpy()
    moving = speed > STOPPED_SPEED
    # Only count distance while moving, so GPS jitter while parked doesn't add metres.
    dist = sum(
        haversine_m(lat[i], lon[i], lat[i + 1], lon[i + 1])
        for i in range(len(rows) - 1) if moving[i] or moving[i + 1]
    )
    duration = float(t[-1] - t[0])
    return {
        "time_s": duration,
        "dist_m": dist,
        "moving_s": float(moving.sum()),
        "holding_s": float((~moving).sum()),
        "avg_speed_kt": dist / duration / KT if duration else 0.0,
    }


def planned_metrics(ap, request, path):
    # A scheduled departure is done when it leaves the line-up point (its roll
    # starts); anything else when it reaches its goal.
    end = path[-1][1] if request.hold_until is not None else path[-1][1] - request.goal_hold
    moves = [(u, v, t0, t1) for (u, t0), (v, t1) in zip(path, path[1:]) if u != v and t1 <= end]
    moving = sum(t1 - t0 for _, _, t0, t1 in moves) * DT
    duration = (end - request.ready_t) * DT
    dist = sum(ap.graph[u][v] for u, v, _, _ in moves)
    return {
        "time_s": float(duration),
        "dist_m": dist,
        "moving_s": float(moving),
        "holding_s": float(duration - moving),
        "avg_speed_kt": dist / duration / KT if duration else 0.0,
    }


# ---------------------------------------------------------------- drawing

def draw_airport(ax, ap, runways):
    """Taxiways and runways; returns {pair: runway line} so busy runways can be highlighted."""
    ax.set_facecolor(SURFACE)
    drawn = set()
    for a, nbrs in ap.graph.items():
        for b in nbrs:
            if (b, a) in drawn:
                continue
            drawn.add((a, b))
            pts = edge_points(ap.nodes, ap.geometry, a, b)
            ax.plot([p[1] for p in pts], [p[0] for p in pts], color=TAXIWAY, lw=0.8, zorder=1)
    lines = {}
    for rw in runways:
        (la0, lo0), (la1, lo1) = rw.threshold, rw.far_end
        lines[rw.pair], = ax.plot([lo0, lo1], [la0, la1], color=RUNWAY, lw=5, solid_capstyle="butt", zorder=0)
        ax.annotate(rw.name, (lo0, la0), xytext=(-18, -12), textcoords="offset points",
                    fontsize=9, weight="bold", color=INK_2)
    mean_lat = sum(n.lat for n in ap.nodes.values()) / len(ap.nodes)
    ax.set_aspect(1 / math.cos(math.radians(mean_lat)))
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color(GRID)
    return lines


def animate(ap, flights, requests, result, gate, t0, minutes, stride, out):
    runways = load_runways()
    strips = [runways["4L"], runways["4R"]]  # the recording is in north-east flow
    by_id = {r.id: r for r in requests}
    real_tracks = {
        f["id"]: (f["rows"]["UnixTime"].to_numpy() - t0, f["rows"]["Lat"].to_numpy(),
                  f["rows"]["Lon"].to_numpy(), f["rows"]["Speed"].to_numpy(), f["kind"])
        for f in flights if f["id"] in result.paths
    }

    fig, (ax_real, ax_plan) = plt.subplots(1, 2, figsize=(15, 8.6), facecolor=SURFACE)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.88, bottom=0.08, wspace=0.03)
    runway_lines = [draw_airport(ax, ap, strips) for ax in (ax_real, ax_plan)]
    ax_real.set_title("Real — ADS-B positions", fontsize=13, weight="bold", color=REAL, loc="left")
    ax_plan.set_title("MAPF — prioritized space-time A*", fontsize=13, weight="bold", color=MAPF, loc="left")
    clock = fig.text(0.5, 0.95, "", ha="center", fontsize=14, weight="bold", color=INK)
    fig.text(0.5, 0.915, "● departure   ■ arrival   ring = holding / stopped   "
             "dark runway = real landing or takeoff in progress (both sides)", ha="center",
             fontsize=10, color=INK_2)

    def layer(ax, colour):
        return {
            kind: ax.scatter([], [], s=34, marker=marker, color=colour, edgecolors="white",
                             linewidths=0.6, zorder=4)
            for kind, marker in (("departure", "o"), ("arrival", "s"))
        }, ax.scatter([], [], s=150, facecolors="none", edgecolors=INK, linewidths=1.0, zorder=3)

    real_dots, real_rings = layer(ax_real, REAL)
    plan_dots, plan_rings = layer(ax_plan, MAPF)
    real_text = ax_real.text(0.01, -0.06, "", transform=ax_real.transAxes, fontsize=11, color=INK)
    plan_text = ax_plan.text(0.01, -0.06, "", transform=ax_plan.transAxes, fontsize=11, color=INK)
    kinds = {f["id"]: f["kind"] for f in flights}
    cumulative = {"real": 0.0, "plan": 0.0}

    def update(dots, rings, positions):
        for kind, artist in dots.items():
            pts = [(lon, lat) for k, lon, lat, _ in positions if k == kind]
            artist.set_offsets(pts or np.empty((0, 2)))
        held = [(lon, lat) for _, lon, lat, holding in positions if holding]
        rings.set_offsets(held or np.empty((0, 2)))

    def frame(t):
        real = []
        for tt, lat, lon, speed, kind in real_tracks.values():
            if tt[0] <= t <= tt[-1]:
                i = min(np.searchsorted(tt, t), len(tt) - 1)
                real.append((kind, np.interp(t, tt, lon), np.interp(t, tt, lat), speed[i] <= STOPPED_SPEED))
        plan = []
        for aid, path in result.paths.items():
            r = by_id[aid]
            done = path[-1][1] if r.hold_until is not None else path[-1][1] - r.goal_hold
            if t / DT > done:
                continue
            pos = position_at(ap, path, t / DT, parks=False)
            if pos:
                plan.append((kinds[aid], pos[1], pos[0], pos[2]))
        for lines in runway_lines:
            for pair, line in lines.items():
                line.set_color(INK if gate.busy(pair, t / DT, t / DT + 0.01) else RUNWAY)
        update(real_dots, real_rings, real)
        update(plan_dots, plan_rings, plan)
        cumulative["real"] += len(real) * stride
        cumulative["plan"] += len(plan) * stride
        clock.set_text(f"KEWR  t = {int(t) // 3600}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}")
        for text, positions, key in ((real_text, real, "real"), (plan_text, plan, "plan")):
            holding = sum(1 for p in positions if p[3])
            text.set_text(f"taxiing {len(positions):>2}   holding {holding:>2}   "
                          f"aircraft-minutes taxiing so far {cumulative[key] / 60:,.0f}")
        return []

    frames = list(np.arange(0, minutes * 60, stride))
    anim = FuncAnimation(fig, frame, frames=frames, blit=False)
    anim.save(out, writer=PillowWriter(fps=12), dpi=72)
    plt.close(fig)
    return out


def stats_figure(rows, n_conflicts, n_incursions, out):
    real_t = [r["real_time_s"] / 60 for r in rows]
    plan_t = [r["mapf_time_s"] / 60 for r in rows]

    fig = plt.figure(figsize=(14, 10), facecolor=SURFACE)
    grid = fig.add_gridspec(3, 2, height_ratios=[0.32, 1, 1], hspace=0.45, wspace=0.22,
                            left=0.07, right=0.97, top=0.95, bottom=0.07)

    # Headline numbers.
    head = fig.add_subplot(grid[0, :])
    head.axis("off")
    tiles = [
        ("aircraft compared", f"{len(rows)}", INK),
        ("median taxi time, real", f"{statistics.median(real_t):.1f} min", REAL),
        ("median taxi time, MAPF", f"{statistics.median(plan_t):.1f} min", MAPF),
        ("total time holding, real", f"{sum(r['real_holding_s'] for r in rows) / 3600:.1f} h", REAL),
        ("total time holding, MAPF", f"{sum(r['mapf_holding_s'] for r in rows) / 3600:.1f} h", MAPF),
        ("conflicts / runway incursions", f"{n_conflicts} / {n_incursions}", INK),
    ]
    for i, (label, value, colour) in enumerate(tiles):
        x = i / len(tiles)
        head.text(x, 0.55, value, fontsize=22, weight="bold", color=INK, transform=head.transAxes)
        head.plot([x, x + 0.03], [0.32, 0.32], color=colour, lw=3, transform=head.transAxes,
                  solid_capstyle="round")
        head.text(x, 0.05, label, fontsize=10, color=INK_2, transform=head.transAxes)

    def style(ax, title, xlabel, ylabel):
        ax.set_facecolor(SURFACE)
        ax.set_title(title, fontsize=12, weight="bold", color=INK, loc="left")
        ax.set_xlabel(xlabel, color=INK_2)
        ax.set_ylabel(ylabel, color=INK_2)
        ax.grid(color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(MUTED)
        ax.tick_params(colors=INK_2)

    # 1. Taxi time per aircraft.
    ax = fig.add_subplot(grid[1, 0])
    for kind, marker in (("departure", "o"), ("arrival", "s")):
        xs = [r["real_time_s"] / 60 for r in rows if r["kind"] == kind]
        ys = [r["mapf_time_s"] / 60 for r in rows if r["kind"] == kind]
        ax.scatter(xs, ys, s=40, marker=marker, color=INK_2, edgecolors=SURFACE, linewidths=1.5,
                   label=f"{kind}s ({len(xs)})", zorder=3)
    top = max(real_t + plan_t) * 1.05
    ax.plot([0, top], [0, top], color=MUTED, lw=1.2, ls="--", zorder=2)
    ax.text(top * 0.62, top * 0.66, "same time", color=MUTED, fontsize=9, rotation=38)
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.legend(frameon=False, loc="upper left")
    style(ax, "Taxi time per aircraft", "real (min)", "MAPF (min)")

    # 2. Average taxi speed.
    ax = fig.add_subplot(grid[1, 1])
    bins = np.arange(0, 22, 1)
    for key, colour, name in (("real_speed_kt", REAL, "Real"), ("mapf_speed_kt", MAPF, "MAPF")):
        values = [r[key] for r in rows]
        ax.hist(values, bins=bins, histtype="step", lw=2, color=colour, label=name)
        med = statistics.median(values)
        ax.axvline(med, color=colour, lw=1, ls=":")
        ax.text(med + 0.2, ax.get_ylim()[1] * 0.92 if name == "MAPF" else ax.get_ylim()[1] * 0.80,
                f"{name} median {med:.1f} kt", color=INK, fontsize=9)
    ax.legend(frameon=False, loc="upper left")
    style(ax, "Average taxi speed (distance ÷ taxi time)", "knots", "aircraft")

    # 3. Where the time goes.
    ax = fig.add_subplot(grid[2, 0])
    groups = ["moving", "holding / stopped"]
    real_v = [sum(r["real_moving_s"] for r in rows) / 3600, sum(r["real_holding_s"] for r in rows) / 3600]
    plan_v = [sum(r["mapf_moving_s"] for r in rows) / 3600, sum(r["mapf_holding_s"] for r in rows) / 3600]
    x = np.arange(len(groups))
    width = 0.36
    for offset, values, colour, name in ((-width / 2 - 0.01, real_v, REAL, "Real"),
                                         (width / 2 + 0.01, plan_v, MAPF, "MAPF")):
        bars = ax.bar(x + offset, values, width, color=colour, label=name, zorder=3)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.15, f"{value:.1f} h",
                    ha="center", fontsize=10, color=INK)
    ax.set_xticks(x, groups)
    ax.legend(frameon=False)
    style(ax, "Total aircraft-hours taxiing", "", "hours")

    # 4. Distance.
    ax = fig.add_subplot(grid[2, 1])
    xs = [r["real_dist_m"] / 1000 for r in rows]
    ys = [r["mapf_dist_m"] / 1000 for r in rows]
    ax.scatter(xs, ys, s=40, color=INK_2, edgecolors=SURFACE, linewidths=1.5, zorder=3)
    top = max(xs + ys) * 1.05
    ax.plot([0, top], [0, top], color=MUTED, lw=1.2, ls="--", zorder=2)
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    style(ax, "Taxi distance per aircraft", "real GPS track (km)", "MAPF route (km)")

    fig.text(0.07, 0.012,
             "Both sides use the real runway schedule: MAPF departures hold for their real takeoff slot and "
             "nobody enters a runway during a real landing or takeoff. MAPF taxis at a constant 8 m/s.",
             fontsize=9, color=INK_2)
    fig.savefig(out, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    return out


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=int, default=120, help="how much of the recording to animate")
    parser.add_argument("--stride", type=int, default=10, help="seconds per GIF frame")
    parser.add_argument("--no-gif", action="store_true")
    args = parser.parse_args(argv)

    ap = get_airport()
    df = read_traffic()
    t0 = int(df["UnixTime"].min())
    uses = real_runway_uses(df, t0)
    flights = load_flights(df, uses)
    gate = build_gate(uses)
    departs_from = {f["id"]: f["pair"] for f in flights if f["kind"] == "departure"}
    requests = make_requests(flights, t0)

    started = time.perf_counter()
    result = plan_batch(
        ap, requests, order=slot_order,
        edge_allowed_for=lambda r: gate.for_aircraft(r.id, departs_from.get(r.id)),
        max_delay_retries=10,  # aircraft that appear inside a queue wait to join it
        hold_starts=2,         # everyone's starting spot is theirs when they appear
    )
    plan_ms = (time.perf_counter() - started) * 1000
    conflicts = check_paths(result.paths, park_at_goal=False)
    incursions = runway_conflicts(gate, result.paths, departs_from)

    # Previous run for reference: no runway schedule, departures hold 60 s at line-up.
    free_requests = make_requests(flights, t0, with_schedule=False)
    free = plan_batch(ap, free_requests)
    free_by_id = {r.id: r for r in free_requests}

    by_id = {r.id: r for r in requests}
    rows = []
    missed_slots = []
    for f in flights:
        if f["id"] not in result.paths:
            continue
        r, path = by_id[f["id"]], result.paths[f["id"]]
        if r.hold_until is not None and path[-1][1] > r.hold_until:
            missed_slots.append((f["id"], (path[-1][1] - r.hold_until) * DT))
        real = real_metrics(f["rows"])
        plan = planned_metrics(ap, r, path)
        free_time = None
        if f["id"] in free.paths:
            fr = free_by_id[f["id"]]
            free_time = round((free.paths[f["id"]][-1][1] - fr.goal_hold - fr.ready_t) * DT, 1)
        rows.append({
            "id": f["id"], "kind": f["kind"],
            "start": ap.label(f["start"]), "goal": ap.label(f["goal"]),
            **{f"real_{k.replace('avg_speed_kt', 'speed_kt')}": round(v, 1) for k, v in real.items()},
            **{f"mapf_{k.replace('avg_speed_kt', 'speed_kt')}": round(v, 1) for k, v in plan.items()},
            "mapf_no_schedule_time_s": free_time,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "real_vs_mapf.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def total(key):
        return sum(r[key] for r in rows)

    deps = sum(1 for f in flights if f["kind"] == "departure")
    print(f"{len(flights)} aircraft with a complete taxi ({deps} departures, {len(flights) - deps} arrivals)")
    print(f"{len(uses)} real runway uses: "
          f"{sum(u.kind == 'takeoff' for u in uses)} takeoffs, {sum(u.kind == 'landing' for u in uses)} landings")
    print(f"MAPF planned {len(result.paths)}/{len(requests)} in {plan_ms:.0f} ms, "
          f"{len(conflicts)} conflicts, {len(incursions)} runway incursions; failures: {len(result.failures)}")
    late = sorted(d for _, d in missed_slots)
    print(f"departures reaching the runway after their real slot: {len(late)}"
          + (f" (median {statistics.median(late):.0f} s late, worst {late[-1]:.0f} s)" if late else ""))
    for aid, reason in result.failures.items():
        print(f"  FAILED {aid}: {reason}")
    free_t = [r["mapf_no_schedule_time_s"] for r in rows if r["mapf_no_schedule_time_s"] is not None]
    print(f"\n(MAPF without the runway schedule, for reference: median {statistics.median(free_t) / 60:.1f} min, "
          f"total {sum(free_t) / 3600:.1f} h)")
    print(f"\n{'':24}{'real':>10}{'MAPF':>10}")
    print(f"{'median taxi time (min)':24}{statistics.median(r['real_time_s'] for r in rows) / 60:>10.1f}"
          f"{statistics.median(r['mapf_time_s'] for r in rows) / 60:>10.1f}")
    print(f"{'total taxi time (h)':24}{total('real_time_s') / 3600:>10.1f}{total('mapf_time_s') / 3600:>10.1f}")
    print(f"{'  of which moving (h)':24}{total('real_moving_s') / 3600:>10.1f}{total('mapf_moving_s') / 3600:>10.1f}")
    print(f"{'  of which holding (h)':24}{total('real_holding_s') / 3600:>10.1f}{total('mapf_holding_s') / 3600:>10.1f}")
    print(f"{'total distance (km)':24}{total('real_dist_m') / 1000:>10.1f}{total('mapf_dist_m') / 1000:>10.1f}")
    print(f"{'median avg speed (kt)':24}{statistics.median(r['real_speed_kt'] for r in rows):>10.1f}"
          f"{statistics.median(r['mapf_speed_kt'] for r in rows):>10.1f}")
    moving_real = total("real_dist_m") / total("real_moving_s") / KT
    moving_plan = total("mapf_dist_m") / total("mapf_moving_s") / KT
    print(f"{'speed while moving (kt)':24}{moving_real:>10.1f}{moving_plan:>10.1f}")

    for kind in ("departure", "arrival"):
        sub = [r for r in rows if r["kind"] == kind]
        print(f"{kind + 's: median (min)':24}{statistics.median(r['real_time_s'] for r in sub) / 60:>10.1f}"
              f"{statistics.median(r['mapf_time_s'] for r in sub) / 60:>10.1f}")

    print(f"\nSaved {stats_figure(rows, len(conflicts), len(incursions), OUT_DIR / 'real_vs_mapf_stats.png')}")
    print(f"Saved {OUT_DIR / 'real_vs_mapf.csv'}")
    if not args.no_gif:
        started = time.perf_counter()
        out = animate(ap, flights, requests, result, gate, t0, args.minutes, args.stride,
                      OUT_DIR / "real_vs_mapf.gif")
        print(f"Saved {out} ({time.perf_counter() - started:.0f} s to render)")


if __name__ == "__main__":
    main()
