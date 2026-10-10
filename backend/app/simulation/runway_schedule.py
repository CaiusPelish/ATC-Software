# backend/app/simulation/runway_schedule.py

"""When each runway was really in use during the Amelia recording.

A runway roll is a run of points above TAKEOFF_ROLL_SPEED inside a runway
strip and below ROLL_MAX_ALT_FT. The recorded altitude is too noisy near the
ground to tell takeoffs from landings (taxiing aircraft read 40+ ft), so the
speed trend decides: accelerating along the strip is a takeoff, decelerating
is a landing. For each roll the runway was blocked:
- takeoff: from when the aircraft entered the runway to line up until lift-off;
- landing: from 2 nm out on final until it left the runway after rollout.

These become RunwayGate windows, so the MAPF planner has to respect the
runway schedule that really happened: no crossing in front of a real landing,
departures queueing for their real takeoff slots.
"""

from dataclasses import dataclass

import numpy as np

from ..routing.airport import get_airport
from ..routing.runways import ZONE_M, _to_xy, load_runways
from .traffic import TAKEOFF_ROLL_SPEED

APPROACH_KT = 140
KT = 0.5144
LOCK_NM = 2
NM_M = 1852
ROLL_MAX_ALT_FT = 100
LINE_UP_MAX_ALT_FT = 60


@dataclass
class RunwayUse:
    id: str
    pair: str          # "4L/22R"
    kind: str          # "takeoff" | "landing"
    busy_from: float   # seconds from the recording start
    busy_until: float
    roll_start: float  # takeoff roll or touchdown


def real_runway_uses(df, t0=None):
    """[RunwayUse] for every takeoff and landing roll in the recording, by time."""
    ap = get_airport()
    t0 = int(df["UnixTime"].min()) if t0 is None else t0
    strips = {}
    for r in load_runways().values():
        strips.setdefault(r.pair, r)
    lock_s = LOCK_NM * NM_M / (APPROACH_KT * KT)

    uses = []
    for flight_id, flight in df[df["Type"] == 0].groupby("ID"):
        flight = flight.sort_values("UnixTime").reset_index(drop=True)
        x, y = _to_xy(ap, flight["Lat"].to_numpy(), flight["Lon"].to_numpy())
        alt = flight["Altitude"].to_numpy()
        speed = flight["Speed"].to_numpy()
        times = flight["UnixTime"].to_numpy() - t0

        best = None
        for pair, rw in strips.items():
            zone = np.array([rw.on_runway_xy(a, b, margin=ZONE_M) for a, b in zip(x, y)])
            roll = zone & (speed > TAKEOFF_ROLL_SPEED) & (alt < ROLL_MAX_ALT_FT)
            if roll.sum() >= 5 and (best is None or roll.sum() > best[2].sum()):
                best = (pair, zone, roll)
        if best is None:
            continue
        pair, zone, roll = best
        idx = np.flatnonzero(roll)
        first, last = idx[0], idx[-1]
        takeoff = speed[last] > speed[first]

        if takeoff:
            start = first
            while start > 0 and zone[start - 1] and alt[start - 1] < LINE_UP_MAX_ALT_FT:
                start -= 1
            uses.append(RunwayUse(str(flight_id), pair, "takeoff",
                                  float(times[start]), float(times[last]), float(times[first])))
        else:
            end = last
            while end + 1 < len(flight) and zone[end + 1]:
                end += 1
            uses.append(RunwayUse(str(flight_id), pair, "landing",
                                  float(times[first] - lock_s), float(times[end]), float(times[first])))
    return sorted(uses, key=lambda u: u.busy_from)
