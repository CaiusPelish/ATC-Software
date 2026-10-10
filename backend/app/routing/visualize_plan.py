# backend/app/routing/visualize_plan.py

"""Animate a MAPF plan as a GIF: aircraft moving along the real taxiway curves.

Each aircraft is a coloured dot with its id, its planned route is drawn
faintly behind it, and a ring around the dot means it is waiting. Any
conflict check_paths finds (there should be none) is marked with a red X at
the place and time it happens.

Run from backend/:
    python3 -m app.routing.visualize_plan pileup
    python3 -m app.routing.visualize_plan headon
    python3 -m app.routing.visualize_plan random --n 10 --seed 3
    python3 -m app.routing.visualize_plan replay --minutes 20
Output goes to app/simulation/data/plans/<scenario>.gif.
"""

import argparse
import math
import random
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402

from .airport import DT, get_airport  # noqa: E402
from .mapf import AircraftRequest, plan_batch  # noqa: E402
from .osm_taxiways import DATA_DIR, edge_points  # noqa: E402
from .reservations import check_paths  # noqa: E402

OUT_DIR = DATA_DIR.parent / "plans"
A_RH = "408963125"


def point_along(points, fraction):
    """[lat, lon] at a fraction of the way along a polyline, by length."""
    seg = [math.dist(a, b) for a, b in zip(points, points[1:])]
    target = fraction * sum(seg)
    for (a, b), length in zip(zip(points, points[1:]), seg):
        if target <= length and length > 0:
            s = target / length
            return [a[0] + s * (b[0] - a[0]), a[1] + s * (b[1] - a[1])]
        target -= length
    return points[-1]


def position_at(airport, path, t, parks):
    """(lat, lon, waiting) of an aircraft at time t, or None if it isn't on the map."""
    if t < path[0][1] or (t > path[-1][1] and not parks):
        return None
    if t >= path[-1][1]:
        node = airport.nodes[path[-1][0]]
        return node.lat, node.lon, True
    for (u, t0), (v, t1) in zip(path, path[1:]):
        if t0 <= t < t1:
            if u == v:
                node = airport.nodes[u]
                return node.lat, node.lon, True
            pts = edge_points(airport.nodes, airport.geometry, u, v)
            lat, lon = point_along(pts, (t - t0) / (t1 - t0))
            return lat, lon, False
    return None


