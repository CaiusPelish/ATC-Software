# backend/app/routing/ground_graph.py

"""KEWR ground graph: the simplified taxiway graph plus both runways as nodes and edges.

Built from KEWR_taxiway_graph_simple.json (taxiways, from OSM) and
KEWR_runways.json (runway centrelines, fitted from real traffic); neither is
changed. Each runway becomes a chain of nodes along its centreline:
- its two ends ("RWY 4L", "RWY 22R"),
- every taxiway node that joins it (exits, and junctions sitting on it),
- a new node wherever a taxiway edge crosses it with no node there; that
  taxiway edge is split in two at the crossing.
Consecutive runway nodes are joined by runway edges, weighted in meters like
every other edge.

Every node also gets a "kind": taxiway, hold_short, runway_end, runway_exit or
runway_crossing. Saved as KEWR_ground.json (same layout as the taxiway graph,
readable with load_ground_graph) and drawn to KEWR_ground.png.

Run from backend/:  python3 -m app.routing.ground_graph
"""

import json
import math

from .osm_taxiways import (
    DATA_DIR,
    TaxiwayNode,
    edge_points,
    edge_taxiway,
    haversine_m,
    load_saved_graph,
)
from .runways import EXIT_M, load_runways

GROUND_JSON = DATA_DIR / "KEWR_ground.json"
GROUND_PNG = DATA_DIR / "KEWR_ground.png"
# Runway nodes closer together than this along the centreline are merged.
MERGE_M = 5


def _polyline_m(points):
    return sum(haversine_m(*a, *b) for a, b in zip(points, points[1:]))


def _xy(runway, lat, lon):
    from .runways import _to_xy
    from .airport import get_airport

    return _to_xy(get_airport(), lat, lon)


def _crossings(runway, points):
    """[(index, fraction, lat, lon)] where a polyline crosses the runway centreline within its length."""
    found = []
    xy = [_xy(runway, lat, lon) for lat, lon in points]
    for i, ((x1, y1), (x2, y2)) in enumerate(zip(xy, xy[1:])):
        o1, o2 = runway.offset(x1, y1), runway.offset(x2, y2)
        if o1 * o2 >= 0 or o1 == o2:
            continue
        s = o1 / (o1 - o2)
        x, y = x1 + s * (x2 - x1), y1 + s * (y2 - y1)
        if 0 < runway.along(x, y) < runway.length_m:
            lat = points[i][0] + s * (points[i + 1][0] - points[i][0])
            lon = points[i][1] + s * (points[i + 1][1] - points[i][1])
            found.append((i, s, lat, lon))
    return found


def build_ground_graph():
    """(nodes, graph, geometry, kinds, runway_edges) for KEWR."""
    nodes, graph, geometry = load_saved_graph("KEWR", simple=True)
    nodes = {k: TaxiwayNode.from_dict(n.to_dict()) for k, n in nodes.items()}
    graph = {k: dict(v) for k, v in graph.items()}
    geometry = dict(geometry)
    runways = load_runways()
    pairs = {}
    for r in runways.values():
        pairs.setdefault(r.pair, r)  # one Runway per strip; 4L and 22R share a centreline

    kinds = {}
    for k, n in nodes.items():
        kinds[k] = "hold_short" if n.tags.get("aeroway") == "holding_position" else "taxiway"

    on_strip = {pair: [] for pair in pairs}  # pair -> [(along_m, node id)]

    # 1. Taxiway nodes that sit on a runway.
    for pair, r in pairs.items():
        for k, n in nodes.items():
            x, y = _xy(r, n.lat, n.lon)
            if abs(r.offset(x, y)) < EXIT_M and 0 < r.along(x, y) < r.length_m:
                on_strip[pair].append((r.along(x, y), k))
                kinds[k] = "runway_exit"
                n.taxiways = sorted(set(n.taxiways) | {f"RWY {pair}"})

    # 2. Split taxiway edges that cross a runway with no node on it.
    for pair, r in pairs.items():
        joined = {k for _, k in on_strip[pair]}
        done = set()
        for u in list(graph):
            for v in list(graph[u]):
                if (v, u) in done or u in joined or v in joined:
                    continue
                done.add((u, v))
                points = edge_points(nodes, geometry, u, v)
                hits = _crossings(r, points)
                if not hits:
                    continue
                i, s, lat, lon = hits[0]
                cid = f"x_{pair}_{min(u, v)}_{max(u, v)}"
                taxiway = edge_taxiway(nodes, u, v) or nodes[u].name
                nodes[cid] = TaxiwayNode(
                    id=cid, osm_id=None, lat=lat, lon=lon,
                    name=f"{taxiway} x {pair}",
                    taxiways=sorted({taxiway, f"RWY {pair}"}),
                    tags={"aeroway": "runway_crossing", "ref": pair},
                )
                kinds[cid] = "runway_crossing"
                joined.add(cid)  # its two halves end on the centreline; don't split them again
                first = points[: i + 1] + [[lat, lon]]
                second = [[lat, lon]] + points[i + 1:]
                for a, b, piece in ((u, cid, first), (cid, v, second)):
                    length = round(_polyline_m(piece), 2)
                    graph.setdefault(a, {})[b] = length
                    graph.setdefault(b, {})[a] = length
                    geometry[f"{a}|{b}"] = piece
                    geometry[f"{b}|{a}"] = piece[::-1]
                del graph[u][v], graph[v][u]
                geometry.pop(f"{u}|{v}", None)
                geometry.pop(f"{v}|{u}", None)
                x, y = _xy(r, lat, lon)
                on_strip[pair].append((r.along(x, y), cid))

    # 3. Runway ends, then chain everything on each runway in order.
    runway_edges = []
    for pair, r in pairs.items():
        for along, (lat, lon), name in ((0.0, r.threshold, r.name), (r.length_m, r.far_end, _other_end(r))):
            eid = f"rwy_{name}"
            nodes[eid] = TaxiwayNode(
                id=eid, osm_id=None, lat=lat, lon=lon, name=f"RWY {name}",
                taxiways=[f"RWY {pair}"], tags={"aeroway": "runway_end", "ref": name},
            )
            kinds[eid] = "runway_end"
            graph.setdefault(eid, {})
            on_strip[pair].append((along, eid))

        chain = []
        for along, k in sorted(on_strip[pair]):
            if chain and along - chain[-1][0] < MERGE_M and kinds[k] != "runway_end":
                continue
            chain.append((along, k))
        for (_, a), (_, b) in zip(chain, chain[1:]):
            pts = [[nodes[a].lat, nodes[a].lon], [nodes[b].lat, nodes[b].lon]]
            length = round(_polyline_m(pts), 2)
            graph[a][b] = graph[b][a] = length
            geometry[f"{a}|{b}"] = pts
            geometry[f"{b}|{a}"] = pts[::-1]
            runway_edges.append(f"{a}|{b}")

    return nodes, graph, geometry, kinds, runway_edges


