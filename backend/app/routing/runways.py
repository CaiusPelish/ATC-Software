# backend/app/routing/runways.py

"""KEWR runways 4L/22R and 4R/22L, fitted from real landing and takeoff rolls.

The OSM graph only has taxiways, so the runway centrelines come from the Amelia
recording: every ground point above 60 kt lies on a runway. The two parallel
clusters are fitted with a straight line each, and each runway's ends are
where its outermost exit taxiways meet it. The result is cached in
KEWR_runways.json next to the taxiway graph.

Also here: which taxiway nodes are runway exits, which nodes and edges are on
the runway (a crossing), and RunwayGate, the edge_allowed hook that keeps
taxiing aircraft off a runway while an arrival is on short final or rolling out.
"""

import json
import math
from dataclasses import dataclass

from .airport import get_airport
from .osm_taxiways import DATA_DIR, edge_points

RUNWAYS_JSON = DATA_DIR / "KEWR_runways.json"
TRAFFIC_CSV = DATA_DIR.parent / "kewr_194_2h.csv"

# Anything closer than this to a centreline is on the runway (150 ft wide, hold-shorts ~80 m out).
ZONE_M = 45
# Taxiway nodes this close to a centreline count as joining the runway.
EXIT_M = 15


@dataclass
class Runway:
    name: str            # "22L"
    pair: str            # "4R/22L"
    threshold: tuple     # (lat, lon) of the landing end for `name`
    far_end: tuple       # (lat, lon) of the other end
    heading_true: float  # degrees, direction of travel landing on `name`

    def __post_init__(self):
        ap = get_airport()
        self.threshold = tuple(float(c) for c in self.threshold)
        self.far_end = tuple(float(c) for c in self.far_end)
        self._a = _to_xy(ap, *self.threshold)
        self._b = _to_xy(ap, *self.far_end)
        dx, dy = self._b[0] - self._a[0], self._b[1] - self._a[1]
        self.length_m = math.hypot(dx, dy)
        self._u = (dx / self.length_m, dy / self.length_m)

    def along(self, x, y):
        """Meters from the threshold along the landing direction."""
        return (x - self._a[0]) * self._u[0] + (y - self._a[1]) * self._u[1]

    def offset(self, x, y):
        """Signed meters from the centreline; positive is right of the landing direction."""
        return (x - self._a[0]) * self._u[1] - (y - self._a[1]) * self._u[0]

    def point(self, along_m):
        """(lat, lon) on the extended centreline; negative is before the threshold."""
        ap = get_airport()
        x = self._a[0] + along_m * self._u[0]
        y = self._a[1] + along_m * self._u[1]
        return _to_latlon(ap, x, y)

    def on_runway_xy(self, x, y, margin=ZONE_M):
        return abs(self.offset(x, y)) < margin and -margin < self.along(x, y) < self.length_m + margin


def _to_xy(ap, lat, lon):
    lat0, lon0 = ap.origin
    return (lon - lon0) * 111_320 * math.cos(math.radians(lat0)), (lat - lat0) * 111_320


def _to_latlon(ap, x, y):
    lat0, lon0 = ap.origin
    return lat0 + y / 111_320, lon0 + x / (111_320 * math.cos(math.radians(lat0)))


def fit_runways(csv_path=TRAFFIC_CSV):
    """Fit both parallel runways from the recording; returns the JSON-ready dict."""
    import numpy as np
    import pandas as pd

    ap = get_airport()
    df = pd.read_csv(csv_path)
    fast = df[(df["Altitude"] < 20) & (df["Speed"] > 60) & (df["Type"] == 0)]
    x, y = _to_xy(ap, fast["Lat"].to_numpy(), fast["Lon"].to_numpy())

    rad = np.radians(fast["Heading"].to_numpy())
    heading = math.degrees(math.atan2(np.sin(rad).mean(), np.cos(rad).mean()))
    # Fold to the north-east direction so both flows land on one axis.
    heading = heading % 180
    h = math.radians(heading)
    ux, uy = math.sin(h), math.cos(h)
    along = x * ux + y * uy
    left = -x * uy + y * ux  # left of north-east travel, i.e. north-west

    order = np.sort(left)
    split = order[np.argmax(np.diff(order))] + np.diff(order).max() / 2
    runways = {}
    for side, mask in (("L", left >= split), ("R", left < split)):
        slope, icept = np.polyfit(along[mask], left[mask], 1)

        def to_xy(a):
            off = icept + slope * a
            return a * ux - off * uy, a * uy + off * ux

        # The runway ends where its outermost taxiways join it.
        joins = []
        for node, (nx, ny) in ap.xy.items():
            a = nx * ux + ny * uy
            if abs(-nx * uy + ny * ux - (icept + slope * a)) < EXIT_M:
                joins.append(a)
        lo, hi = min(joins) - 40, max(joins) + 40
        sw = _to_latlon(ap, *to_xy(lo))
        ne = _to_latlon(ap, *to_xy(hi))
        bearing = math.degrees(math.atan2(*(np.subtract(to_xy(hi), to_xy(lo))))) % 360
        runways[f"4{side}/22{'R' if side == 'L' else 'L'}"] = {
            "sw_end": list(sw), "ne_end": list(ne), "bearing_ne": round(bearing, 2),
        }
    return {"source": "fitted from kewr_194_2h.csv ground rolls > 60 kt", "runways": runways}


