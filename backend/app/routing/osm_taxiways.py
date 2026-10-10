# backend/app/routing/osm_taxiways.py

"""Build a taxiway graph from OpenStreetMap aeroway=taxiway ways.

OSM nodes rarely carry names, so each node's name comes from the taxiways it
sits on: "A" for a point on taxiway A, "A/B" where A and B meet. Edges link
consecutive nodes along each way and are weighted by distance in meters, in
the same {node: {neighbor: cost}} shape that routing.astar expects.
"""

import json
import math
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path


OVERPASS_URL = "https://overpass-api.de/api/interpreter"

DATA_DIR = (
    Path(__file__).resolve().parents[1]
    / "simulation"
    / "data"
    / "osm"
)

QUERY = """
[out:json][timeout:90];
wr["aeroway"="aerodrome"]["icao"="{icao}"];
map_to_area->.airport;
way["aeroway"="taxiway"](area.airport);
(._;>;);
out body;
"""


@dataclass
class TaxiwayNode:
    """One OSM point on the taxiway network."""

    id: str
    osm_id: int
    lat: float
    lon: float
    name: str
    taxiways: list[str] = field(default_factory=list)
    tags: dict = field(default_factory=dict)

    @property
    def is_junction(self):
        """True where two or more named taxiways meet."""
        return "/" in self.name

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


