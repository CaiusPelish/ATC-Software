# backend/app/simulation/tests/Delay/RealVsMapfGif.py

"""GIF + stats: real KEWR ground movements next to MAPF routing between the same nodes.

Reads the TrackFrame pickle (builds it first if it's missing) and finds every
real takeoff and landing roll in the same hours (real_runway_uses). Each
track is cut to its taxi, from the first to the last taxiway node it passes:
- departures: until their real takeoff roll starts (holding at the line-up
  point counts as taxi);
- arrivals: from when they vacated the runway;
- anything else (tows, repositioning): the whole track.
The real side is those ADS-B positions; the MAPF side is a route between the
same nodes, released when the real aircraft reached its first node.

All aircraft are planned together with prioritized space-time A* at the real
average moving speed, under compare_real_vs_mapf's runway rules:
- every real landing holds its runway from 2 nm final until it vacated, and is
  planned around, never moved;
- a crossing holds the runway against landings and takeoffs while the aircraft
  is on it, but several aircraft may cross together (crossings_overlap);
- everyone is planned in the order they appear, not arrivals first, so an
  arrival that must cross a busy departure runway queues at the hold-short
  line and crosses with whoever else is waiting in the next gap;
- a departure takes off once it reaches its line-up point and the runway is
  free: a TAKEOFF_ROLL_S roll, at least TAKEOFF_SPACING_S after the last
  takeoff from that runway. Its takeoff time is the planner's, not the real one.

Metrics per aircraft, real vs MAPF:
- taxi time: first node to the takeoff roll (departures) or the last node;
- stopped time: real = time at or below STOPPED_SPEED,
  MAPF = time not moving (start delay, waits on the way, holding for the runway);
- moving time: taxi time minus stopped time;
- distance: real = GPS track, MAPF = graph edge lengths.

Averages only count aircraft both sides have: planned by MAPF, and in the
GIF only once they have finished on both maps. Runways turn red on each map
while in use on that side.

Not modelled on the MAPF side: pushback, gates, wake categories, and speed
changes.

Writes next to this file, in data/:
  real_vs_mapf_<start>_<n>h.gif        maps, live aircraft-minutes chart, running avg moving time,
                                       running scoreboard;
                                       each flight has one colour and its ID label on both maps
  real_vs_mapf_<start>_<n>h_stats.png  summary: averages, per-aircraft taxi time, time saved
  real_vs_mapf_<start>_<n>h.csv        one row per aircraft

Run:  python3 RealVsMapfGif.py [start_hour] [n_hours] [--stride S]   (default 164 4, stride 20 s)
"""

import argparse
import dataclasses
import math
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from PIL import Image  # noqa: E402

BACKEND = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from TrackFrame import OUT_DIR, build_tracks  # noqa: E402
from app.routing.airport import DT, get_airport  # noqa: E402
from app.routing.reservations import check_paths  # noqa: E402
from app.routing.runways import load_runways, runway_conflicts  # noqa: E402
from app.routing.visualize_plan import position_at  # noqa: E402
from app.simulation.compare_real_vs_mapf import (  # noqa: E402
    FOLLOW_M,
    IN_USE,
    INK,
    INK_2,
    MAPF,
    NODE_CLEAR_M,
    REAL,
    RUNWAY,
    SURFACE,
    TAKEOFF_ROLL_S,
    TAKEOFF_SPACING_S,
    build_gate,
    draw_airport,
    make_requests,
    planner,
)
from app.simulation.runway_schedule import real_runway_uses  # noqa: E402
from app.simulation.tests.RealVsMapfTest import (  # noqa: E402
    NODE_SNAP_M,
    NodeSnapper,
    load_hours,
    measured_taxi_speed,
    print_table,
    real_metrics,
    summarize,
)
from app.simulation.traffic import STOPPED_SPEED  # noqa: E402

MUTED = "#898781"
# Reference categorical palette, fixed order. Colours repeat across flights; the ID label identifies one.
FLIGHT_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRID = "#e1e0d9"
TZ = "America/New_York"


