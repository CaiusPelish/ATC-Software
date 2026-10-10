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
import json
import math
import statistics
import time
from dataclasses import replace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

from ..routing.airport import DT, get_airport  # noqa: E402
from ..routing.mapf import AircraftRequest, plan_batch  # noqa: E402
from ..routing.ordering import free_flow_steps  # noqa: E402
from ..routing.osm_taxiways import edge_points, haversine_m  # noqa: E402
from ..routing.reservations import ReservationTable, check_paths  # noqa: E402
from ..routing.runways import RunwayGate, _to_xy, load_runways, runway_conflicts  # noqa: E402
from ..routing.visualize_plan import OUT_DIR, position_at  # noqa: E402
from .runway_schedule import real_runway_uses  # noqa: E402
from .traffic import (  # noqa: E402
    MIN_TAXI_SECONDS,
    STOPPED_SPEED,
    NotEnoughHistory,
    measured_taxi_speed,
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
# Spacing, as in RealVsMapfTest's realistic mode: distance kept behind the
# aircraft ahead on the same taxiway, and clearance around every node.
FOLLOW_M = 150
NODE_CLEAR_M = 60
# Gate hold: a departure released this many steps before it must leave to make
# its slot, and how many times a release that misses the slot is moved earlier.
GATE_HOLD_MARGIN = 2
GATE_HOLD_ROUNDS = 6
# Fuel burned taxiing: a two-engine narrowbody at ICAO idle (CFM56 ~0.1 kg/s per engine).
TAXI_FUEL_KG_PER_MIN = 12
CO2_PER_KG_FUEL = 3.16
# GIF: how far out arrivals on final are drawn, their speed, and how close to a
# runway a stopped real aircraft counts as holding short for it.
FINAL_DRAW_M = 700
APPROACH_KT = 140
HOLD_SHORT_M = 120
# A departure this close (straight line) to its line-up point is in the takeoff queue.
QUEUE_M = 600
MAX_QUEUE_LABELS = 12

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
IN_USE = "#d03b3b"
CLEAR = "#2e8b57"
QUEUE = "#e09a00"


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


def build_gate(ap, uses):
    """RunwayGate closed for every real landing (2 nm final to vacated) and takeoff roll."""
    runways = load_runways()
    gate = RunwayGate([runways["4L"], runways["4R"]], airport=ap)
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


def arrivals_first(airport, requests):
    """Arrivals always have right of way: every arrival is planned before any departure.

    Within each group, in the order they appear. Departures (hold_until set)
    then route around the arrivals' paths and the runways they hold.
    """
    return [r.id for r in sorted(requests, key=lambda r: (r.hold_until is not None, r.ready_t, r.id))]


def planner(ap, gate, flights, node_buffer, follow_gap):
    """plan(requests) -> BatchResult with the real runway schedule, runway-in-use flags and realistic spacing.

    Each planned aircraft claims every runway it crosses, lines up on or rolls
    out along (RunwayGate.claim) for the steps it is there; anyone planned
    after it waits short of that runway until it is clear. The gate keeps the
    real landings and takeoffs; each call starts again from just those.
    """
    departs_from = {f["id"]: f["pair"] for f in flights if f["kind"] == "departure"}
    # Arrivals that start their taxi still on the runway they landed on.
    rolling_out = {f["id"]: next((p for p, nodes in gate.on.items() if f["start"] in nodes), None)
                   for f in flights if f["kind"] == "arrival"}
    real_windows = {pair: list(w) for pair, w in gate.windows.items()}

    def plan(requests):
        gate.windows = {pair: list(w) for pair, w in real_windows.items()}
        return plan_batch(
            ap, requests, ReservationTable(node_buffer, follow_gap),
            edge_allowed_for=lambda r: gate.for_aircraft(r.id, departs_from.get(r.id), rolling_out.get(r.id)),
            order=arrivals_first,
            max_delay_retries=10,  # aircraft that appear inside a queue wait to join it
            hold_starts=2,         # everyone's starting spot is theirs when they appear
            on_planned=lambda r, path: gate.claim(r.id, path, departs_from.get(r.id)),
        )
    return plan, departs_from


def reached_goal(request, path):
    """Step the path first reaches its goal and stays there."""
    k = len(path) - 1
    while k > 0 and path[k - 1][0] == request.goal:
        k -= 1
    return path[k][1]


def moving_steps(path):
    return sum(t1 - t0 for (u, t0), (v, t1) in zip(path, path[1:]) if u != v)


def gate_hold(plan, requests, base):
    """Hold each departure at the gate, engines off, and release it just in time for its real slot.

    Starts from the waiting each departure did in the base plan, replans
    everyone with the later releases, and halves the hold of any departure
    that would now miss its slot, until none does. Returns (BatchResult,
    shifted requests, {aid: steps held at the gate}).
    """
    by_id = {r.id: r for r in requests}
    held = {}
    for aid, path in base.paths.items():
        r = by_id[aid]
        if r.hold_until is not None and reached_goal(r, path) <= r.hold_until:
            spare = r.hold_until - LINE_UP_STEPS - r.ready_t - moving_steps(path)
            held[aid] = max(0, spare - GATE_HOLD_MARGIN)

    for attempt in range(GATE_HOLD_ROUNDS + 1):
        shifted = [replace(r, ready_t=r.ready_t + held.get(r.id, 0)) for r in requests]
        result = plan(shifted)
        late = [aid for aid, h in held.items() if h and (
            aid not in result.paths or reached_goal(by_id[aid], result.paths[aid]) > by_id[aid].hold_until)]
        if not late:
            return result, shifted, held
        for aid in late:
            held[aid] = 0 if attempt == GATE_HOLD_ROUNDS - 1 else held[aid] // 2
    return result, shifted, held


def busiest_window(flights, minutes, t0):
    """Start (s) of the `minutes`-long window with the most aircraft taxiing in the recording."""
    spans = [(int(f["rows"]["UnixTime"].iloc[0]) - t0, int(f["rows"]["UnixTime"].iloc[-1]) - t0)
             for f in flights]
    end = max(b for _, b in spans)
    best, best_s = -1, 0
    for start in range(0, max(1, end - minutes * 60), 60):
        stop = start + minutes * 60
        load = sum(max(0, min(b, stop) - max(a, start)) for a, b in spans)
        if load > best:
            best, best_s = load, start
    return best_s


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


def runway_wait(gate, path, step):
    """Runway pair the aircraft is waiting for at `step` (holding short or lined up), or None."""
    for k, ((u, t0), (v, t1)) in enumerate(zip(path, path[1:])):
        if not t0 <= step < t1:
            continue
        if u != v:
            return None
        lined_up = next((p for p, nodes in gate.on.items() if u in nodes), None)
        if lined_up:
            return lined_up
        nxt = next((n for n, _ in path[k + 1:] if n != u), None)
        touched = gate.runways_touched(u, nxt) if nxt else []
        return touched[0] if touched else None
    return None


def on_final(uses, runways, t, max_m=FINAL_DRAW_M):
    """[(lat, lon)] of real arrivals on final at t (seconds), on the extended centreline."""
    found = []
    for u in uses:
        if u.kind == "landing" and u.busy_from <= t < u.roll_start:
            out_m = (u.roll_start - t) * APPROACH_KT * KT
            if out_m <= max_m:
                found.append(runways[u.pair.split("/")[0]].point(-out_m))
    return found


def animate(ap, flights, requests, result, gate, real_windows, uses, t0, minutes, stride, out, start_s=0):
    """Real ADS-B next to the plan, with each runway flagged IN USE or CLEAR on each side.

    Real side: the real landings and takeoffs. Plan side: those plus the
    runway time every planned aircraft claimed (crossings, line-ups, rollouts).
    Arrivals on final are drawn on both sides; a red ring is an aircraft
    waiting for a runway to clear. Departures near their line-up point are
    numbered in takeoff order (their real slot, the same on both sides); an
    amber ring is one waiting behind another departure.
    """
    runways = load_runways()
    strips = [runways["4L"], runways["4R"]]  # the recording is in north-east flow
    by_id = {r.id: r for r in requests}
    real_tracks = {
        f["id"]: (f["rows"]["UnixTime"].to_numpy() - t0, f["rows"]["Lat"].to_numpy(),
                  f["rows"]["Lon"].to_numpy(), f["rows"]["Speed"].to_numpy(), f["kind"])
        for f in flights if f["id"] in result.paths
    }
    real_gate = RunwayGate(strips, airport=ap)
    real_gate.windows = real_windows
    kinds = {f["id"]: f["kind"] for f in flights}
    # Takeoff queue: runway, slot and line-up point (x, y) of every departure.
    lineup = {f["id"]: (f["pair"], f["slot_s"], ap.xy[f["goal"]]) for f in flights if f["kind"] == "departure"}

    fig, (ax_real, ax_plan) = plt.subplots(1, 2, figsize=(12, 9), facecolor=SURFACE)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.87, bottom=0.07, wspace=0.03)
    panels = []
    for ax, side_gate, colour, title in ((ax_real, real_gate, REAL, "Real — ADS-B positions"),
                                         (ax_plan, gate, MAPF, "Hold Short — arrivals first, runway-in-use flags")):
        lines = draw_airport(ax, ap, strips)
        # Room for arrivals on final, south-west of the 4L/4R thresholds.
        for rw in strips:
            lat, lon = rw.point(-FINAL_DRAW_M)
            ax.update_datalim([(lon, lat)])
        ax.autoscale_view()
        ax.set_title(title, fontsize=13, weight="bold", color=colour, loc="left")
        dots = {
            kind: ax.scatter([], [], s=34, marker=marker, color=colour, edgecolors="white",
                             linewidths=0.6, zorder=5)
            for kind, marker in (("departure", "o"), ("arrival", "s"))
        }
        panels.append({
            "gate": side_gate, "lines": lines, "dots": dots,
            "rings": ax.scatter([], [], s=150, facecolors="none", edgecolors=INK, linewidths=1.0, zorder=4),
            "waits": ax.scatter([], [], s=190, facecolors="none", edgecolors=IN_USE, linewidths=1.8, zorder=4),
            "final": ax.scatter([], [], s=70, marker=">", color=IN_USE, edgecolors="white", linewidths=0.6,
                                zorder=6),
            "flags": [ax.text(0.015, 0.985 - 0.04 * k, "", transform=ax.transAxes, fontsize=10,
                              weight="bold", va="top",
                              bbox=dict(boxstyle="round,pad=0.3", fc=SURFACE, ec=GRID))
                      for k in range(len(strips))],
            "queued": ax.scatter([], [], s=190, facecolors="none", edgecolors=QUEUE, linewidths=1.8, zorder=4),
            "queue_labels": [ax.annotate("", (0, 0), xytext=(-14, 0), textcoords="offset points", ha="right",
                                         va="center", fontsize=9, weight="bold", color=INK, zorder=7,
                                         bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=QUEUE, lw=1),
                                         visible=False) for _ in range(MAX_QUEUE_LABELS)],
            "status": ax.text(0.01, -0.05, "", transform=ax.transAxes, fontsize=10, color=INK),
            "cumulative": 0.0,
        })

    clock = fig.text(0.5, 0.965, "", ha="center", fontsize=14, weight="bold", color=INK)
    fig.text(0.5, 0.905, "● departure   ■ arrival   ▶ arrival on final   red runway = in use   "
             "red ring = waiting for a runway\n#n = takeoff order   amber ring = waiting behind another "
             "departure   dark ring = other stop",
             ha="center", fontsize=10, color=INK_2)

    def offsets(pts):
        return pts or np.empty((0, 2))

    def takeoff_queues(positions, t):
        """{pair: [(aid, lon, lat)]} of departures near their line-up point, in takeoff order."""
        queues = {}
        for aid, kind, lon, lat, _, _ in positions:
            if aid not in lineup:
                continue
            pair, slot, (gx, gy) = lineup[aid]
            if slot <= t:
                continue
            x, y = _to_xy(ap, lat, lon)
            if math.hypot(x - gx, y - gy) <= QUEUE_M:
                queues.setdefault(pair, []).append((slot, aid, lon, lat))
        return {pair: [(aid, lon, lat) for _, aid, lon, lat in sorted(q)] for pair, q in queues.items()}

    def draw(panel, positions, t):
        step = t / DT
        queues = takeoff_queues(positions, t)
        behind = {aid for q in queues.values() for aid, _, _ in q[1:]}
        for kind, artist in panel["dots"].items():
            artist.set_offsets(offsets([(lon, lat) for _, k, lon, lat, _, _ in positions if k == kind]))
        panel["rings"].set_offsets(offsets([(lon, lat) for aid, _, lon, lat, stop, wait in positions
                                            if stop and not wait and aid not in behind]))
        panel["waits"].set_offsets(offsets([(lon, lat) for aid, _, lon, lat, _, wait in positions
                                            if wait and aid not in behind]))
        panel["queued"].set_offsets(offsets([(lon, lat) for aid, _, lon, lat, _, _ in positions if aid in behind]))
        panel["final"].set_offsets(offsets([(lon, lat) for lat, lon in on_final(uses, runways, t)]))
        labels = iter(panel["queue_labels"])
        for q in queues.values():
            for n, (_, lon, lat) in enumerate(q, 1):
                label = next(labels, None)
                if label:
                    label.xy = (lon, lat)
                    label.set_text(f"#{n}")
                    label.set_visible(True)
        for label in labels:
            label.set_visible(False)
        for flag, (pair, line) in zip(panel["flags"], panel["lines"].items()):
            use = panel["gate"].in_use(pair, step)
            line.set_color(IN_USE if use else RUNWAY)
            queued = len(queues.get(pair, []))
            tail = f" · {queued} in takeoff queue" if queued else ""
            if use:
                label, kind = use
                flag.set_text(f"{pair}  IN USE · {kind} {label}{tail}")
                flag.set_color(IN_USE)
            else:
                flag.set_text(f"{pair}  CLEAR{tail}")
                flag.set_color(CLEAR)
        panel["cumulative"] += len(positions) * stride
        waiting = sum(1 for p in positions if p[5])
        panel["status"].set_text(f"taxiing {len(positions):>2} · queued behind {len(behind):>2} · "
                                 f"runway waits {waiting:>2} · engine-min {panel['cumulative'] / 60:,.0f}")

    def frame(t):
        real = []
        for aid, (tt, lat, lon, speed, kind) in real_tracks.items():
            if tt[0] <= t <= tt[-1]:
                i = min(np.searchsorted(tt, t), len(tt) - 1)
                x, y = _to_xy(ap, np.interp(t, tt, lat), np.interp(t, tt, lon))
                stopped = speed[i] <= STOPPED_SPEED
                # Stopped beside a runway that is in use: holding short for it.
                wait = stopped and any(
                    real_gate.in_use(p, t / DT) and rw.on_runway_xy(x, y, margin=HOLD_SHORT_M)
                    for p, rw in real_gate.runways.items())
                real.append((aid, kind, np.interp(t, tt, lon), np.interp(t, tt, lat), stopped, wait))
        plan = []
        for aid, path in result.paths.items():
            r = by_id[aid]
            done = path[-1][1] if r.hold_until is not None else path[-1][1] - r.goal_hold
            if t / DT > done:
                continue
            pos = position_at(ap, path, t / DT, parks=False)
            if pos:
                plan.append((aid, kinds[aid], pos[1], pos[0], pos[2],
                             runway_wait(gate, path, t / DT) is not None))
        draw(panels[0], real, t)
        draw(panels[1], plan, t)
        clock.set_text(f"KEWR  t = {int(t) // 3600}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}")
        return []

    frames = list(np.arange(start_s, start_s + minutes * 60, stride))
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
             "nobody enters a runway during a real landing or takeoff. MAPF taxis at the real average moving speed.",
             fontsize=9, color=INK_2)
    fig.savefig(out, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    return out


def pitch_figure(rows, summary, out):
    """One 16:9 slide: headline tiles, per-aircraft delay, departure engine time."""
    fig = plt.figure(figsize=(16, 9), facecolor=SURFACE)
    grid = fig.add_gridspec(2, 2, height_ratios=[0.3, 1], width_ratios=[1.5, 1], hspace=0.3, wspace=0.18,
                            left=0.05, right=0.97, top=0.93, bottom=0.11)
    arr, dep = summary["arrivals"], summary["departures"]
    fig.text(0.05, 0.955, f"KEWR, {summary['aircraft']} real aircraft, same runway schedule, "
             f"same taxi speed ({summary['speed_kt']:.1f} kt)", fontsize=14, weight="bold", color=INK)

    head = fig.add_subplot(grid[0, :])
    head.axis("off")
    tiles = [
        (f"{arr['real_delay_min']:.0f} → {arr['mapf_delay_min']:.0f} min", "arrival delay, total", MAPF),
        (f"{dep['real_engine_min']:,.0f} → {dep['mapf_engine_min']:,.0f} min",
         "departure engine-running taxi time", MAPF),
        (f"{summary['fuel_kg'] / 1000:.1f} t", f"fuel saved (≈{summary['co2_t']:.1f} t CO₂) in 2 h", MAPF),
        (f"{summary['conflicts']} / {summary['incursions']}", "conflicts / runway incursions", INK),
    ]
    for k, (value, label, colour) in enumerate(tiles):
        x = k / len(tiles)
        head.text(x, 0.55, value, fontsize=24, weight="bold", color=INK, transform=head.transAxes)
        head.plot([x, x + 0.03], [0.35, 0.35], color=colour, lw=3, transform=head.transAxes,
                  solid_capstyle="round")
        head.text(x, 0.05, label, fontsize=11, color=INK_2, transform=head.transAxes)

    def style(ax, title, xlabel, ylabel):
        ax.set_facecolor(SURFACE)
        ax.set_title(title, fontsize=13, weight="bold", color=INK, loc="left")
        ax.set_xlabel(xlabel, color=INK_2)
        ax.set_ylabel(ylabel, color=INK_2)
        ax.grid(color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(MUTED)
        ax.tick_params(colors=INK_2)

    # Delay per aircraft over the unimpeded taxi, sorted by the real delay.
    ax = fig.add_subplot(grid[1, 0])
    ordered = sorted(rows, key=lambda r: r["real_delay_s"])
    x = np.arange(len(ordered))
    ax.vlines(x, [r["mapf_engine_delay_s"] / 60 for r in ordered], [r["real_delay_s"] / 60 for r in ordered],
              color=GRID, lw=1.2, zorder=2)
    ax.scatter(x, [r["real_delay_s"] / 60 for r in ordered], s=16, color=REAL, label="Real", zorder=3)
    ax.scatter(x, [r["mapf_engine_delay_s"] / 60 for r in ordered], s=16, color=MAPF,
               label="Hold Short (engines on)", zorder=3)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks([])
    ax.legend(frameon=False, loc="upper left")
    style(ax, "Delay per aircraft, beyond an unimpeded taxi", "aircraft, sorted by real delay", "minutes")

    # Where departures spend the wait for their slot.
    ax = fig.add_subplot(grid[1, 1])
    real_h = dep["real_engine_min"] / 60
    plan_h = dep["mapf_engine_min"] / 60
    held_h = dep["gate_hold_min"] / 60
    ax.bar([0], [real_h], 0.55, color=REAL, zorder=3)
    ax.bar([1], [plan_h], 0.55, color=MAPF, zorder=3, label="engines on, taxiing or queued")
    ax.bar([1], [held_h], 0.55, bottom=[plan_h], color="none", edgecolor=MAPF, hatch="//", zorder=3,
           label="held at gate, engines off")
    ax.text(0, real_h + 0.3, f"{real_h:.1f} h", ha="center", fontsize=11, color=INK)
    ax.text(1, plan_h / 2, f"{plan_h:.1f} h", ha="center", fontsize=11, color=SURFACE, weight="bold")
    ax.text(1, plan_h + held_h + 0.3, f"+{held_h:.1f} h at gate", ha="center", fontsize=11, color=INK)
    ax.set_xticks([0, 1], ["Real", "Hold Short"])
    ax.set_ylim(0, (max(real_h, plan_h + held_h)) * 1.15)
    ax.legend(frameon=False, loc="upper right", fontsize=9)
    style(ax, f"{dep['n']} departures, same takeoff slots", "", "aircraft-hours")

    fig.text(0.05, 0.03,
             "Real = ADS-B (Amelia42, KEWR). Hold Short = prioritized space-time A* on the same start/end points and "
             "release times, planned in order of appearance, real landings and takeoffs closing the runways, "
             f"{FOLLOW_M} m following distance. Not modelled: slowing for turns, pushback, {DT} s time steps. "
             f"Fuel at {TAXI_FUEL_KG_PER_MIN} kg/min per aircraft (narrowbody idle).",
             fontsize=9, color=INK_2, wrap=True)
    fig.savefig(out, dpi=120, facecolor=SURFACE)
    plt.close(fig)
    return out


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=int, default=15, help="how much of the recording to animate")
    parser.add_argument("--start-minute", type=float, default=None,
                        help="where the GIF starts; default is the busiest window")
    parser.add_argument("--stride", type=int, default=5, help="seconds per GIF frame")
    parser.add_argument("--speed-kt", type=float, default=None,
                        help="planner taxi speed; default is the real average moving speed")
    parser.add_argument("--no-gate-hold", action="store_true", help="release departures when they really appeared")
    parser.add_argument("--no-gif", action="store_true")
    args = parser.parse_args(argv)

    df = read_traffic()
    t0 = int(df["UnixTime"].min())
    uses = real_runway_uses(df, t0)
    flights = load_flights(df, uses)

    speed = args.speed_kt * KT if args.speed_kt else measured_taxi_speed(flights)
    ap = get_airport(taxi_speed=round(speed, 2))
    step_m = ap.taxi_speed * DT
    node_buffer, follow_gap = math.ceil(NODE_CLEAR_M / step_m), math.ceil(FOLLOW_M / step_m)
    gate = build_gate(ap, uses)
    real_windows = {pair: list(w) for pair, w in gate.windows.items()}
    plan, departs_from = planner(ap, gate, flights, node_buffer, follow_gap)
    requests = make_requests(flights, t0)

    started = time.perf_counter()
    base = plan(requests)
    plan_ms = (time.perf_counter() - started) * 1000
    base_late = sum(1 for r in requests if r.hold_until is not None and r.id in base.paths
                    and reached_goal(r, base.paths[r.id]) > r.hold_until)
    print(f"planner: {ap.taxi_speed / KT:.1f} kt, follow gap {follow_gap * DT} s, node clearance {node_buffer * DT} s")
    print(f"base plan: {len(base.paths)}/{len(requests)} in {plan_ms:.0f} ms, {len(base.failures)} failed, "
          f"{base_late} departures late for their slot")

    if args.no_gate_hold:
        result, held_requests, held = base, requests, {}
    else:
        started = time.perf_counter()
        result, held_requests, held = gate_hold(plan, requests, base)
        print(f"gate hold: {len(result.paths)}/{len(requests)} in {(time.perf_counter() - started) * 1000:.0f} ms, "
              f"{len(result.failures)} failed")
    # gate.windows now holds the real schedule plus every runway claim of `result`.
    conflicts = check_paths(result.paths, park_at_goal=False, node_buffer=node_buffer, follow_gap=follow_gap)
    incursions = runway_conflicts(gate, result.paths, departs_from)
    claims = sum(len(w) for w in gate.windows.values()) - sum(len(w) for w in real_windows.values())
    print(f"{len(conflicts)} taxiway conflicts, {len({(a, p) for a, p, _ in incursions})} runway incursions, "
          f"{claims} runway-in-use claims by planned aircraft")
    for aid, reason in result.failures.items():
        print(f"  FAILED {aid}: {reason}")

    by_id = {r.id: r for r in requests}
    held_by_id = {r.id: r for r in held_requests}
    rows = []
    for f in flights:
        aid = f["id"]
        if aid not in result.paths or aid not in base.paths:
            continue
        r = by_id[aid]
        real = real_metrics(f["rows"])
        plan_m = planned_metrics(ap, r, base.paths[aid])
        engine = planned_metrics(ap, held_by_id[aid], result.paths[aid])
        unimpeded = free_flow_steps(ap, f["start"], f["goal"]) * DT
        rows.append({
            "id": aid, "kind": f["kind"],
            "start": ap.label(f["start"]), "goal": ap.label(f["goal"]),
            **{f"real_{k.replace('avg_speed_kt', 'speed_kt')}": round(v, 1) for k, v in real.items()},
            **{f"mapf_{k.replace('avg_speed_kt', 'speed_kt')}": round(v, 1) for k, v in plan_m.items()},
            "unimpeded_s": unimpeded,
            "real_delay_s": round(real["time_s"] - unimpeded, 1),
            "mapf_delay_s": round(plan_m["time_s"] - unimpeded, 1),
            "gate_hold_s": held.get(aid, 0) * DT,
            "mapf_engine_s": round(engine["time_s"], 1),
            "late_s": max(0, reached_goal(r, result.paths[aid]) - r.hold_until) * DT
            if r.hold_until is not None else 0,
        })

    pitch_dir = OUT_DIR / "pitch"
    pitch_dir.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "real_vs_mapf.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def minutes(subset, key):
        return round(sum(r[key] for r in subset) / 60, 1)

    arr = [r for r in rows if r["kind"] == "arrival"]
    dep = [r for r in rows if r["kind"] == "departure"]
    engine_saved = minutes(dep, "real_time_s") - minutes(dep, "mapf_engine_s")
    summary = {
        "aircraft": len(rows),
        "speed_kt": round(ap.taxi_speed / KT, 1),
        "conflicts": len(conflicts),
        "incursions": len({(a, p) for a, p, _ in incursions}),
        "runway_claims": claims,
        "failed": len(result.failures),
        "arrivals": {
            "n": len(arr),
            "real_taxi_min": minutes(arr, "real_time_s"), "mapf_taxi_min": minutes(arr, "mapf_time_s"),
            "real_holding_min": minutes(arr, "real_holding_s"), "mapf_holding_min": minutes(arr, "mapf_holding_s"),
        },
        "departures": {
            "n": len(dep),
            "real_engine_min": minutes(dep, "real_time_s"), "mapf_engine_min": minutes(dep, "mapf_engine_s"),
            "gate_hold_min": minutes(dep, "gate_hold_s"),
            "late_for_slot": sum(1 for r in dep if r["late_s"] > 0),
        },
        "fuel_saved_kg": round(max(0.0, engine_saved) * TAXI_FUEL_KG_PER_MIN),
    }
    (pitch_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    print(f"\nSaved {stats_figure(rows, len(conflicts), summary['incursions'], OUT_DIR / 'real_vs_mapf_stats.png')}")
    print(f"Saved {OUT_DIR / 'real_vs_mapf.csv'}, {pitch_dir / 'summary.json'}")
    if not args.no_gif:
        start_s = (args.start_minute * 60 if args.start_minute is not None
                   else busiest_window(flights, args.minutes, t0))
        started = time.perf_counter()
        out = animate(ap, flights, held_requests, result, gate, real_windows, uses, t0, args.minutes,
                      args.stride, pitch_dir / "compare.gif", start_s=start_s)
        print(f"Saved {out} (minute {start_s / 60:.0f}, {time.perf_counter() - started:.0f} s to render)")


if __name__ == "__main__":
    main()
