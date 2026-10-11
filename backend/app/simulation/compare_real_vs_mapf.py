# backend/app/simulation/compare_real_vs_mapf.py

"""Real taxi movements at KEWR next to the same aircraft routed by our MAPF planner.

Every aircraft in the 2-hour Amelia recording with a complete taxi is used:
departures from first appearing on the ground until their takeoff roll,
arrivals from vacating the runway until their last ground point. Each one's
first and last taxi points are snapped to the taxiway graph, and all of them
are planned together with prioritized space-time A*.

Only the spawn times are real: each aircraft is released when it really
started to taxi, and gets to its destination as soon as the planner can get
it there. A departure takes off when it reaches its line-up point and the
runway is free, not at its real takeoff time. Runway rules:
- a real landing holds its runway from 2 nm out until it has vacated;
- a takeoff roll holds its runway for TAKEOFF_ROLL_S; crossings and rollouts
  hold it while they are on it;
- takeoffs from the same runway start at least TAKEOFF_SPACING_S apart;
- arrivals always have right of way: they are planned before any departure.

Prints real vs MAPF tables to the terminal and writes
app/simulation/data/plans/real_vs_mapf.csv (one row per aircraft).
--gif also renders data/plans/pitch/compare.gif.

Not modelled on the MAPF side: pushback and gates, speed changes (constant
taxi speed), and ATC workload.

Run from backend/:
    python3 -m app.simulation.compare_real_vs_mapf [--speed-kt 9] [--gif]
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
import pandas as pd  # noqa: E402
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
ARRIVAL_HOLD = 20 // DT     # handed to ramp control
LINE_UP_STEPS = 1           # a departure's time on its line-up point before it rolls
# A takeoff roll keeps crossings and landings off its runway this long.
TAKEOFF_ROLL_S = 40
TAKEOFF_ROLL = math.ceil(TAKEOFF_ROLL_S / DT)
# Takeoffs from the same runway start at least this far apart.
TAKEOFF_SPACING_S = 35
TAKEOFF_SPACING = math.ceil(TAKEOFF_SPACING_S / DT)
# Spacing, as in RealVsMapfTest's realistic mode: distance kept behind the
# aircraft ahead on the same taxiway, and clearance around every node.
FOLLOW_M = 150
NODE_CLEAR_M = 60
# GIF: a departure this close (straight line) to its line-up point is in the takeoff queue.
QUEUE_M = 600

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


def build_gate(ap, uses, crossings_overlap=False):
    """RunwayGate closed for every real landing, from 2 nm final until it vacated.

    Real takeoffs are not in it: MAPF departures take off when they can.
    crossings_overlap: see RunwayGate.
    """
    runways = load_runways()
    gate = RunwayGate([runways["4L"], runways["4R"]], airport=ap, crossings_overlap=crossings_overlap)
    for u in uses:
        if u.kind == "landing":
            gate.add_window(u.pair, int(u.busy_from // DT), math.ceil(u.busy_until / DT), u.id, "landing")
    return gate


def make_requests(flights, t0):
    """Released at the real spawn time; a departure rolls one step after reaching its line-up point."""
    requests = []
    for f in flights:
        ready = (int(f["rows"]["UnixTime"].iloc[0]) - t0) // DT
        hold = LINE_UP_STEPS if f["kind"] == "departure" else ARRIVAL_HOLD
        requests.append(AircraftRequest(f["id"], f["start"], f["goal"], ready, goal_hold=hold))
    return requests


def planner(ap, gate, flights, node_buffer, follow_gap, progress=None, arrivals_first=True):
    """plan(requests) -> BatchResult under the runway rules in the module docstring.

    Arrivals are planned first, then departures, each group in the order they
    appear; arrivals_first=False plans everyone in the order they appear, so
    an arrival can find the runway it must cross taken by departures planned
    before it and queue at the hold-short line.
    progress(request, path) is called after each aircraft is planned. Every planned aircraft closes each runway it is on for that time;
    a departure's takeoff roll closes its runway for TAKEOFF_ROLL_S and may
    only start when nobody else holds the runway and the last takeoff from it
    started TAKEOFF_SPACING_S or more before.
    """
    departs_from = {f["id"]: f["pair"] for f in flights if f["kind"] == "departure"}
    # Arrivals that start their taxi still on the runway they landed on.
    rolling_out = {f["id"]: next((p for p, nodes in gate.on.items() if f["start"] in nodes), None)
                   for f in flights if f["kind"] == "arrival"}
    rolls = {}  # pair -> [step each planned takeoff roll starts]

    def order(airport, requests):
        return [r.id for r in sorted(requests, key=lambda r: (
            arrivals_first and r.id in departs_from, r.ready_t, r.id))]

    def takeoff_clear(r):
        pair = departs_from.get(r.id)
        if pair is None:
            return None

        def clear(t):
            roll = t + r.goal_hold
            if any(abs(roll - s) < TAKEOFF_SPACING for s in rolls.get(pair, ())):
                return False
            # Other departures lined up or rolling are spaced above; anything else must be off the runway.
            return not any(w0 < roll + TAKEOFF_ROLL and roll < w1
                           for w0, w1, label, kind in gate.windows[pair] if label != r.id and kind != "takeoff")
        return clear

    def claim(r, path):
        pair = departs_from.get(r.id)
        for p, t0, t1 in gate.occupancy(path):
            gate.add_window(p, t0, t1, r.id, "takeoff" if p == pair else "crossing")
        if pair:
            roll = path[-1][1]
            gate.add_window(pair, roll, roll + TAKEOFF_ROLL, r.id, "takeoff")
            rolls.setdefault(pair, []).append(roll)
        if progress:
            progress(r, path)

    def plan(requests):
        rolls.clear()
        return plan_batch(
            ap, requests, ReservationTable(node_buffer, follow_gap),
            edge_allowed_for=lambda r: gate.for_aircraft(r.id, departs_from.get(r.id), rolling_out.get(r.id)),
            order=order,
            max_delay_retries=10,  # aircraft that appear inside a queue wait to join it
            hold_starts=2,         # everyone's starting spot is theirs when they appear
            on_planned=claim,
            finish_allowed_for=takeoff_clear,
        )
    return plan, departs_from


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


def takeoff_step(request, path):
    """Step a departure reaches its line-up point; it rolls goal_hold steps later."""
    return path[-1][1] - request.goal_hold


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
    # A departure is done when its takeoff starts, an arrival when it reaches its goal.
    end = path[-1][1] - request.goal_hold
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


def delay_series(rows, finish, t_end):
    """(times s, real avg min, MAPF avg min): average delay of every aircraft finished by each minute.

    Delay is taxi time beyond an unimpeded taxi at the planner's speed; MAPF's
    is with engines running (a departure held at the gate isn't delayed yet).
    """
    times = np.arange(0, t_end + 60, 60)
    out = {}
    for side, key in (("real", "real_delay_s"), ("mapf", "mapf_engine_delay_s")):
        done = sorted((finish[side][r["id"]], r[key] / 60) for r in rows if r["id"] in finish[side])
        series, total, k = [], 0.0, 0
        for t in times:
            while k < len(done) and done[k][0] <= t:
                total += done[k][1]
                k += 1
            series.append(total / k if k else np.nan)
        out[side] = np.array(series)
    return times, out["real"], out["mapf"]


def animate(ap, flights, requests, result, gate, real_windows, rows, t0, minutes, stride, out, start_s=0):
    """Real ADS-B next to the plan, with a running average-delay chart underneath.

    Runways turn red while in use (real side: real landings and takeoffs;
    plan side: those plus the planned aircraft's crossings and line-ups).
    An amber ring is a departure waiting behind another for takeoff.
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
    lineup = {f["id"]: (f["pair"], f["slot_s"], ap.xy[f["goal"]]) for f in flights if f["kind"] == "departure"}

    def plan_done(aid):
        return takeoff_step(by_id[aid], result.paths[aid])

    finish = {"real": {aid: tr[0][-1] for aid, tr in real_tracks.items()},
              "mapf": {aid: plan_done(aid) * DT for aid in result.paths}}
    t_end = max(max(finish["real"].values()), max(finish["mapf"].values()))
    times, real_avg, plan_avg = delay_series(rows, finish, t_end)

    fig = plt.figure(figsize=(12, 10), facecolor=SURFACE)
    grid = fig.add_gridspec(2, 2, height_ratios=[2.6, 1], hspace=0.16, wspace=0.03,
                            left=0.06, right=0.98, top=0.9, bottom=0.07)
    ax_real, ax_plan = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])
    panels = []
    for ax, side_gate, colour, title in ((ax_real, real_gate, REAL, "Real (ADS-B)"),
                                         (ax_plan, gate, MAPF, "Hold Short")):
        lines = draw_airport(ax, ap, strips)
        ax.set_title(title, fontsize=14, weight="bold", color=colour, loc="left")
        dots = {
            kind: ax.scatter([], [], s=34, marker=marker, color=colour, edgecolors="white",
                             linewidths=0.6, zorder=5)
            for kind, marker in (("departure", "o"), ("arrival", "s"))
        }
        panels.append({
            "gate": side_gate, "lines": lines, "dots": dots,
            "rings": ax.scatter([], [], s=150, facecolors="none", edgecolors=INK, linewidths=1.0, zorder=4),
            "queued": ax.scatter([], [], s=170, facecolors="none", edgecolors=QUEUE, linewidths=1.8, zorder=4),
            "status": ax.text(0.02, 0.02, "", transform=ax.transAxes, fontsize=11, color=INK,
                              bbox=dict(boxstyle="round,pad=0.3", fc=SURFACE, ec=GRID)),
        })

    # Average delay over the whole recording, with a cursor at the clock.
    ax_d = fig.add_subplot(grid[1, :])
    ax_d.set_facecolor(SURFACE)
    ax_d.axvspan(start_s / 60, (start_s + minutes * 60) / 60, color=GRID, alpha=0.6, lw=0, zorder=0)
    ax_d.plot(times / 60, real_avg, color=REAL, lw=2.2, zorder=3)
    ax_d.plot(times / 60, plan_avg, color=MAPF, lw=2.2, zorder=3)
    for series, colour, name in ((real_avg, REAL, "Real"), (plan_avg, MAPF, "Hold Short")):
        ax_d.annotate(f"{name} {series[-1]:.1f} min", (times[-1] / 60, series[-1]), xytext=(6, 0),
                      textcoords="offset points", va="center", fontsize=10, weight="bold", color=colour)
    cursor = ax_d.axvline(start_s / 60, color=INK, lw=1.2, zorder=4)
    marks = [ax_d.scatter([], [], s=40, color=c, edgecolors="white", zorder=5) for c in (REAL, MAPF)]
    ax_d.set_title("Average delay per aircraft (engines running), minutes", fontsize=12, weight="bold",
                   color=INK, loc="left")
    ax_d.set_xlabel("minutes into the recording", color=INK_2)
    ax_d.set_xlim(0, times[-1] / 60 * 1.18)
    ax_d.set_ylim(0, np.nanmax(np.concatenate([real_avg, plan_avg])) * 1.15)
    ax_d.grid(color=GRID, lw=0.8)
    for side in ("top", "right"):
        ax_d.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax_d.spines[side].set_color(MUTED)
    ax_d.tick_params(colors=INK_2)

    clock = fig.text(0.5, 0.955, "", ha="center", fontsize=15, weight="bold", color=INK)
    fig.text(0.5, 0.925, "● departure   ■ arrival   ring = stopped   amber ring = waiting behind another "
             "for takeoff   red runway = in use", ha="center", fontsize=10, color=INK_2)

    def offsets(pts):
        return pts or np.empty((0, 2))

    def queued_behind(positions, t):
        """Departures near their line-up point with another, earlier slot ahead of them."""
        queues = {}
        for aid, _, lon, lat, _ in positions:
            if aid in lineup and lineup[aid][1] > t:
                pair, slot, (gx, gy) = lineup[aid]
                x, y = _to_xy(ap, lat, lon)
                if math.hypot(x - gx, y - gy) <= QUEUE_M:
                    queues.setdefault(pair, []).append((slot, aid))
        return {aid for q in queues.values() for _, aid in sorted(q)[1:]}

    def draw(panel, positions, t):
        behind = queued_behind(positions, t)
        for kind, artist in panel["dots"].items():
            artist.set_offsets(offsets([(lon, lat) for _, k, lon, lat, _ in positions if k == kind]))
        panel["rings"].set_offsets(offsets([(lon, lat) for aid, _, lon, lat, stop in positions
                                            if stop and aid not in behind]))
        panel["queued"].set_offsets(offsets([(lon, lat) for aid, _, lon, lat, _ in positions if aid in behind]))
        for pair, line in panel["lines"].items():
            line.set_color(IN_USE if panel["gate"].in_use(pair, t / DT) else RUNWAY)
        panel["status"].set_text(f"taxiing {len(positions):>2}   queued for takeoff {len(behind):>2}")

    def frame(t):
        real = []
        for aid, (tt, lat, lon, speed, kind) in real_tracks.items():
            if tt[0] <= t <= tt[-1]:
                i = min(np.searchsorted(tt, t), len(tt) - 1)
                real.append((aid, kind, np.interp(t, tt, lon), np.interp(t, tt, lat), speed[i] <= STOPPED_SPEED))
        plan = []
        for aid, path in result.paths.items():
            if t / DT > plan_done(aid):
                continue
            pos = position_at(ap, path, t / DT, parks=False)
            if pos:
                plan.append((aid, kinds[aid], pos[1], pos[0], pos[2]))
        draw(panels[0], real, t)
        draw(panels[1], plan, t)
        k = min(int(t // 60), len(times) - 1)
        cursor.set_xdata([t / 60, t / 60])
        for mark, series in zip(marks, (real_avg, plan_avg)):
            mark.set_offsets(offsets([(t / 60, series[k])] if not np.isnan(series[k]) else []))
        clock.set_text(f"KEWR  {int(t) // 3600}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}")
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


# ---------------------------------------------------------------- main

def position_node(path, t):
    """Node the path is at, or last left, at step t."""
    return next((n for n, step in reversed(path) if step <= t), path[0][0])


def takeoff_gaps(times):
    """Seconds between consecutive takeoffs on each runway, all runways together."""
    gaps = []
    for pair_times in times.values():
        t = sorted(pair_times)
        gaps += [b - a for a, b in zip(t, t[1:])]
    return gaps


def report(rows, real_takeoffs, plan_takeoffs, safety, settings):
    """Real vs MAPF tables for the terminal."""
    def avg(subset, key):
        return statistics.mean(r[key] for r in subset) / 60 if subset else float("nan")

    table = []
    for label, kind in (("Arrivals", "arrival"), ("Departures", "departure"), ("All aircraft", None)):
        sub = [r for r in rows if kind is None or r["kind"] == kind]
        group = f"{label} ({len(sub)})"
        table += [
            (group, "avg taxi time (min)", avg(sub, "real_time_s"), avg(sub, "mapf_time_s")),
            (group, "avg delay (min)", avg(sub, "real_delay_s"), avg(sub, "mapf_delay_s")),
            (group, "avg time stopped (min)", avg(sub, "real_holding_s"), avg(sub, "mapf_holding_s")),
        ]
        if kind is None:
            table.append((group, "total taxi time (h)", sum(r["real_time_s"] for r in sub) / 3600,
                          sum(r["mapf_time_s"] for r in sub) / 3600))
    real_gaps, plan_gaps = takeoff_gaps(real_takeoffs), takeoff_gaps(plan_takeoffs)
    last = (max(t for v in real_takeoffs.values() for t in v) / 60,
            max(t for v in plan_takeoffs.values() for t in v) / 60)
    table += [
        ("Runway", "median gap between takeoffs (s)", statistics.median(real_gaps), statistics.median(plan_gaps)),
        ("Runway", "shortest gap between takeoffs (s)", min(real_gaps), min(plan_gaps)),
        ("Runway", "last takeoff (min into recording)", *last),
    ]
    df = pd.DataFrame(table, columns=["", "metric", "Real", "Hold Short"]).set_index(["", "metric"])
    df["change"] = ((df["Hold Short"] - df["Real"]) / df["Real"] * 100).map(lambda v: f"{v:+.0f}%")

    pd.set_option("display.width", 120)
    print(f"\n=== KEWR, real ADS-B vs Hold Short MAPF: {len(rows)} aircraft, same spawn times ===")
    print(", ".join(f"{k} {v}" for k, v in settings.items()))
    print(df.to_string(float_format=lambda v: f"{v:,.1f}"))

    per = pd.DataFrame(rows)
    per["saved_min"] = (per["real_time_s"] - per["mapf_time_s"]) / 60
    cols = ["id", "kind", "start", "goal", "real_min", "mapf_min", "saved_min"]
    per["real_min"], per["mapf_min"] = per["real_time_s"] / 60, per["mapf_time_s"] / 60
    print("\nBiggest time savings:")
    print(per.nlargest(5, "saved_min")[cols].to_string(index=False, float_format=lambda v: f"{v:.1f}"))
    print("\nBiggest losses:")
    print(per.nsmallest(5, "saved_min")[cols].to_string(index=False, float_format=lambda v: f"{v:.1f}"))
    print("\nSafety: " + ", ".join(f"{k} {v}" for k, v in safety.items()))


def main(argv=None):
    global TAKEOFF_SPACING_S, TAKEOFF_SPACING
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--speed-kt", type=float, default=None,
                        help="planner taxi speed; default is the real average moving speed")
    parser.add_argument("--takeoff-spacing", type=int, default=TAKEOFF_SPACING_S,
                        help="minimum seconds between takeoffs from the same runway")
    parser.add_argument("--gif", action="store_true", help="also render the side-by-side GIF")
    parser.add_argument("--minutes", type=int, default=15, help="GIF length")
    parser.add_argument("--start-minute", type=float, default=None, help="GIF start; default the busiest window")
    parser.add_argument("--stride", type=int, default=5, help="seconds per GIF frame")
    args = parser.parse_args(argv)
    TAKEOFF_SPACING_S = args.takeoff_spacing
    TAKEOFF_SPACING = math.ceil(TAKEOFF_SPACING_S / DT)

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
    result = plan(requests)
    plan_s = time.perf_counter() - started

    # gate.windows now holds the real landings plus every runway use the plan claimed.
    conflicts = check_paths(result.paths, park_at_goal=False, node_buffer=node_buffer, follow_gap=follow_gap)
    incursions = runway_conflicts(gate, result.paths, departs_from)
    by_id = {r.id: r for r in requests}
    plan_takeoffs, real_takeoffs = {}, {}
    for f in flights:
        if f["kind"] == "departure" and f["id"] in result.paths:
            plan_takeoffs.setdefault(f["pair"], []).append(result.paths[f["id"]][-1][1] * DT)
            real_takeoffs.setdefault(f["pair"], []).append(f["slot_s"])
    # Takeoffs at least TAKEOFF_SPACING_S apart, and no roll during a real landing.
    separation = sum(1 for g in takeoff_gaps(plan_takeoffs) if g < TAKEOFF_SPACING_S) + sum(
        1 for pair, times in plan_takeoffs.items() for t in times
        for w0, w1, _, _ in real_windows[pair] if w0 * DT < t + TAKEOFF_ROLL_S and t < w1 * DT)

    rows = []
    for f in flights:
        aid = f["id"]
        if aid not in result.paths:
            continue
        real = real_metrics(f["rows"])
        planned = planned_metrics(ap, by_id[aid], result.paths[aid])
        unimpeded = free_flow_steps(ap, f["start"], f["goal"]) * DT
        rows.append({
            "id": aid, "kind": f["kind"],
            "start": ap.label(f["start"]), "goal": ap.label(f["goal"]),
            **{f"real_{k.replace('avg_speed_kt', 'speed_kt')}": round(v, 1) for k, v in real.items()},
            **{f"mapf_{k.replace('avg_speed_kt', 'speed_kt')}": round(v, 1) for k, v in planned.items()},
            "unimpeded_s": unimpeded,
            "real_delay_s": round(real["time_s"] - unimpeded, 1),
            "mapf_delay_s": round(planned["time_s"] - unimpeded, 1),
            "mapf_engine_delay_s": round(planned["time_s"] - unimpeded, 1),
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "real_vs_mapf.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report(rows, real_takeoffs, plan_takeoffs,
           safety={"taxiway conflicts": len(conflicts),
                   "runway incursions": len({(a, p) for a, p, _ in incursions}),
                   "runway separation violations": separation,
                   "not routed": len(result.failures)},
           settings={"taxi speed": f"{ap.taxi_speed / KT:.1f} kt",
                     "following distance": f"{FOLLOW_M} m",
                     "takeoff spacing": f"{TAKEOFF_SPACING_S} s",
                     "planned in": f"{plan_s:.0f} s"})
    for aid, reason in result.failures.items():
        print(f"  NOT ROUTED {aid}: {reason}")
    kinds = {f["id"]: f["kind"] for f in flights}
    for aid, pair, t in sorted({(a, p, t) for a, p, t in incursions}, key=lambda x: x[2])[:20]:
        holders = [(label, kind) for w0, w1, label, kind in gate.windows[pair] if w0 <= t < w1 and label != aid]
        node = position_node(result.paths[aid], t)
        print(f"  INCURSION {kinds[aid]} {aid} on {pair} at {t * DT // 60}:{t * DT % 60:02d} "
              f"(at {ap.label(node)}), runway held by {holders}")

    if args.gif:
        pitch_dir = OUT_DIR / "pitch"
        pitch_dir.mkdir(exist_ok=True)
        start_s = (args.start_minute * 60 if args.start_minute is not None
                   else busiest_window(flights, args.minutes, t0))
        out = animate(ap, flights, requests, result, gate, real_windows, rows, t0, args.minutes,
                      args.stride, pitch_dir / "compare.gif", start_s=start_s)
        print(f"Saved {out}")


if __name__ == "__main__":
    main()