def fetch_taxiways(icao, use_cache=True):
    """Return the raw Overpass JSON for all taxiways at the airport, cached on disk."""
    icao = icao.upper()
    cache = DATA_DIR / f"{icao}_taxiways_raw.json"

    if use_cache and cache.exists():
        with open(cache, "r", encoding="utf-8") as file:
            return json.load(file)

    body = urllib.parse.urlencode({"data": QUERY.format(icao=icao)}).encode()
    request = urllib.request.Request(
        OVERPASS_URL,
        data=body,
        headers={"User-Agent": "ATC-Software/0.1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.load(response)

    if not any(e["type"] == "way" for e in data["elements"]):
        raise ValueError(f"No aeroway=taxiway ways found for {icao}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as file:
        json.dump(data, file)

    return data


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def snap_to_nearest_node(nodes, lat, lon, max_distance_m=None):
    """Return (TaxiwayNode, distance_m) for the node closest to lat/lon.

    Returns None if max_distance_m is given and nothing is that close,
    e.g. for an aircraft parked at a gate or still in the air.
    """
    best, best_dist = None, float("inf")
    for node in nodes.values():
        dist = haversine_m(lat, lon, node.lat, node.lon)
        if dist < best_dist:
            best, best_dist = node, dist

    if best is None or (max_distance_m is not None and best_dist > max_distance_m):
        return None
    return best, best_dist


def edge_points(nodes, geometry, a, b):
    """[lat, lon] polyline of edge a -> b: its stored geometry, or a straight line."""
    if geometry and f"{a}|{b}" in geometry:
        return geometry[f"{a}|{b}"]
    return [[nodes[a].lat, nodes[a].lon], [nodes[b].lat, nodes[b].lon]]


def snap_to_nearest_edge(nodes, graph, lat, lon, max_distance_m=None, geometry=None):
    """Return (node_a, node_b, distance_m, t) for the edge closest to lat/lon.

    t is how far along a -> b the point projects, from 0 (at a) to 1 (at b),
    measured along the edge's geometry when one is given (simplified graph).
    Better than snap_to_nearest_node on long straight taxiways, where OSM
    nodes can be hundreds of meters apart. Returns None if max_distance_m is
    given and nothing is that close.
    """
    # Flat x/y in meters around the point; fine over an airport's few km.
    m_per_deg_lat = 111_320
    m_per_deg_lon = 111_320 * math.cos(math.radians(lat))

    best, best_dist = None, float("inf")
    seen = set()
    for a, neighbors in graph.items():
        for b in neighbors:
            if (b, a) in seen:
                continue
            seen.add((a, b))
            pts = [
                ((p_lon - lon) * m_per_deg_lon, (p_lat - lat) * m_per_deg_lat)
                for p_lat, p_lon in edge_points(nodes, geometry, a, b)
            ]

            edge_dist, along, total = float("inf"), 0.0, 0.0
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                dx, dy = x2 - x1, y2 - y1
                length_sq = dx * dx + dy * dy
                s = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_sq))
                dist = math.hypot(x1 + s * dx, y1 + s * dy)
                if dist < edge_dist:
                    edge_dist, along = dist, total + s * math.sqrt(length_sq)
                total += math.sqrt(length_sq)

            if edge_dist < best_dist:
                t = along / total if total else 0.0
                best, best_dist = (nodes[a], nodes[b], edge_dist, t), edge_dist

    if best is None or (max_distance_m is not None and best_dist > max_distance_m):
        return None
    return best


def edge_taxiway(nodes, a, b):
    """The named taxiway edge a-b runs along, or None for unnamed connectors."""
    shared = [
        t for t in nodes[a].taxiways
        if t in nodes[b].taxiways and not t.startswith("way_")
    ]
    return shared[0] if shared else None


def taxiway_sequence(nodes, route):
    """Collapse a node route into the taxiway names it uses, e.g. ['B', 'AA']."""
    names = []
    for a, b in zip(route, route[1:]):
        name = edge_taxiway(nodes, a, b)
        if name and (not names or names[-1] != name):
            names.append(name)
    return names


def taxiway_label(tags, way_id):
    """Best human-readable name for a taxiway way."""
    return tags.get("ref") or tags.get("name") or f"way_{way_id}"


def build_graph(data):
    """Turn Overpass JSON into (nodes, graph).

    nodes: {node_id: TaxiwayNode}
    graph: {node_id: {neighbor_id: distance_m}}
    """
    osm_nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"] if e["type"] == "way"]

    taxiways_at = {}
    graph = {}

    for way in ways:
        tags = way.get("tags", {})
        label = taxiway_label(tags, way["id"])
        refs = [r for r in way["nodes"] if r in osm_nodes]
        oneway = tags.get("oneway") in ("yes", "1", "true")

        for ref in refs:
            taxiways_at.setdefault(ref, set()).add(label)
            graph.setdefault(str(ref), {})

        for a, b in zip(refs, refs[1:]):
            na, nb = osm_nodes[a], osm_nodes[b]
            dist = round(haversine_m(na["lat"], na["lon"], nb["lat"], nb["lon"]), 2)

            graph[str(a)][str(b)] = dist
            if not oneway:
                graph[str(b)][str(a)] = dist

    nodes = {}
    for ref, labels in taxiways_at.items():
        n = osm_nodes[ref]
        node_tags = n.get("tags", {})
        taxiways = sorted(labels)
        # About 40% of KEWR taxiway ways have no ref in OSM; leave those out
        # of the name unless the node sits only on unnamed ways.
        named = [t for t in taxiways if not t.startswith("way_")] or taxiways
        nodes[str(ref)] = TaxiwayNode(
            id=str(ref),
            osm_id=ref,
            lat=n["lat"],
            lon=n["lon"],
            # A named node (e.g. a holding position) keeps its own name.
            name=node_tags.get("ref") or node_tags.get("name") or "/".join(named),
            taxiways=taxiways,
            tags=node_tags,
        )

    return nodes, graph


def simplify_graph(nodes, graph):
    """Collapse chains of shape points into single edges.

    Most OSM nodes only trace curves. Keep a node only if it is an
    intersection (3+ neighbours), a dead end (1 neighbour), or tagged in OSM
    (hold-short points), and join the kept nodes with one edge per chain.

    Returns (nodes, graph, geometry): edge weights are the summed lengths in
    meters, and geometry["a|b"] is the [lat, lon] polyline of edge a -> b so
    curves can still be drawn and snapped to.
    """
    neighbours = {k: set() for k in graph}
    for a, nbrs in graph.items():
        for b in nbrs:
            neighbours[a].add(b)
            neighbours[b].add(a)

    def passes_through(v):
        # A shape point can only be merged away if traffic flows straight
        # through it the same way in both hops (matters for one-way ways).
        u, w = neighbours[v]
        forward = v in graph[u], w in graph[v]
        backward = v in graph[w], u in graph[v]
        return forward[0] == forward[1] and backward[0] == backward[1] and (forward[0] or backward[0])

    keep = {
        v for v in graph
        if len(neighbours[v]) != 2 or nodes[v].tags or not passes_through(v)
    }

    while True:
        new_graph = {k: {} for k in keep}
        geometry = {}
        visited = set()
        for a in keep:
            for b in graph[a]:
                path, cost = [a, b], graph[a][b]
                while path[-1] not in keep:
                    v = path[-1]
                    nxt = next(n for n in graph[v] if n != path[-2])
                    cost += graph[v][nxt]
                    path.append(nxt)
                visited.update(path[1:-1])
                end = path[-1]
                if end == a:
                    continue  # chain loops back to where it started; no use for routing
                if end not in new_graph[a] or cost < new_graph[a][end]:
                    new_graph[a][end] = round(cost, 2)
                    geometry[f"{a}|{end}"] = [[nodes[n].lat, nodes[n].lon] for n in path]

        # A closed loop of shape points has nothing to start a walk from; keep
        # one node of it and walk again.
        leftover = set(graph) - keep - visited
        if not leftover:
            break
        keep.add(min(leftover))

    return {k: nodes[k] for k in keep}, new_graph, geometry


def plot_taxiway_graph(nodes, graph, out, title=None, geometry=None):
    """Draw every edge and node, highlight junctions, and label each taxiway once."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(14, 14))

    drawn = set()
    for a, neighbors in graph.items():
        for b in neighbors:
            if (b, a) in drawn:
                continue
            drawn.add((a, b))
            pts = edge_points(nodes, geometry, a, b)
            ax.plot([p[1] for p in pts], [p[0] for p in pts], color="#8a8f98", linewidth=0.8, zorder=1)

    plain = [n for n in nodes.values() if not n.is_junction]
    junctions = [n for n in nodes.values() if n.is_junction]
    ax.scatter([n.lon for n in plain], [n.lat for n in plain], s=3, color="#3b6fb6", zorder=2, label="node")
    ax.scatter([n.lon for n in junctions], [n.lat for n in junctions], s=14, color="#d9622b", zorder=3, label="junction")

    # Label each named taxiway at its middle point.
    by_taxiway = {}
    for n in plain:
        if not n.name.startswith("way_"):
            by_taxiway.setdefault(n.name, []).append(n)
    for name, members in by_taxiway.items():
        mid = sorted(members, key=lambda n: (n.lon, n.lat))[len(members) // 2]
        ax.annotate(name, (mid.lon, mid.lat), fontsize=7, weight="bold", ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8), zorder=4)

    # One degree of longitude is shorter than one of latitude away from the equator.
    mean_lat = sum(n.lat for n in nodes.values()) / len(nodes)
    ax.set_aspect(1 / math.cos(math.radians(mean_lat)))
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(title or f"Taxiway graph — {len(nodes)} nodes, {len(junctions)} junctions")
    ax.legend(loc="upper right")

    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def graph_path(icao, simple):
    return DATA_DIR / f"{icao.upper()}_taxiway_graph{'_simple' if simple else ''}.json"


def load_taxiway_graph(icao="KEWR", use_cache=True):
    """Fetch, build, and save the taxiway graph for an airport.

    Writes both the full graph (every OSM node) and the simplified one
    (intersections, dead ends and hold-short points only). Returns the full one.
    """
    nodes, graph = build_graph(fetch_taxiways(icao, use_cache))
    simple_nodes, simple_graph, geometry = simplify_graph(nodes, graph)

    saves = [
        (graph_path(icao, simple=False), nodes, graph, None),
        (graph_path(icao, simple=True), simple_nodes, simple_graph, geometry),
    ]
    for out, n, g, geo in saves:
        data = {"nodes": {k: node.to_dict() for k, node in n.items()}, "graph": g}
        if geo is not None:
            data["geometry"] = geo
        with open(out, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    return nodes, graph


def load_saved_graph(icao="KEWR", simple=True):
    """Read a graph written by load_taxiway_graph(): (nodes, graph, geometry).

    geometry is None for the full graph, whose edges are already straight.
    """
    with open(graph_path(icao, simple), "r", encoding="utf-8") as file:
        data = json.load(file)
    nodes = {k: TaxiwayNode.from_dict(v) for k, v in data["nodes"].items()}
    return nodes, data["graph"], data.get("geometry")


if __name__ == "__main__":
    import sys

    icao = sys.argv[1] if len(sys.argv) > 1 else "KEWR"
    load_taxiway_graph(icao)

    for simple in (False, True):
        nodes, graph, geometry = load_saved_graph(icao, simple)
        label = "simple" if simple else "full"
        edges = sum(len(v) for v in graph.values())
        junctions = [n for n in nodes.values() if n.is_junction]
        print(f"{icao} {label}: {len(nodes)} nodes, {edges} directed edges, {len(junctions)} junctions")

        image = plot_taxiway_graph(
            nodes,
            graph,
            graph_path(icao, simple).with_suffix(".png"),
            title=f"{icao.upper()} taxiways ({label}) — {len(nodes)} nodes, {len(junctions)} junctions",
            geometry=geometry,
        )
        print(f"  saved {image}")