class FixedPaletteWriter(PillowWriter):
    """GIF frames mapped onto one fixed palette: the flight colours plus a grey ramp.

    Pillow's default per-frame palette spends its slots on the map's greys
    and turns small coloured dots muddy or black.
    """

    def setup(self, fig, outfile, dpi=None):
        super().setup(fig, outfile, dpi)
        rgb = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
               for c in FLIGHT_COLORS + [INK, INK_2, MUTED, GRID, SURFACE, IN_USE, RUNWAY]]
        rgb += [(v, v, v) for v in range(0, 256, 2)]
        self._palette = Image.new("P", (1, 1))
        self._palette.putpalette([x for c in rgb for x in c] + [0] * (768 - 3 * len(rgb)))

    def grab_frame(self, **savefig_kwargs):
        super().grab_frame(**savefig_kwargs)
        frame = self._frames.pop().convert("RGB")
        self._frames.append(frame.quantize(palette=self._palette, dither=Image.Dither.NONE))


def load_tracks(start_hour, n_hours):
    path = OUT_DIR / f"kewr_{start_hour}_{n_hours}h_tracks.pkl"
    if path.exists():
        return pd.read_pickle(path)
    df = build_tracks(start_hour, n_hours)
    OUT_DIR.mkdir(exist_ok=True)
    df.to_pickle(path)
    return df


def runway_uses(start_hour, n_hours):
    """[RunwayUse] for every real takeoff and landing roll, in Unix seconds (cached in OUT_DIR)."""
    path = OUT_DIR / f"kewr_{start_hour}_{n_hours}h_runway_uses.pkl"
    if path.exists():
        return pd.read_pickle(path)
    uses = real_runway_uses(load_hours(start_hour, n_hours), t0=0)
    OUT_DIR.mkdir(exist_ok=True)
    pd.to_pickle(uses, path)
    return uses


def flights_from(df, uses):
    """[{id, kind, pair, rows, start, goal}]: each track cut to its taxi (see module docstring).

    kind is "departure", "arrival" or "other"; pair is the runway it took off from or landed on.
    """
    snapper = NodeSnapper(get_airport())
    use_of = {u.id: u for u in uses}
    flights = []
    for fid, track in df[df["FirstNode"].notna()].groupby("ID"):
        use = use_of.get(str(fid))
        kind = {"takeoff": "departure", "landing": "arrival"}[use.kind] if use else "other"
        if kind == "departure":
            track = track[track["UnixTime"] < use.roll_start]
        elif kind == "arrival":
            track = track[track["UnixTime"] >= use.busy_until]
        if len(track) < 2:
            continue
        ids, dist = snapper.nearest(track["Lat"].to_numpy(), track["Lon"].to_numpy())
        on_node = np.flatnonzero(dist <= NODE_SNAP_M)
        if not len(on_node) or ids[on_node[0]] == ids[on_node[-1]]:
            continue
        first, last = on_node[0], on_node[-1]
        # A departure's taxi runs on to its takeoff roll: holding at the line-up point counts.
        rows = track.iloc[first:] if kind == "departure" else track.iloc[first:last + 1]
        if len(rows) < 2:
            continue
        flights.append({"id": str(fid), "kind": kind, "pair": use.pair if use else None,
                        "rows": rows.reset_index(drop=True), "start": ids[first], "goal": ids[last]})
    return flights


def plan(flights, uses, t0):
    """(airport, requests, BatchResult, gate, departs_from, spacing), at the real average moving speed.

    gate ends up holding the real landings plus every runway use the plan claimed;
    spacing is the ReservationTable's {node_buffer, follow_gap}.
    """
    ap = get_airport(taxi_speed=round(measured_taxi_speed(flights), 2))
    step_m = ap.taxi_speed * DT
    spacing = {"node_buffer": math.ceil(NODE_CLEAR_M / step_m), "follow_gap": math.ceil(FOLLOW_M / step_m)}
    gate = build_gate(ap, shift_uses(uses, t0), crossings_overlap=True)
    started, done = time.perf_counter(), []

    def progress(r, path):
        done.append(r.id)
        if len(done) % 25 == 0 or len(done) == len(flights):
            print(f"  planned {len(done)}/{len(flights)}  ({time.perf_counter() - started:.0f} s)", flush=True)

    run, departs_from = planner(ap, gate, flights, **spacing, progress=progress, arrivals_first=False)
    requests = make_requests(flights, t0)
    print(f"Planning {len(flights)} aircraft at {ap.taxi_speed:g} m/s...", flush=True)
    return ap, requests, run(requests), gate, departs_from, spacing