def _other_end(runway):
    north, south = runway.pair.split("/")
    return south if runway.name == north else north


def save_ground_graph():
    nodes, graph, geometry, kinds, runway_edges = build_ground_graph()
    data = {
        "nodes": {k: {**n.to_dict(), "kind": kinds[k]} for k, n in nodes.items()},
        "graph": graph,
        "geometry": geometry,
        "runway_edges": runway_edges,
        "source": "KEWR_taxiway_graph_simple.json + KEWR_runways.json (routing/ground_graph.py)",
    }
    with open(GROUND_JSON, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    return nodes, graph, geometry, kinds, runway_edges


def load_ground_graph():
    """(nodes, graph, geometry, kinds, runway_edges) from KEWR_ground.json; builds it if missing."""
    if not GROUND_JSON.exists():
        return save_ground_graph()
    with open(GROUND_JSON, "r", encoding="utf-8") as file:
        data = json.load(file)
    kinds = {k: v.pop("kind") for k, v in data["nodes"].items()}
    nodes = {k: TaxiwayNode.from_dict(v) for k, v in data["nodes"].items()}
    return nodes, data["graph"], data["geometry"], kinds, data["runway_edges"]


STYLE = {
    "taxiway": dict(s=4, color="#3b6fb6", label="taxiway node"),
    "hold_short": dict(s=14, color="#e0a100", marker="s", label="hold-short"),
    "runway_exit": dict(s=30, color="#2e9e5b", label="runway exit / junction on runway"),
    "runway_crossing": dict(s=30, color="#b8322a", marker="D", label="taxiway crosses runway"),
    "runway_end": dict(s=90, color="#222222", marker="s", label="runway end"),
}


def plot_ground_graph(out=GROUND_PNG):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    nodes, graph, geometry, kinds, runway_edges = load_ground_graph()
    on_runway = set(runway_edges) | {f"{e.split('|')[1]}|{e.split('|')[0]}" for e in runway_edges}

    fig, ax = plt.subplots(figsize=(14, 14))
    drawn = set()
    for a, nbrs in graph.items():
        for b in nbrs:
            if (b, a) in drawn:
                continue
            drawn.add((a, b))
            pts = edge_points(nodes, geometry, a, b)
            runway = f"{a}|{b}" in on_runway
            ax.plot([p[1] for p in pts], [p[0] for p in pts],
                    color="#5b6068" if runway else "#9aa0a8",
                    linewidth=6 if runway else 0.9, zorder=0 if runway else 1,
                    solid_capstyle="butt")

    for kind, style in STYLE.items():
        members = [nodes[k] for k, kd in kinds.items() if kd == kind]
        ax.scatter([n.lon for n in members], [n.lat for n in members], zorder=3,
                   edgecolors="white", linewidths=0.5, **{**style, "label": f"{style['label']} ({len(members)})"})

    for k, kd in kinds.items():
        if kd == "runway_end":
            ax.annotate(nodes[k].name, (nodes[k].lon, nodes[k].lat), xytext=(8, -4),
                        textcoords="offset points", fontsize=11, weight="bold")

    by_taxiway = {}
    for k, n in nodes.items():
        if kinds[k] == "taxiway" and "/" not in n.name and not n.name.startswith("way_"):
            by_taxiway.setdefault(n.name, []).append(n)
    for name, members in by_taxiway.items():
        mid = sorted(members, key=lambda n: (n.lon, n.lat))[len(members) // 2]
        ax.annotate(name, (mid.lon, mid.lat), fontsize=7, weight="bold", ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8), zorder=4)

    mean_lat = sum(n.lat for n in nodes.values()) / len(nodes)
    ax.set_aspect(1 / math.cos(math.radians(mean_lat)))
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    edges = sum(len(v) for v in graph.values())
    ax.set_title(f"KEWR ground — {len(nodes)} nodes, {edges} directed edges "
                 f"(taxiways + runways 4L/22R, 4R/22L)")
    ax.legend(loc="upper right")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


if __name__ == "__main__":
    nodes, graph, _, kinds, runway_edges = save_ground_graph()
    from collections import Counter

    print(f"KEWR ground: {len(nodes)} nodes, {sum(len(v) for v in graph.values())} directed edges, "
          f"{len(runway_edges)} runway segments")
    print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(Counter(kinds.values()).items())))
    print(f"Saved {GROUND_JSON}")
    print(f"Saved {plot_ground_graph()}")