def load_runways(refit=False):
    """{name: Runway} for every landing direction: 4L, 22R, 4R, 22L."""
    if refit or not RUNWAYS_JSON.exists():
        data = fit_runways()
        with open(RUNWAYS_JSON, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
    else:
        with open(RUNWAYS_JSON, "r", encoding="utf-8") as file:
            data = json.load(file)

    out = {}
    for pair, r in data["runways"].items():
        north, south = pair.split("/")
        sw, ne, brg = tuple(r["sw_end"]), tuple(r["ne_end"]), r["bearing_ne"]
        out[north] = Runway(north, pair, sw, ne, brg)
        out[south] = Runway(south, pair, ne, sw, (brg + 180) % 360)
    return out


def _edge_xy(ap, u, v):
    return [_to_xy(ap, lat, lon) for lat, lon in edge_points(ap.nodes, ap.geometry, u, v)]


def on_runway_nodes(runway):
    ap = get_airport()
    return {n for n, (x, y) in ap.xy.items() if runway.on_runway_xy(x, y)}


def edge_enters(runway, u, v):
    """True if taxiing u -> v puts the aircraft on the runway (ends on it or crosses it)."""
    ap = get_airport()
    x, y = ap.xy[u]
    if runway.on_runway_xy(x, y):
        return False  # leaving or moving along it; leaving is always allowed
    pts = _edge_xy(ap, u, v)
    if any(runway.on_runway_xy(px, py) for px, py in pts):
        return True
    offs = [runway.offset(px, py) for px, py in pts]
    inside = [0 < runway.along(px, py) < runway.length_m for px, py in pts]
    return any(a * b < 0 and i for a, b, i in zip(offs, offs[1:], inside))


def exits(runway, side=None):
    """[(along_m, node)] of taxiway nodes joining the runway, sorted by distance from the threshold.

    side "left"/"right" keeps exits whose taxiway leaves to that side of the
    landing direction.
    """
    ap = get_airport()
    found = []
    for node, (x, y) in ap.xy.items():
        if abs(runway.offset(x, y)) >= EXIT_M or not 0 < runway.along(x, y) < runway.length_m:
            continue
        away = [runway.offset(*ap.xy[v]) for v in ap.graph[node]]
        if not any(abs(o) > EXIT_M for o in away):
            continue  # a stub that never leaves the runway
        if side:
            sign = 1 if side == "right" else -1
            if not any(o * sign > EXIT_M for o in away):
                continue
        found.append((runway.along(x, y), node))
    return sorted(found)


class RunwayGate:
    """edge_allowed hook: keeps taxiing aircraft off runways while they're in use.

    Each runway has busy windows in timesteps, e.g. an arrival from short final
    until it has vacated, or a departure's takeoff roll. Entering a runway (or
    waiting on one) is allowed only if the runway stays free until the
    aircraft can be clear again. Leaving a runway is always allowed.

    Called directly it applies every window. for_aircraft(aid, departs_from)
    gives one aircraft its own hook that ignores its own windows and, for a
    departure, other takeoffs from its own runway: lining up while the
    aircraft ahead rolls is normal; the takeoff order comes from its slot.
    rolling_out=pair lets an arrival that starts its taxi still on the runway
    it landed on keep moving along it to an exit (but not wait there), as
    runway_conflicts does; the landing behind it was cleared knowing it would.

    crossings_overlap=True lets several aircraft cross a runway at once, as
    controllers clear a line of crossers together: a crossing then ignores
    other crossings, but still waits for landings and takeoffs, and a takeoff
    still waits for every crossing.
    """

    def __init__(self, runways, airport=None, crossings_overlap=False):
        ap = self.airport = airport or get_airport()
        self.crossings_overlap = crossings_overlap
        self.runways = {r.pair: r for r in runways}
        self.windows = {pair: [] for pair in self.runways}
        self.on = {pair: on_runway_nodes(r) for pair, r in self.runways.items()}
        self._enters = {}
        for pair, r in self.runways.items():
            for u, nbrs in ap.graph.items():
                for v in nbrs:
                    if edge_enters(r, u, v):
                        self._enters.setdefault((u, v), []).append(pair)

    def add_window(self, pair, t0, t1, label="", kind="landing"):
        self.windows[pair].append((t0, t1, label, kind))

    def ignores(self, kind, pair, departs_from=None):
        """True if a window of this kind on pair doesn't block an aircraft departing from departs_from."""
        if pair == departs_from:
            return kind == "takeoff"
        return self.crossings_overlap and kind == "crossing"

    def busy(self, pair, t0, t1, aid=None, departs_from=None):
        return any(
            w0 < t1 and t0 < w1
            for w0, w1, label, kind in self.windows[pair]
            if label != aid and not self.ignores(kind, pair, departs_from)
        )

    def runways_touched(self, u, v):
        """Runways the move u -> v (or a wait, u == v) puts the aircraft on."""
        touched = [pair for pair, nodes in self.on.items() if u in nodes and v in nodes]
        if u != v:
            touched += self._enters.get((u, v), [])
        return touched

    def allowed(self, u, v, t, aid=None, departs_from=None, rolling_out=None):
        pairs = self.runways_touched(u, v)
        if rolling_out and u != v and u in self.on.get(rolling_out, ()):
            pairs = [p for p in pairs if p != rolling_out]
        if not pairs:
            return True
        ap = self.airport
        d = 1 if u == v else ap.steps[u][v]
        # After this move the aircraft still needs its slowest way off the runway.
        clear = max(ap.steps[v].values()) if any(v in self.on[p] for p in pairs) else 0
        return not any(self.busy(pair, t, t + d + clear, aid, departs_from) for pair in pairs)

    def occupancy(self, path):
        """[(pair, t0, t1)]: steps [t0, t1) the path is on each runway, crossing, lining up or rolling out."""
        spans = []
        for (u, t0), (v, t1) in zip(path, path[1:]):
            for pair in set(self.runways_touched(u, v)) | {p for p, nodes in self.on.items() if u in nodes}:
                if spans and spans[-1][0] == pair and spans[-1][2] >= t0:
                    spans[-1] = (pair, spans[-1][1], max(spans[-1][2], t1))
                else:
                    spans.append((pair, t0, t1))
        node, t = path[-1]
        for pair, nodes in self.on.items():
            if node in nodes:  # still on the runway when the path ends
                spans.append((pair, t, t + 1))
        return spans

    def claim(self, aid, path, departs_from=None):
        """Flag every runway the path uses as in use for those steps, so nobody planned later enters it.

        On departs_from the window is a "takeoff" (lining up), which still lets
        the next departure off that runway line up behind it, as busy() does
        for real takeoffs; anywhere else it is a "crossing".
        """
        claimed = self.occupancy(path)
        for pair, t0, t1 in claimed:
            self.add_window(pair, t0, t1, aid, "takeoff" if pair == departs_from else "crossing")
        return claimed

    def in_use(self, pair, t):
        """(label, kind) of the window holding the runway at step t, landings first, or None."""
        found = [(w0, label, kind) for w0, w1, label, kind in self.windows[pair] if w0 <= t < w1]
        rank = {"landing": 0, "takeoff": 1}
        found.sort(key=lambda w: (rank.get(w[2], 2), w[0]))
        return (found[0][1], found[0][2]) if found else None

    def __call__(self, u, v, t):
        return self.allowed(u, v, t)

    def for_aircraft(self, aid, departs_from=None, rolling_out=None):
        return lambda u, v, t: self.allowed(u, v, t, aid, departs_from, rolling_out)


def runway_conflicts(gate, paths, departs_from=None):
    """[(aid, pair, t)] for every step an aircraft is on a runway during one of its busy windows.

    An aircraft whose path starts on a runway (an arrival at its exit) is
    still rolling out until it first leaves that runway, so that part is
    skipped. departs_from {aid: pair} exempts departures lining up on their
    own runway from other takeoff windows there, as RunwayGate.for_aircraft does;
    with gate.crossings_overlap, crossings don't count against each other.
    """
    ap = get_airport()
    departs_from = departs_from or {}
    found = []
    for aid, path in paths.items():
        rolling_out = {pair for pair, nodes in gate.on.items() if path[0][0] in nodes}
        for (u, t0), (v, t1) in zip(path, path[1:]):
            rolling_out = {pair for pair in rolling_out if u in gate.on[pair]}
            for pair, r in gate.runways.items():
                if pair in rolling_out:
                    continue
                # Sample the move once per step and see if the aircraft is on the runway then.
                pts = _edge_xy(ap, u, v) if u != v else [ap.xy[u]]
                for k in range(t1 - t0):
                    px, py = pts[min(len(pts) - 1, round(k / max(1, t1 - t0) * (len(pts) - 1)))]
                    t = t0 + k
                    if r.on_runway_xy(px, py, margin=ZONE_M / 2) and any(
                        w0 <= t < w1 and lbl != aid
                        and not gate.ignores(kind, pair, departs_from.get(aid))
                        for w0, w1, lbl, kind in gate.windows[pair]
                    ):
                        found.append((aid, pair, t))
    return found