def shift_uses(uses, t0):
    """uses with times in seconds from t0."""
    return [dataclasses.replace(u, busy_from=u.busy_from - t0, busy_until=u.busy_until - t0,
                                roll_start=u.roll_start - t0) for u in uses]


def mapf_metrics(ap, request, path):
    """Taxi from ready_t until the takeoff roll starts (departures) or the goal is reached.

    Waiting at the line-up point for the runway counts as stopped, as it does on the real side.
    """
    end = path[-1][1] - request.goal_hold
    moves = [(u, v, t0, t1) for (u, t0), (v, t1) in zip(path, path[1:]) if u != v and t1 <= end]
    total = end - request.ready_t
    moving = sum(t1 - t0 for _, _, t0, t1 in moves)
    return {
        "length_m": sum(ap.graph[u][v] for u, v, _, _ in moves),
        "time_s": total * DT,
        "wait_s": (total - moving) * DT,
    }


def per_aircraft_metrics(ap, flights, requests, result, t0):
    """[{id, kind, real, mapf, real_done_s, mapf_done_s}] for every planned flight; times from t0."""
    by_id = {r.id: r for r in requests}
    rows = []
    for f in flights:
        if f["id"] not in result.paths:
            continue
        r = by_id[f["id"]]
        mapf = mapf_metrics(ap, r, result.paths[f["id"]])
        real = real_metrics(f["rows"])
        for side in (real, mapf):
            side["moving_s"] = side["time_s"] - side["wait_s"]
        rows.append({
            "id": f["id"], "kind": f["kind"], "real": real, "mapf": mapf,
            "real_done_s": int(f["rows"]["UnixTime"].iloc[-1]) - t0,
            "mapf_done_s": r.ready_t * DT + mapf["time_s"],
        })
    return rows


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def pct(real, mapf):
    return f"{(mapf - real) / real * 100:+.0f}%" if real else ""


def flight_colors(metrics, result):
    """{id: colour}: flights on the map at the same time (either side) get different
    colours while there are enough; past that, the least-used colour among them."""
    spans = sorted(
        (min(m["real_done_s"] - m["real"]["time_s"], result.paths[m["id"]][0][1] * DT),
         max(m["real_done_s"], result.paths[m["id"]][-1][1] * DT), m["id"])
        for m in metrics
    )
    colors, active = {}, []
    for start, end, aid in spans:
        active = [(e, a) for e, a in active if e >= start]
        in_use = [colors[a] for _, a in active]
        colors[aid] = min(FLIGHT_COLORS, key=lambda c: (in_use.count(c), FLIGHT_COLORS.index(c)))
        active.append((end, aid))
    return colors


def frame_positions(ap, flights, result, t0, times, ids):
    """Per frame: (real positions, MAPF positions) as [(id, lon, lat, stopped)], for ids only."""
    real_tracks = [
        (f["id"], f["rows"]["UnixTime"].to_numpy() - t0, f["rows"]["Lat"].to_numpy(),
         f["rows"]["Lon"].to_numpy(), f["rows"]["Speed"].to_numpy())
        for f in flights if f["id"] in ids
    ]
    out = []
    for t in times:
        real = []
        for aid, tt, lat, lon, speed in real_tracks:
            if tt[0] <= t <= tt[-1]:
                i = min(np.searchsorted(tt, t), len(tt) - 1)
                real.append((aid, np.interp(t, tt, lon), np.interp(t, tt, lat), speed[i] <= STOPPED_SPEED))
        planned = []
        for aid, path in result.paths.items():
            if aid not in ids:
                continue
            pos = position_at(ap, path, t / DT, parks=False)
            if pos:
                planned.append((aid, pos[1], pos[0], pos[2]))
        out.append((real, planned))
    return out


