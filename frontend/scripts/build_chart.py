"""Build the EWR ground chart assets for the /sim page.

Outputs (paths relative to frontend/):
  public/kewr-chart.jpg       dark-graded aerial backdrop (USGS imagery, public domain)
  app/data/kewr-chart.json    taxiways, runways and nodes projected into the image's pixel space

Usage (from frontend/):
  git show origin/backend:backend/app/simulation/data/osm/KEWR_ground.json > ground.json
  python scripts/build_chart.py ground.json

Requires: pip install --user pillow
The ground graph comes from the backend (routing/ground_graph.py builds KEWR_ground.json):
nodes, adjacency, per-edge curve geometry and which edges lie on a runway.
Runway outlines (width, ends) are fetched from OpenStreetMap.
"""

import io
import json
import math
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageEnhance

ZOOM = 16
TILE = 256
MARGIN_DEG = 0.004
ASPECT = 16 / 10  # wide, so object-cover style cropping loses little on most screens
TILE_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "HoldShort-hackathon/0.1"
NAVY = (18, 26, 38)  # close to --background, baked into the image grade

ROOT = Path(__file__).resolve().parent.parent


def world_px(lat, lon, zoom=ZOOM):
    """Web Mercator: lat/lon -> global pixel coordinates at this zoom."""
    scale = TILE * 2**zoom
    x = (lon + 180) / 360 * scale
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale
    return x, y


def meters_per_px(lat, zoom=ZOOM):
    return 156543.03392 * math.cos(math.radians(lat)) / 2**zoom


def haversine_m(a, b):
    r = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def http_get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_runways(bbox):
    s, w, n, e = bbox
    query = f'[out:json][timeout:60];way["aeroway"="runway"]({s},{w},{n},{e});out geom tags;'
    body = urllib.parse.urlencode({"data": query}).encode()
    elements = json.loads(http_get(OVERPASS_URL, body))["elements"]

    # OSM splits each runway into several ways; merge by ref and order along the runway axis.
    by_ref = defaultdict(list)
    widths = {}
    for el in elements:
        ref = el["tags"].get("ref", "?")
        by_ref[ref] += [(p["lat"], p["lon"]) for p in el["geometry"]]
        widths[ref] = float(el["tags"].get("width", 45))
    runways = []
    for ref, pts in by_ref.items():
        a = max(pts, key=lambda p: haversine_m(p, pts[0]))
        b = max(pts, key=lambda p: haversine_m(p, a))
        runways.append({"ref": ref, "ends": [a, b], "width_m": widths[ref]})
    return runways


