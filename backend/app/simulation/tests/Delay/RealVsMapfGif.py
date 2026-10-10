# backend/app/simulation/tests/Delay/RealVsMapfGif.py

"""GIF + stats: real KEWR ground movements next to MAPF routing between the same nodes.

Reads the TrackFrame pickle (builds it first if it's missing). For every
aircraft with a FirstNode and a different LastNode:
- real:  its ADS-B positions from FirstNodeTime to LastNodeTime;
- MAPF:  a route from FirstNode to LastNode, released at FirstNodeTime.
All aircraft are planned together with prioritized space-time A* (plan_batch)
at the real average moving speed, so the two sides share one clock.

Metrics per aircraft, real vs MAPF (same definitions as RealVsMapfTest):
- taxi time: first node to last node;
- stopped time: real = time at or below STOPPED_SPEED,
  MAPF = time not moving (start delay and waits on the way);
- distance: real = GPS track, MAPF = graph edge lengths.

Not modelled on the MAPF side: runway schedule (landings, takeoff slots,
crossing clearances), pushback, gates, and speed changes. That flatters MAPF:
real departures wait for their slot and real crossings wait for runway traffic.

Writes next to this file, in data/:
  real_vs_mapf_<start>_<n>h.gif        maps, live aircraft-minutes chart, running scoreboard;
                                       each flight has one colour and its ID label on both maps
  real_vs_mapf_<start>_<n>h_stats.png  summary: averages, per-aircraft taxi time, time saved
  real_vs_mapf_<start>_<n>h.csv        one row per aircraft

Run:  python3 RealVsMapfGif.py [start_hour] [n_hours] [--stride S]   (default 164 4, stride 20 s)
"""

import argparse
import sys
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
from app.routing.mapf import AircraftRequest, plan_batch  # noqa: E402
from app.routing.reservations import check_paths  # noqa: E402
from app.routing.runways import load_runways  # noqa: E402
from app.routing.visualize_plan import position_at  # noqa: E402
from app.simulation.compare_real_vs_mapf import INK, INK_2, MAPF, REAL, SURFACE, draw_airport  # noqa: E402
from app.simulation.tests.RealVsMapfTest import (  # noqa: E402
    mapf_metrics,
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
               for c in FLIGHT_COLORS + [INK, INK_2, MUTED, GRID, SURFACE]]
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


def flights_from(df):
    """[{id, rows, start, goal}]: each track cut to its first..last node."""
    flights = []
    for fid, track in df[df["FirstNode"].notna()].groupby("ID"):
        first = track.iloc[0]
        if first["FirstNode"] == first["LastNode"]:
            continue
        rows = track[track["UnixTime"].between(first["FirstNodeTime"], first["LastNodeTime"])]
        if len(rows) < 2:
            continue
        flights.append({"id": str(fid), "rows": rows.reset_index(drop=True),
                        "start": first["FirstNode"], "goal": first["LastNode"]})
    return flights