def finished_both(metrics, t):
    """Aircraft that have finished on both sides by t."""
    return [m for m in metrics if max(m["real_done_s"], m["mapf_done_s"]) <= t]


def scoreboard(metrics, t):
    """Running averages over aircraft both sides have finished by t."""
    both = finished_both(metrics, t)
    lines = [f"{'finished on both':<20}{'real':>7}{'MAPF':>7}{'change':>8}"]
    lines.append(f"{'aircraft':<20}{len(both):>7}{len(both):>7}")
    done = {side: [m[side] for m in both] for side in ("real", "mapf")}
    for label, key, scale in (("avg taxi time (min)", "time_s", 60), ("avg moving (min)", "moving_s", 60),
                              ("avg stopped (min)", "wait_s", 60), ("avg distance (km)", "length_m", 1000)):
        r, m = (sum(x[key] for x in done[s]) / scale / len(done[s]) if done[s] else 0.0
                for s in ("real", "mapf"))
        lines.append(f"{label:<20}{r:>7.1f}{m:>7.1f}{pct(r, m):>8}")
    return "\n".join(lines)


def animate(ap, flights, result, metrics, gate, real_uses, t0, stride, out):
    """real_uses: the real runway uses, times from t0; gate: the MAPF side's runway windows."""
    end_s = max(max(m["real_done_s"] for m in metrics),
                max(p[-1][1] for p in result.paths.values()) * DT)
    times = np.arange(0, end_s + stride, stride)
    positions = frame_positions(ap, flights, result, t0, times, {m["id"] for m in metrics})
    colors = flight_colors(metrics, result)
    most = max(max(len(r), len(p)) for r, p in positions)
    # Aircraft-minutes spent taxiing (moving or stopped) up to each frame.
    cum_real = np.cumsum([len(r) for r, _ in positions]) * stride / 60
    cum_plan = np.cumsum([len(p) for _, p in positions]) * stride / 60
    clock = pd.to_datetime(t0 + times, unit="s", utc=True).tz_convert(TZ).tz_localize(None)
    # Average moving time (stops excluded) over aircraft finished on both sides by each frame.
    avg_move = {side: np.full(len(times), np.nan) for side in ("real", "mapf")}
    for i, t in enumerate(times):
        both = finished_both(metrics, t)
        for side in avg_move:
            if both:
                avg_move[side][i] = np.mean([m[side]["moving_s"] for m in both]) / 60

    fig = plt.figure(figsize=(15, 11.5), facecolor=SURFACE)
    grid = fig.add_gridspec(2, 1, height_ratios=[3, 1.25], left=0.04, right=0.98, top=0.9,
                            bottom=0.06, hspace=0.14)
    maps = grid[0].subgridspec(1, 2, wspace=0.06)
    bottom = grid[1].subgridspec(1, 3, width_ratios=[1, 1, 0.9], wspace=0.3)
    ax_real, ax_plan = fig.add_subplot(maps[0]), fig.add_subplot(maps[1])
    ax_cum, ax_move, ax_board = (fig.add_subplot(bottom[k]) for k in range(3))
    real_runways, plan_runways = (draw_airport(ax, ap, load_runways().values()) for ax in (ax_real, ax_plan))
    ax_real.set_title("Real — ADS-B positions", fontsize=13, weight="bold", color=INK, loc="left")
    ax_plan.set_title(f"MAPF — space-time A* at {ap.taxi_speed:g} m/s, real landings, "
                      f"takeoffs {TAKEOFF_SPACING_S} s apart", fontsize=13, weight="bold", color=INK, loc="left")
    clock_text = fig.text(0.5, 0.955, "", ha="center", fontsize=14, weight="bold", color=INK)
    fig.text(0.5, 0.93, "same colour + ID = same flight on both maps   ring = stopped / holding   "
             "red runway = in use   departures until their takeoff roll, arrivals from vacating the runway",
             ha="center", fontsize=10, color=INK_2)

    def layer(ax):
        dots = ax.scatter([], [], s=48, edgecolors="white", linewidths=0.8, zorder=4)
        rings = ax.scatter([], [], s=170, facecolors="none", edgecolors=INK, linewidths=1.0, zorder=3)
        text = ax.text(0.01, -0.03, "", transform=ax.transAxes, fontsize=11, color=INK)
        labels = [ax.annotate("", (0, 0), xytext=(5, 4), textcoords="offset points", fontsize=7,
                              color=INK, zorder=5) for _ in range(most)]
        return dots, rings, text, labels

    real_layer, plan_layer = layer(ax_real), layer(ax_plan)

    style_axes(ax_cum)
    ax_cum.set_xlim(clock[0], clock[-1])
    ax_cum.set_ylim(0, max(cum_real[-1], cum_plan[-1]) * 1.12)
    ax_cum.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_cum.set_title("Cumulative aircraft-minutes on the taxiways (lower is better)", fontsize=11,
                     color=INK, loc="left")
    real_line, = ax_cum.plot([], [], color=REAL, linewidth=2)
    plan_line, = ax_cum.plot([], [], color=MAPF, linewidth=2)
    real_label = ax_cum.text(0, 0, "", fontsize=9, color=INK, va="bottom")
    plan_label = ax_cum.text(0, 0, "", fontsize=9, color=INK, va="top")
    ax_cum.plot([], [], color=REAL, linewidth=2, label="Real")
    ax_cum.plot([], [], color=MAPF, linewidth=2, label="MAPF")
    ax_cum.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK)

    style_axes(ax_move)
    ax_move.set_xlim(clock[0], clock[-1])
    ax_move.set_ylim(0, np.nanmax([avg_move["real"], avg_move["mapf"]]) * 1.2)
    ax_move.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_move.set_title("Avg taxi time excl. stops, per aircraft (min)", fontsize=11, color=INK, loc="left")
    move_lines = {side: ax_move.plot([], [], color=c, linewidth=2)[0]
                  for side, c in (("real", REAL), ("mapf", MAPF))}
    move_labels = {side: ax_move.text(0, 0, "", fontsize=9, color=INK, va=va)
                   for side, va in (("real", "bottom"), ("mapf", "top"))}
    ax_move.text(0.01, 0.97, "aircraft finished on both sides", transform=ax_move.transAxes,
                 fontsize=8, color=MUTED, va="top")

    ax_board.axis("off")
    board = ax_board.text(0.0, 0.95, "", transform=ax_board.transAxes, family="monospace",
                          fontsize=9.5, color=INK, va="top")

    def update(layer_, pts):
        dots, rings, text, labels = layer_
        dots.set_offsets([(lon, lat) for _, lon, lat, _ in pts] or np.empty((0, 2)))
        dots.set_facecolors([colors[aid] for aid, *_ in pts] or "none")
        held = [(lon, lat) for _, lon, lat, stopped in pts if stopped]
        rings.set_offsets(held or np.empty((0, 2)))
        for label, (aid, lon, lat, _) in zip(labels, pts):
            label.xy = (lon, lat)
            label.set_text(aid)
        for label in labels[len(pts):]:
            label.set_text("")
        text.set_text(f"taxiing now {len(pts):>2}   stopped now {len(held):>2}")

    def frame(i):
        real, planned = positions[i]
        t = times[i]
        for pair, line in real_runways.items():
            busy = any(u.pair == pair and u.busy_from <= t < u.busy_until for u in real_uses)
            line.set_color(IN_USE if busy else RUNWAY)
        for pair, line in plan_runways.items():
            line.set_color(IN_USE if gate.in_use(pair, t / DT) else RUNWAY)
        update(real_layer, real)
        update(plan_layer, planned)
        real_line.set_data(clock[: i + 1], cum_real[: i + 1])
        plan_line.set_data(clock[: i + 1], cum_plan[: i + 1])
        real_label.set_position((clock[i], cum_real[i]))
        real_label.set_text(f" {cum_real[i]:,.0f}")
        plan_label.set_position((clock[i], cum_plan[i]))
        plan_label.set_text(f" {cum_plan[i]:,.0f}  ({pct(cum_real[i], cum_plan[i])})")
        for side in ("real", "mapf"):
            move_lines[side].set_data(clock[: i + 1], avg_move[side][: i + 1])
            v = avg_move[side][i]
            move_labels[side].set_position((clock[i], 0 if np.isnan(v) else v))
            move_labels[side].set_text("" if np.isnan(v) else f" {v:.1f}" + (
                f"  ({pct(avg_move['real'][i], v)})" if side == "mapf" else ""))
        board.set_text(scoreboard(metrics, t))
        clock_text.set_text(f"KEWR  {clock[i]:%a %Y-%m-%d %H:%M:%S} EDT")
        return []

    anim = FuncAnimation(fig, frame, frames=len(times), init_func=lambda: [], blit=False)
    started = time.perf_counter()

    def progress(i, n):
        if i % 50 == 0 or i == n - 1:
            print(f"  frame {i + 1}/{n}  ({time.perf_counter() - started:.0f} s)", flush=True)

    print(f"Rendering {len(times)} GIF frames...", flush=True)
    anim.save(out, writer=FixedPaletteWriter(fps=12), dpi=72, progress_callback=progress)
    plt.close(fig)
    return out


