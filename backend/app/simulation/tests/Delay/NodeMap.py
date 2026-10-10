# backend/app/simulation/tests/Delay/NodeMap.py

"""Template: whole-KEWR taxiway node map, real aircraft movements vs MAPF routes.

Two panels with the same extent (the whole airport):
- left:  real routes (blue), one line per aircraft;
- right: MAPF routes (orange), one line per aircraft.
Lines are semi-transparent, so taxiways many aircraft share draw darker.

Fill in the two dicts in __main__ (or import plot_comparison) with
{aircraft id: route}. A route is a list of graph node ids; a MAPF
[(node, t), ...] path is also accepted and its waits are dropped.

Writes data/nodemap.png next to this file.

Run:  python3 NodeMap.py
"""

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
import numpy as np  # noqa: E402

BACKEND = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BACKEND))

from app.routing.airport import get_airport  # noqa: E402
from app.routing.osm_taxiways import edge_points  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data"

REAL_COLOR = "#2a78d6"
MAPF_COLOR = "#eb6834"
EDGE_COLOR = "#d5d7db"
NODE_COLOR = "#b3b8bf"
HOLD_COLOR = "#8a8f96"
INK = "#2b2b2b"


def as_node_route(path):
    """A node list, or a MAPF [(node, t), ...] path with waits dropped."""
    if path and isinstance(path[0], (tuple, list)):
        path = [n for n, _ in path]
    return [n for i, n in enumerate(path) if i == 0 or n != path[i - 1]]


def route_polyline(ap, route):
    """(n, 2) [lon, lat] array following each edge's curve."""
    line = [[ap.nodes[route[0]].lat, ap.nodes[route[0]].lon]]
    for a, b in zip(route, route[1:]):
        line.extend(edge_points(ap.nodes, ap.geometry, a, b)[1:])
    return np.array(line)[:, ::-1]


def draw_base_map(ax, ap, holds=True, node_ids=False):
    """The whole taxiway network, faded, zoomed to the full airport."""
    edges = [(u, v) for u, nbrs in ap.graph.items() for v in nbrs if u < v]
    ax.add_collection(LineCollection(
        [np.array(edge_points(ap.nodes, ap.geometry, u, v))[:, ::-1] for u, v in edges],
        colors=EDGE_COLOR, linewidths=0.8, zorder=1))
    lats = [n.lat for n in ap.nodes.values()]
    lons = [n.lon for n in ap.nodes.values()]
    ax.scatter(lons, lats, s=3, color=NODE_COLOR, zorder=2)
    if holds:
        h = ap.hold_shorts("runway")
        ax.scatter([ap.nodes[k].lon for k in h], [ap.nodes[k].lat for k in h], s=10,
                   marker="D", color=HOLD_COLOR, zorder=2, label="runway hold-short")
    if node_ids:
        for k, n in ap.nodes.items():
            ax.annotate(str(k), (n.lon, n.lat), fontsize=3, color=INK, zorder=3)
    pad = 0.002
    ax.set_xlim(min(lons) - pad, max(lons) + pad)
    ax.set_ylim(min(lats) - pad, max(lats) + pad)
    ax.set_aspect(1 / math.cos(math.radians(ap.origin[0])))
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")


def draw_routes(ax, ap, routes, color, label, alpha=0.35):
    """Every route as one line along the taxiways; start nodes as dots, ends as squares."""
    routes = [as_node_route(r) for r in routes.values()]
    routes = [r for r in routes if len(r) >= 2]
    ax.add_collection(LineCollection([route_polyline(ap, r) for r in routes], colors=color,
                                     linewidths=1.8, alpha=alpha, capstyle="round", zorder=4))
    ax.plot([], [], color=color, linewidth=1.8, label=f"{label} ({len(routes)} aircraft)")
    for idx, marker in ((0, "o"), (-1, "s")):
        ax.scatter([ap.nodes[r[idx]].lon for r in routes], [ap.nodes[r[idx]].lat for r in routes],
                   s=16, marker=marker, color=color, edgecolor="white", linewidth=0.6, zorder=5)


def plot_comparison(real, mapf, out, title="KEWR ground movements: real vs MAPF", ap=None):
    """Real (left) | MAPF (right), whole airport. real/mapf are {aircraft id: route}."""
    ap = ap or get_airport()
    fig, (real_ax, mapf_ax) = plt.subplots(1, 2, figsize=(14, 10), sharex=True, sharey=True,
                                           layout="constrained")
    for ax, routes, color, label in ((real_ax, real, REAL_COLOR, "Real"),
                                     (mapf_ax, mapf, MAPF_COLOR, "MAPF")):
        draw_base_map(ax, ap)
        draw_routes(ax, ap, routes, color, label)
        ax.set_title(label, fontsize=14, weight="bold", color=INK)
        ax.legend(loc="upper left", fontsize=9)
    fig.suptitle(title, fontsize=16, weight="bold", color=INK)
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return out


if __name__ == "__main__":
    # TODO: {aircraft id: [node, node, ...]} from the real ADS-B tracks.
    real_routes = {}
    # TODO: {aircraft id: path} from your MAPF planner, e.g. result.paths.
    mapf_routes = {}

    OUT_DIR.mkdir(exist_ok=True)
    out = plot_comparison(real_routes, mapf_routes, OUT_DIR / "nodemap.png")
    print(f"Saved {out}")