def plan(flights, t0):
    """(airport, requests, BatchResult) for every flight, at the real average moving speed."""
    ap = get_airport(taxi_speed=round(measured_taxi_speed(flights), 2))
    requests = [
        AircraftRequest(f["id"], f["start"], f["goal"],
                        ready_t=(int(f["rows"]["UnixTime"].iloc[0]) - t0) // DT, goal_hold=1)
        for f in flights
    ]
    return ap, requests, plan_batch(ap, requests, hold_starts=2)


def per_aircraft_metrics(ap, flights, requests, result, t0):
    """[{id, real, mapf, real_done_s, mapf_done_s}] for every planned flight; times from t0."""
    by_id = {r.id: r for r in requests}
    rows = []
    for f in flights:
        if f["id"] not in result.paths:
            continue
        r = by_id[f["id"]]
        mapf = mapf_metrics(ap, r, result.paths[f["id"]])
        rows.append({
            "id": f["id"], "real": real_metrics(f["rows"]), "mapf": mapf,
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


def frame_positions(ap, flights, result, t0, times):
    """Per frame: (real positions, MAPF positions) as [(id, lon, lat, stopped)]."""
    real_tracks = [
        (f["id"], f["rows"]["UnixTime"].to_numpy() - t0, f["rows"]["Lat"].to_numpy(),
         f["rows"]["Lon"].to_numpy(), f["rows"]["Speed"].to_numpy())
        for f in flights if f["id"] in result.paths
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
            pos = position_at(ap, path, t / DT, parks=False)
            if pos:
                planned.append((aid, pos[1], pos[0], pos[2]))
        out.append((real, planned))
    return out


def scoreboard(metrics, t):
    """Running averages over aircraft each side has finished by t."""
    lines = [f"{'finished aircraft':<22}{'real':>9}{'MAPF':>9}{'change':>9}"]
    done = {side: [m[side] for m in metrics if m[f"{side}_done_s"] <= t] for side in ("real", "mapf")}
    lines.append(f"{'count':<22}{len(done['real']):>9}{len(done['mapf']):>9}")
    for label, key, scale in (("avg taxi time (min)", "time_s", 60), ("avg stopped (min)", "wait_s", 60),
                              ("avg distance (km)", "length_m", 1000)):
        r, m = (sum(x[key] for x in done[s]) / scale / len(done[s]) if done[s] else 0.0
                for s in ("real", "mapf"))
        lines.append(f"{label:<22}{r:>9.1f}{m:>9.1f}{pct(r, m):>9}")
    return "\n".join(lines)


def animate(ap, flights, result, metrics, t0, stride, out):
    end_s = max(max(m["real_done_s"] for m in metrics),
                max(p[-1][1] for p in result.paths.values()) * DT)
    times = np.arange(0, end_s + stride, stride)
    positions = frame_positions(ap, flights, result, t0, times)
    colors = flight_colors(metrics, result)
    most = max(max(len(r), len(p)) for r, p in positions)
    # Aircraft-minutes spent taxiing (moving or stopped) up to each frame.
    cum_real = np.cumsum([len(r) for r, _ in positions]) * stride / 60
    cum_plan = np.cumsum([len(p) for _, p in positions]) * stride / 60
    clock = pd.to_datetime(t0 + times, unit="s", utc=True).tz_convert(TZ).tz_localize(None)

    fig = plt.figure(figsize=(15, 11.5), facecolor=SURFACE)
    grid = fig.add_gridspec(2, 2, height_ratios=[3, 1.25], left=0.04, right=0.98, top=0.9,
                            bottom=0.06, wspace=0.06, hspace=0.14)
    ax_real, ax_plan = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])
    ax_cum, ax_board = fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 1])
    for ax in (ax_real, ax_plan):
        draw_airport(ax, ap, load_runways().values())
    ax_real.set_title("Real — ADS-B positions", fontsize=13, weight="bold", color=INK, loc="left")
    ax_plan.set_title(f"MAPF — space-time A* at {ap.taxi_speed:g} m/s", fontsize=13, weight="bold",
                      color=INK, loc="left")
    clock_text = fig.text(0.5, 0.955, "", ha="center", fontsize=14, weight="bold", color=INK)
    fig.text(0.5, 0.93, "same colour + ID = same flight on both maps   ring = stopped / holding   "
             "each aircraft from its first to its last taxiway node", ha="center", fontsize=10, color=INK_2)

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

    ax_board.axis("off")
    board = ax_board.text(0.02, 0.95, "", transform=ax_board.transAxes, family="monospace",
                          fontsize=12, color=INK, va="top")

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
        update(real_layer, real)
        update(plan_layer, planned)
        real_line.set_data(clock[: i + 1], cum_real[: i + 1])
        plan_line.set_data(clock[: i + 1], cum_plan[: i + 1])
        real_label.set_position((clock[i], cum_real[i]))
        real_label.set_text(f" {cum_real[i]:,.0f}")
        plan_label.set_position((clock[i], cum_plan[i]))
        plan_label.set_text(f" {cum_plan[i]:,.0f}  ({pct(cum_real[i], cum_plan[i])})")
        board.set_text(scoreboard(metrics, times[i]))
        clock_text.set_text(f"KEWR  {clock[i]:%a %Y-%m-%d %H:%M:%S} EDT")
        return []

    anim = FuncAnimation(fig, frame, frames=len(times), init_func=lambda: [], blit=False)
    anim.save(out, writer=FixedPaletteWriter(fps=12), dpi=72)
    plt.close(fig)
    return out


def stats_figure(metrics, ap, out):
    """Averages per aircraft (three small multiples), per-aircraft taxi time, time saved."""
    real_t = np.array([m["real"]["time_s"] for m in metrics]) / 60
    mapf_t = np.array([m["mapf"]["time_s"] for m in metrics]) / 60
    saved = real_t - mapf_t

    fig = plt.figure(figsize=(14, 9.5), facecolor=SURFACE, layout="constrained")
    grid = fig.add_gridspec(2, 3, height_ratios=[1, 1.35])
    for col, (label, key, scale) in enumerate((("Avg taxi time per aircraft (min)", "time_s", 60),
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

    ax = fig.add_subplot(grid[1, :2])
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

    ax = fig.add_subplot(grid[1, 2])
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
                 "no runway schedule on the MAPF side", fontsize=14, weight="bold", color=INK)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("start_hour", nargs="?", type=int, default=164)
    parser.add_argument("n_hours", nargs="?", type=int, default=4)
    parser.add_argument("--stride", type=int, default=20, help="seconds of traffic per GIF frame")
    args = parser.parse_args(argv)

    df = load_tracks(args.start_hour, args.n_hours)
    flights = flights_from(df)
    t0 = int(min(f["rows"]["UnixTime"].iloc[0] for f in flights))
    ap, requests, result = plan(flights, t0)
    conflicts = check_paths(result.paths, park_at_goal=False)
    print(f"{len(flights)} aircraft: {len(result.paths)} planned, {len(result.failures)} failed, "
          f"{len(conflicts)} taxiway conflicts (speed {ap.taxi_speed:g} m/s)")
    for aid, reason in result.failures.items():
        print(f"  failed {aid}: {reason}")

    metrics = per_aircraft_metrics(ap, flights, requests, result, t0)
    print_table("All aircraft", summarize(metrics, "real"), summarize(metrics, "mapf"))

    stem = OUT_DIR / f"real_vs_mapf_{args.start_hour}_{args.n_hours}h"
    pd.DataFrame([
        {"id": m["id"], **{f"{side}_{k}": round(v, 1) for side in ("real", "mapf") for k, v in m[side].items()}}
        for m in metrics
    ]).to_csv(f"{stem}.csv", index=False)
    stats_figure(metrics, ap, f"{stem}_stats.png")
    animate(ap, flights, result, metrics, t0, args.stride, f"{stem}.gif")
    print(f"\nSaved {stem}.csv\nSaved {stem}_stats.png\nSaved {stem}.gif")


if __name__ == "__main__":
    main()