def build_backdrop(bbox):
    s, w, n, e = bbox
    x0, y0 = world_px(n, w)
    x1, y1 = world_px(s, e)
    # Grow the shorter side so the image has the target aspect ratio.
    cx, cy, width, height = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    if width / height < ASPECT:
        width = height * ASPECT
    else:
        height = width / ASPECT
    ox, oy = round(cx - width / 2), round(cy - height / 2)
    width, height = round(width), round(height)

    canvas = Image.new("RGB", (width, height))
    for ty in range(oy // TILE, (oy + height) // TILE + 1):
        for tx in range(ox // TILE, (ox + width) // TILE + 1):
            tile = Image.open(io.BytesIO(http_get(TILE_URL.format(z=ZOOM, x=tx, y=ty)))).convert("RGB")
            canvas.paste(tile, (tx * TILE - ox, ty * TILE - oy))

    # Night grade: mute colour, darken, pull toward the app's navy background.
    graded = ImageEnhance.Color(canvas).enhance(0.35)
    graded = ImageEnhance.Brightness(graded).enhance(0.55)
    graded = ImageEnhance.Contrast(graded).enhance(1.15)
    graded = Image.blend(graded, Image.new("RGB", graded.size, NAVY), 0.45)
    return graded, {"zoom": ZOOM, "originX": ox, "originY": oy, "width": width, "height": height}


def runway_end_labels(ref, pa, pb):
    """Runway 04L means 'heading 040 degrees', so its number is painted at the end
    you start from: the end where the runway points toward 040 degrees."""
    bearing_ab = math.degrees(math.atan2(pb[0] - pa[0], -(pb[1] - pa[1]))) % 360
    labels = []
    for name in ref.split("/"):
        heading = int("".join(ch for ch in name if ch.isdigit())) * 10
        diff = abs((bearing_ab - heading + 180) % 360 - 180)
        start, other = (pa, pb) if diff < 90 else (pb, pa)
        labels.append({"label": name, "at": start, "toward": other})
    return labels


def main(graph_path):
    graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    nodes, adj = graph["nodes"], graph["graph"]

    geometry = graph["geometry"]  # "a|b" -> [[lat, lon], ...] along the real taxiway curve
    runway_edges = {frozenset(e.split("|")) for e in graph.get("runway_edges", [])}

    lats = [p[0] for line in geometry.values() for p in line] + [n["lat"] for n in nodes.values()]
    lons = [p[1] for line in geometry.values() for p in line] + [n["lon"] for n in nodes.values()]
    bbox = (min(lats) - MARGIN_DEG, min(lons) - MARGIN_DEG, max(lats) + MARGIN_DEG, max(lons) + MARGIN_DEG)

    runways = fetch_runways(bbox)
    image, proj = build_backdrop(bbox)

    def px(lat, lon):
        x, y = world_px(lat, lon)
        return round(x - proj["originX"], 1), round(y - proj["originY"], 1)

    def edge_line(a, b):
        line = geometry.get(f"{a}|{b}") or list(reversed(geometry.get(f"{b}|{a}", [])))
        if not line:
            line = [[nodes[a]["lat"], nodes[a]["lon"]], [nodes[b]["lat"], nodes[b]["lon"]]]
        return [px(lat, lon) for lat, lon in line]

    # Every edge's curve in chart pixels, one direction (a < b), so vehicles follow the real path.
    edges = {f"{a}|{b}": edge_line(a, b) for a, nb in adj.items() for b in nb if a < b}

    # Taxiways: one SVG path per taxiway (named ref, or way id when unnamed). Runway edges are
    # drawn by the runway layer instead.
    lines = defaultdict(list)
    for key_ab, line in edges.items():
        a, b = key_ab.split("|")
        if frozenset((a, b)) in runway_edges:
            continue
        shared = set(nodes[a]["taxiways"]) & set(nodes[b]["taxiways"])
        named = sorted(t for t in shared if not t.startswith("way_") and not t.startswith("RWY"))
        key = named[0] if named else (sorted(shared)[0] if shared else "way_unknown")
        lines[key].append(line)
    mid_lat = (bbox[0] + bbox[2]) / 2
    taxiways = []
    for key, polys in sorted(lines.items()):
        segs = [(p, q) for line in polys for p, q in zip(line, line[1:])]
        # Label goes on the segment closest to the taxiway's middle, like letters on a paper chart.
        mids = [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in segs]
        cx = sum(m[0] for m in mids) / len(mids)
        cy = sum(m[1] for m in mids) / len(mids)
        label = min(mids, key=lambda m: (m[0] - cx) ** 2 + (m[1] - cy) ** 2)
        length_px = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in segs)
        taxiways.append({
            "id": key,
            "name": None if key.startswith("way_") else key,
            "d": "".join("M" + "L".join(f"{x} {y}" for x, y in line) for line in polys),
            "label": [round(label[0], 1), round(label[1], 1)],
            "lengthFt": round(length_px * meters_per_px(mid_lat) * 3.28084 / 100) * 100,
        })

    # Nodes worth showing: hold-short points, runway crossings/exits, intersections and dead ends.
    # Degree-2 taxiway nodes are just where a line changes name, so they stay hidden.
    kinds = {"hold_short": "hold", "runway_crossing": "crossing", "runway_exit": "exit"}
    shown = []
    for nid, n in nodes.items():
        degree = len(adj.get(nid, {}))
        kind = kinds.get(n.get("kind")) or ("end" if degree <= 1 else "intersection")
        if n.get("kind") == "runway_end" or (kind == "intersection" and degree == 2):
            continue  # runway ends are already labelled by the runway layer
        x, y = px(n["lat"], n["lon"])
        shown.append({
            "id": nid,
            "name": n["name"],
            "x": x,
            "y": y,
            "kind": kind,
            "holdType": n["tags"].get("holding_position:type") if kind == "hold" else None,
            "taxiways": sorted(t for t in n["taxiways"] if not t.startswith("way_")),
            "on": sorted(n["taxiways"]),  # every taxiway id, named or not, for focus filtering
        })

    out_runways = []
    for r in runways:
        (a, b) = r["ends"]
        pa, pb = px(*a), px(*b)
        out_runways.append({
            "id": r["ref"],
            "lengthFt": round(haversine_m(a, b) * 3.28084 / 100) * 100,
            "widthPx": round(r["width_m"] / meters_per_px(mid_lat), 1),
            "from": pa,
            "to": pb,
            "ends": runway_end_labels(r["ref"], pa, pb),
        })

    image.save(ROOT / "public" / "kewr-chart.jpg", quality=82, optimize=True)
    data_dir = ROOT / "app" / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "kewr-chart.json").write_text(
        json.dumps(
            {
                "projection": proj,
                "runways": out_runways,
                "taxiways": taxiways,
                "nodes": shown,
                # Every graph node so vehicles from the backend can be placed on any of them
                "positions": {nid: px(n["lat"], n["lon"]) for nid, n in nodes.items()},
                "graph": {nid: sorted(nb) for nid, nb in adj.items()},
                "edges": edges,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"image {proj['width']}x{proj['height']}, {len(out_runways)} runways, {len(taxiways)} taxiways, {len(shown)} nodes")


if __name__ == "__main__":
    main(sys.argv[1])