def animate(airport, requests, result, out, title, stride=1, zoom=True):
    parks = {r.id: r.goal_hold is None for r in requests}
    conflicts = check_paths(result.paths, park_at_goal=all(parks.values()))
    ids = sorted(result.paths)
    colours = {aid: plt.cm.tab10(i % 10) for i, aid in enumerate(ids)}
    t_first = min(p[0][1] for p in result.paths.values())
    t_last = max(p[-1][1] for p in result.paths.values()) + 2

    fig, ax = plt.subplots(figsize=(10, 10))
    drawn = set()
    for a, nbrs in airport.graph.items():
        for b in nbrs:
            if (b, a) in drawn:
                continue
            drawn.add((a, b))
            pts = edge_points(airport.nodes, airport.geometry, a, b)
            ax.plot([p[1] for p in pts], [p[0] for p in pts], color="#d0d3d8", lw=1, zorder=1)

    route_pts = []
    for aid, path in result.paths.items():
        line = [[airport.nodes[path[0][0]].lat, airport.nodes[path[0][0]].lon]]
        for (u, _), (v, _) in zip(path, path[1:]):
            if u != v:
                line.extend(edge_points(airport.nodes, airport.geometry, u, v)[1:])
        route_pts.extend(line)
        ax.plot([p[1] for p in line], [p[0] for p in line], color=colours[aid],
                lw=2.5, alpha=0.3, zorder=2)

    for c in conflicts:
        node = airport.nodes[c.node or c.edge[0]]
        ax.scatter(node.lon, node.lat, marker="x", s=200, color="red", zorder=6)

    lats = [p[0] for p in route_pts]
    lons = [p[1] for p in route_pts]
    if zoom:
        pad = 0.001
        ax.set_xlim(min(lons) - pad, max(lons) + pad)
        ax.set_ylim(min(lats) - pad, max(lats) + pad)
    mean_lat = sum(lats) / len(lats)
    ax.set_aspect(1 / math.cos(math.radians(mean_lat)))
    ax.set_xticks([])
    ax.set_yticks([])

    dots = ax.scatter([], [], s=90, zorder=4, edgecolors="white", linewidths=1)
    rings = ax.scatter([], [], s=320, facecolors="none", edgecolors="#333", linewidths=1.5, zorder=3)
    labels = {aid: ax.text(0, 0, aid, fontsize=8, weight="bold", zorder=5, visible=False) for aid in ids}
    clock = ax.text(0.02, 0.97, "", transform=ax.transAxes, fontsize=12, weight="bold", va="top")
    status = (f"{len(result.paths)} planned, {len(result.failures)} failed, "
              f"{len(conflicts)} conflicts, delay {result.total_delay_steps * DT}s")
    ax.set_title(f"{title}\n{status}", fontsize=11)

    def frame(t):
        xy, cols, waiting = [], [], []
        for aid in ids:
            pos = position_at(airport, result.paths[aid], t, parks[aid])
            label = labels[aid]
            if pos is None:
                label.set_visible(False)
                continue
            lat, lon, wait = pos
            xy.append((lon, lat))
            cols.append(colours[aid])
            if wait:
                waiting.append((lon, lat))
            label.set_position((lon, lat))
            label.set_visible(True)
        dots.set_offsets(xy or [(math.nan, math.nan)])
        dots.set_facecolors(cols or ["none"])
        rings.set_offsets(waiting or [(math.nan, math.nan)])
        clock.set_text(f"t = {t * DT}s")
        return [dots, rings, clock, *labels.values()]

    frames = range(t_first, t_last, stride)
    anim = FuncAnimation(fig, frame, frames=frames, blit=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    anim.save(out, writer=PillowWriter(fps=8))
    plt.close(fig)
    return out, conflicts


def scenario(airport, name, n=8, seed=1, minutes=20):
    if name == "pileup":
        nbrs = sorted(airport.graph[A_RH])
        return [AircraftRequest(f"N{i}", s, nbrs[(i + 2) % 4]) for i, s in enumerate(nbrs)], 1, True
    if name == "headon":
        a, b = "408963114", "408967521"
        return [AircraftRequest("N1", a, b), AircraftRequest("N2", b, a)], 1, True
    if name == "random":
        rng = random.Random(seed)
        pairs = zip(rng.sample(airport.spawn_nodes(), n), rng.sample(airport.hold_shorts(), n))
        return [AircraftRequest(f"N{i}", s, g, ready_t=rng.randint(0, 10))
                for i, (s, g) in enumerate(pairs)], 1, True
    if name == "replay":
        from ..simulation.traffic import load_departures

        limit = minutes * 60 // DT
        return [r for r, _ in load_departures() if r.ready_t < limit], 2, False
    raise ValueError(f"Unknown scenario {name!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=["pileup", "headon", "random", "replay"])
    parser.add_argument("--n", type=int, default=8, help="aircraft in the random scenario")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--minutes", type=int, default=20, help="replay: departures ready in the first N minutes")
    args = parser.parse_args(argv)

    airport = get_airport()
    requests, stride, zoom = scenario(airport, args.scenario, args.n, args.seed, args.minutes)
    result = plan_batch(airport, requests)
    for aid, reason in result.failures.items():
        print(f"FAILED {aid}: {reason}", file=sys.stderr)
    out, conflicts = animate(airport, requests, result, OUT_DIR / f"{args.scenario}.gif",
                             f"KEWR MAPF — {args.scenario}", stride=stride, zoom=zoom)
    for c in conflicts:
        print(f"CONFLICT: {c.message(airport)}", file=sys.stderr)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
