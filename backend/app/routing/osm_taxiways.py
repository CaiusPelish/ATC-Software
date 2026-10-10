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
    Path(__file__).resolve().parents[3]
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


def plot_taxiway_graph(nodes, graph, out, title=None):
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
            na, nb = nodes[a], nodes[b]
            ax.plot([na.lon, nb.lon], [na.lat, nb.lat], color="#8a8f98", linewidth=0.8, zorder=1)

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


def load_taxiway_graph(icao="KEWR", use_cache=True):
    """Fetch, build, and save the taxiway graph for an airport."""
    nodes, graph = build_graph(fetch_taxiways(icao, use_cache))

    out = DATA_DIR / f"{icao.upper()}_taxiway_graph.json"
    with open(out, "w", encoding="utf-8") as file:
        json.dump(
            {"nodes": {k: n.to_dict() for k, n in nodes.items()}, "graph": graph},
            file,
            indent=2,
        )

    return nodes, graph


if __name__ == "__main__":
    import sys

    icao = sys.argv[1] if len(sys.argv) > 1 else "KEWR"
    nodes, graph = load_taxiway_graph(icao)

    edges = sum(len(v) for v in graph.values())
    junctions = [n for n in nodes.values() if n.is_junction]
    print(f"{icao}: {len(nodes)} nodes, {edges} directed edges, {len(junctions)} junctions")
    for n in junctions[:10]:
        print(f"  {n.name:<12} ({n.lat:.6f}, {n.lon:.6f})")

    image = plot_taxiway_graph(
        nodes,
        graph,
        DATA_DIR / f"{icao.upper()}_taxiway_graph.png",
        title=f"{icao.upper()} taxiways — {len(nodes)} nodes, {len(junctions)} junctions",
    )
    print(f"Saved {image}")
