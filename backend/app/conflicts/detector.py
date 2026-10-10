# backend/app/conflicts/detector.py

"""Conflicts in the live simulation, from where everything is right now.

- Runway incursion (critical, location = runway pair like "4L/22R"): an
  aircraft or vehicle on a runway while someone else's landing or takeoff
  window on it is open. aircraft_ids is [the intruder, the aircraft using the
  runway]; the latter may still be on final and not in state yet.
- Loss of separation (location = the first one's position): two movers closer
  than WARNING_M, critical under CRITICAL_M, when at least one of them is off
  its plan (holding, waiting for a route, or a vehicle). Two aircraft both on
  planned routes are conflict-free by construction, and the simplified graph
  has nodes 10 m apart, so their distance alone would raise false alarms.
"""

import math

from ..models import Conflict
from ..routing.airport import DT
from ..routing.graph import ground_geometry, ground_nodes
from ..routing.osm_taxiways import edge_points, haversine_m
from ..routing.runways import ZONE_M, _to_xy

WARNING_M = 40
CRITICAL_M = 20


def _latlon(track, t):
    """(lat, lon) at time t, along the drawn edge geometry like the frontend."""
    d = track.dist(t)
    i = track.index(d)
    if i >= len(track.route) - 1:
        n = ground_nodes[track.route[-1]]
        return n.lat, n.lon
    u, v = track.route[i], track.route[i + 1]
    span = track.cum[i + 1] - track.cum[i]
    frac = (d - track.cum[i]) / span if span > 0 else 0.0
    pts = edge_points(ground_nodes, ground_geometry, u, v)
    lengths = [haversine_m(*a, *b) for a, b in zip(pts, pts[1:])]
    left = frac * sum(lengths)
    for (a, b), seg in zip(zip(pts, pts[1:]), lengths):
        if left <= seg and seg > 0:
            f = left / seg
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        left -= seg
    return tuple(pts[-1])


def detect_conflicts(sim):
    t = sim.now
    step = t / DT
    movers = [(a.id, sim.tracks[a.id]) for a in sim.state.aircraft if a.status != "arrived"]
    movers += [(v.id, sim.tracks[v.id]) for v in sim.state.vehicles]
    xy = {mid: _to_xy(sim.ap, *_latlon(tr, t)) for mid, tr in movers}

    conflicts = []
    for mid, _ in movers:
        for pair, runway in sim.gate.runways.items():
            if not runway.on_runway_xy(*xy[mid], margin=ZONE_M / 2):
                continue
            users = sorted({
                label for w0, w1, label, kind in sim.gate.windows[pair]
                if w0 <= step < w1 and label != mid
                # Lining up behind a departure that is rolling is normal.
                and not (kind == "takeoff" and sim.departs.get(mid) == pair)
            })
            for user in users:
                conflicts.append(Conflict(aircraft_ids=[mid, user], location=pair, severity="critical"))

    planned = {a.id for a in sim.state.aircraft if a.status == "taxiing"}
    ids = [mid for mid, _ in movers]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a in planned and b in planned:
                continue
            gap = math.dist(xy[a], xy[b])
            if gap < WARNING_M:
                position = next(x.position for x in sim.state.aircraft + sim.state.vehicles if x.id == a)
                conflicts.append(Conflict(aircraft_ids=[a, b], location=position,
                                          severity="critical" if gap < CRITICAL_M else "warning"))
    return conflicts
