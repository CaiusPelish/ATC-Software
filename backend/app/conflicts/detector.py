# backend/app/conflicts/detector.py

"""Conflicts in the live simulation, from where everything is right now, and
warnings, from where everything is going.

Conflicts:
- Runway incursion (critical, location = runway pair like "4L/22R"): an
  aircraft or vehicle on a runway while someone else's landing or takeoff
  window on it is open. aircraft_ids is [the intruder, the aircraft using the
  runway]; the latter may still be on final and not in state yet.
- Loss of separation (location = the first one's position): two movers closer
  than WARNING_M, critical under CRITICAL_M, when at least one of them is off
  its plan (holding, waiting for a route, or a vehicle). Two aircraft both on
  planned routes are conflict-free by construction, and the simplified graph
  has nodes 10 m apart, so their distance alone would raise false alarms.

Warnings:
- Predicted loss of separation (eta = seconds until it happens): a vehicle
  whose track, followed for the next LOOKAHEAD_S, comes within WARNING_M of an
  aircraft or another vehicle following its own. This is what a controller
  sees right after routing a truck across where a plane is going. Once they
  are actually that close it is a conflict instead.
"""

import math

from ..models import CollisionWarning, Conflict
from ..routing.airport import DT
from ..routing.graph import ground_geometry, ground_nodes
from ..routing.osm_taxiways import edge_points, haversine_m
from ..routing.runways import ZONE_M, _to_xy

WARNING_M = 40
CRITICAL_M = 20
LOOKAHEAD_S = 30
# Fine enough that a truck and a rolling-out arrival can't pass through each other between samples.
LOOKAHEAD_STEP_S = 0.5


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


def _max_speed(track, t0, t1):
    """Fastest the track moves anywhere in [t0, t1], in m/s."""
    return max((
        (d1 - d0) / (b - a)
        for (a, d0), (b, d1) in zip(track.points, track.points[1:])
        if b > t0 and a < t1 and b > a
    ), default=0.0)


def _predicted(sim, t, movers, xy, now_close):
    vehicles = {v.id for v in sim.state.vehicles}
    if not vehicles:
        return []
    tracks = dict(movers)
    horizon = t + LOOKAHEAD_S
    times = [t + LOOKAHEAD_STEP_S * k for k in range(1, int(LOOKAHEAD_S / LOOKAHEAD_STEP_S) + 1)]
    vmax = {mid: _max_speed(tr, t, horizon) for mid, tr in movers}
    future = {}

    def path(mid):
        if mid not in future:
            tr = tracks[mid]
            # Standing still for the whole lookahead: no need to walk the geometry.
            future[mid] = ([xy[mid]] * len(times) if vmax[mid] == 0
                           else [_to_xy(sim.ap, *_latlon(tr, s)) for s in times])
        return future[mid]

    warnings = []
    ids = [mid for mid, _ in movers]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a not in vehicles and b not in vehicles or frozenset((a, b)) in now_close:
                continue
            # Too far apart to close the gap within the lookahead even head-on.
            if math.dist(xy[a], xy[b]) - (vmax[a] + vmax[b]) * LOOKAHEAD_S >= WARNING_M:
                continue
            for s, pa, pb in zip(times, path(a), path(b)):
                if math.dist(pa, pb) < WARNING_M:
                    # The vehicle first, like the intruder in a runway incursion.
                    vehicle, other = (a, b) if a in vehicles else (b, a)
                    tr = tracks[other]
                    warnings.append(CollisionWarning(
                        aircraft_ids=[vehicle, other],
                        location=tr.route[tr.index(tr.dist(s))],
                        eta=round(s - t, 1)))
                    break
    return warnings


def detect_conflicts(sim):
    """(conflicts happening now, warnings of ones coming up)."""
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
    now_close = set()
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a in planned and b in planned:
                continue
            gap = math.dist(xy[a], xy[b])
            if gap < WARNING_M:
                now_close.add(frozenset((a, b)))
                position = next(x.position for x in sim.state.aircraft + sim.state.vehicles if x.id == a)
                conflicts.append(Conflict(aircraft_ids=[a, b], location=position,
                                          severity="critical" if gap < CRITICAL_M else "warning"))
    return conflicts, _predicted(sim, t, movers, xy, now_close)