def stats_figure(metrics, ap, out):
    """Averages per aircraft (three small multiples), per-aircraft taxi time, time saved."""
    real_t = np.array([m["real"]["time_s"] for m in metrics]) / 60
    mapf_t = np.array([m["mapf"]["time_s"] for m in metrics]) / 60
    saved = real_t - mapf_t

    fig = plt.figure(figsize=(17, 9.5), facecolor=SURFACE, layout="constrained")
    grid = fig.add_gridspec(2, 4, height_ratios=[1, 1.35])
    for col, (label, key, scale) in enumerate((("Avg taxi time per aircraft (min)", "time_s", 60),
                                               ("Avg taxi time excl. stops (min)", "moving_s", 60),
                                               ("Avg stopped per aircraft (min)", "wait_s", 60),
                                               ("Avg distance per aircraft (km)", "length_m", 1000))):
        ax = fig.add_subplot(grid[0, col])
        style_axes(ax)
        r = np.mean([m["real"][key] for m in metrics]) / scale
        p = np.mean([m["mapf"][key] for m in metrics]) / scale
        ax.bar(["Real", "MAPF"], [r, p], color=[REAL, MAPF], width=0.5, edgecolor=SURFACE, linewidth=2)
        for x, v in enumerate((r, p)):
            ax.text(x, v, f"{v:.1f}", ha="center", va="bottom", fontsize=11, color=INK)
        ax.set_ylim(0, max(r, p) * 1.2)
        ax.set_title(f"{label}\nMAPF {pct(r, p)}", fontsize=11, color=INK, loc="left")
        ax.tick_params(axis="x", colors=INK, labelsize=11)

    ax = fig.add_subplot(grid[1, :3])
    style_axes(ax)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    top = max(real_t.max(), mapf_t.max()) * 1.05
    ax.plot([0, top], [0, top], color=MUTED, linewidth=1, linestyle="--", zorder=1)
    ax.text(top * 0.98, top * 0.98, "same time", color=MUTED, fontsize=9, ha="right", va="bottom")
    ax.scatter(real_t, mapf_t, s=64, color=MAPF, alpha=0.7, edgecolors=SURFACE, linewidths=2, zorder=2)
    faster = (mapf_t < real_t).sum()
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_xlabel("real taxi time (min)", color=INK_2)
    ax.set_ylabel("MAPF taxi time (min)", color=INK_2)
    ax.set_title(f"Taxi time per aircraft: below the line = MAPF faster "
                 f"({faster} of {len(metrics)} aircraft)", fontsize=11, color=INK, loc="left")

    ax = fig.add_subplot(grid[1, 3])
    style_axes(ax)
    lo, hi = 2 * np.floor(saved.min() / 2), 2 * np.ceil(saved.max() / 2)
    ax.hist(saved, bins=np.arange(lo, hi + 2, 2), color=MAPF, edgecolor=SURFACE, linewidth=1)
    ax.axvline(0, color=MUTED, linewidth=1)
    med = np.median(saved)
    ax.axvline(med, color=INK, linewidth=1, linestyle="--")
    ax.text(med, ax.get_ylim()[1] * 0.97, f" median {med:+.1f} min", color=INK, fontsize=9, va="top")
    ax.set_xlabel("time saved by MAPF per aircraft (min)", color=INK_2)
    ax.set_ylabel("aircraft", color=INK_2)
    ax.set_title("Time saved per aircraft", fontsize=11, color=INK, loc="left")

    fig.suptitle(f"KEWR real vs MAPF — {len(metrics)} aircraft, MAPF at {ap.taxi_speed:g} m/s, "
                 f"real landings, takeoffs {TAKEOFF_SPACING_S} s apart ({TAKEOFF_ROLL_S} s roll)", fontsize=14, weight="bold", color=INK)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("start_hour", nargs="?", type=int, default=164)
    parser.add_argument("n_hours", nargs="?", type=int, default=4)
    parser.add_argument("--stride", type=int, default=20, help="seconds of traffic per GIF frame")
    args = parser.parse_args(argv)

    print(f"Loading KEWR hours {args.start_hour}..{args.start_hour + args.n_hours - 1}...", flush=True)
    df = load_tracks(args.start_hour, args.n_hours)
    uses = runway_uses(args.start_hour, args.n_hours)
    flights = flights_from(df, uses)
    t0 = int(min(f["rows"]["UnixTime"].iloc[0] for f in flights))
    ap, requests, result, gate, departs_from, spacing = plan(flights, uses, t0)
    conflicts = check_paths(result.paths, park_at_goal=False, **spacing)
    incursions = runway_conflicts(gate, result.paths, departs_from)
    kinds = {k: sum(f["kind"] == k for f in flights) for k in ("departure", "arrival", "other")}
    print(f"{len(flights)} aircraft ({', '.join(f'{n} {k}' for k, n in kinds.items())}), "
          f"{len(uses)} real runway uses")
    print(f"MAPF: {len(result.paths)} planned, {len(result.failures)} failed, {len(conflicts)} taxiway "
          f"conflicts, {len({(a, p) for a, p, _ in incursions})} runway incursions (speed {ap.taxi_speed:g} m/s)")
    for aid, reason in result.failures.items():
        print(f"  failed {aid}: {reason}") 

    metrics = per_aircraft_metrics(ap, flights, requests, result, t0)
    print_table("All aircraft", summarize(metrics, "real"), summarize(metrics, "mapf"))
    for kind in kinds:
        subset = [m for m in metrics if m["kind"] == kind]
        if subset:
            print_table(f"{kind.capitalize()}s", summarize(subset, "real"), summarize(subset, "mapf"))

    stem = OUT_DIR / f"real_vs_mapf_{args.start_hour}_{args.n_hours}h"
    pd.DataFrame([
        {"id": m["id"], "kind": m["kind"],
         **{f"{side}_{k}": round(v, 1) for side in ("real", "mapf") for k, v in m[side].items()}}
        for m in metrics
    ]).to_csv(f"{stem}.csv", index=False)
    stats_figure(metrics, ap, f"{stem}_stats.png")
    print(f"Saved {stem}.csv and {stem}_stats.png", flush=True)
    animate(ap, flights, result, metrics, gate, shift_uses(uses, t0), t0, args.stride, f"{stem}.gif")
    print(f"\nSaved {stem}.csv\nSaved {stem}_stats.png\nSaved {stem}.gif")
    print("finished")


if __name__ == "__main__":
    main()
