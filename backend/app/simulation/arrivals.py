# backend/app/simulation/arrivals.py

"""Arrival push into KEWR on runways 22L and 22R, taxied to the ramp by the MAPF planner.

What is modelled, roughly the way EWR runs in south-west flow:
- One arrival stream split across 22L (most traffic) and 22R. The runways are
  950 ft apart, closer than 2500 ft, so wake turbulence separation applies
  between consecutive arrivals as if they used one runway (FAA 7110.65
  5-5-4 / 5-5-11), with 1.5 nm minimum between staggered aircraft on
  different runways and 3 nm on the same runway.
- 140 kt on final. Touchdown about 350 m past the threshold, then a braking
  roll to the first exit to the right (the terminal side) that the aircraft
  can make for its weight: heavies need more runway than narrowbodies.
- A runway is "busy" from when an arrival is 2 nm out until it has vacated.
  Nobody may enter it then (RunwayGate). Every 22L arrival has to cross 22R
  to reach the terminals, so crossings are squeezed between 22R arrivals,
  aircraft holding short between the runways until there is a gap.
- After vacating, each aircraft is planned by prioritized space-time A* to a
  ramp entry (a movement-area boundary), where it is handed to ramp control
  and leaves the simulation 20 s later.

Run from backend/:
    python3 -m app.simulation.arrivals [--n 15] [--seed 7]
writes app/simulation/data/plans/arrivals.gif and prints the arrival log.
"""

import argparse
import math
import random
from dataclasses import dataclass

from ..routing.airport import DT, get_airport
from ..routing.mapf import AircraftRequest, plan_batch
from ..routing.reservations import check_paths
from ..routing.runways import RunwayGate, exits, load_runways, runway_conflicts

APPROACH_KT = 140
KT_TO_MS = 0.5144
NM_M = 1852
# How far out an arrival is drawn on final, and how far out it closes the runway.
FINAL_NM = 3
LOCK_NM = 2
TOUCHDOWN_M = 350
EXIT_SPEED_MS = 13
# Runway needed after touchdown before a high-speed turn-off, by weight class.
ROLLOUT_M = {"small": 900, "large": 1300, "heavy": 1800}
# Wake turbulence miles, leader -> follower; anything not listed is the radar minimum.
WAKE_NM = {("heavy", "heavy"): 4, ("heavy", "large"): 5, ("heavy", "small"): 6, ("large", "small"): 4}
SAME_RUNWAY_NM = 3
STAGGER_NM = 1.5
HANDOFF_HOLD_S = 20

FLEET = [
    # (callsign prefix, weight class, type)
    ("UAL", "large", "B738"), ("UAL", "large", "A320"), ("UAL", "large", "B739"),
    ("UAL", "heavy", "B77W"), ("UAL", "heavy", "B789"), ("JBU", "large", "A321"),
    ("DAL", "large", "A321"), ("AAL", "large", "B738"), ("RPA", "large", "E175"),
    ("SWA", "large", "B38M"), ("BAW", "heavy", "B772"), ("FDX", "heavy", "B763"),
]


@dataclass
class Arrival:
    id: str
    callsign: str
    ac_type: str
    weight: str
    runway: str
    touchdown_s: float = 0.0
    exit_node: str = ""
    exit_along: float = 0.0
    vacate_s: float = 0.0
    ramp: str = ""


def wake_gap_s(leader, follower):
    same = leader.runway == follower.runway
    nm = max(WAKE_NM.get((leader.weight, follower.weight), 0), SAME_RUNWAY_NM if same else STAGGER_NM)
    return nm * NM_M / (APPROACH_KT * KT_TO_MS)


def schedule(n=15, seed=7, first_touchdown_s=90):
    """Sequence, separate and roll out n arrivals; returns [Arrival] in landing order."""
    rng = random.Random(seed)
    runways = load_runways()
    ap = get_airport()
    v0 = APPROACH_KT * KT_TO_MS

    arrivals = []
    used = set()
    for i in range(n):
        prefix, weight, ac_type = rng.choice(FLEET)
        number = rng.randint(100, 2999)
        while (prefix, number) in used:
            number = rng.randint(100, 2999)
        used.add((prefix, number))
        # About two in three land on 22L, EWR's primary arrival runway.
        runway = "22R" if i % 3 == 2 else "22L"
        arrivals.append(Arrival(f"{prefix}{number}", f"{prefix}{number}", ac_type, weight, runway))

    ramps = ap.hold_shorts("movement")
    last_on = {}
    for i, a in enumerate(arrivals):
        t = first_touchdown_s if i == 0 else arrivals[i - 1].touchdown_s + wake_gap_s(arrivals[i - 1], a)
        t += rng.uniform(0, 12)  # controllers never deliver exactly the minimum
        # The previous arrival on the same runway must have vacated before touchdown.
        if a.runway in last_on:
            t = max(t, last_on[a.runway].vacate_s + 5)
        a.touchdown_s = t

        rw = runways[a.runway]
        need = TOUCHDOWN_M + ROLLOUT_M[a.weight] + rng.uniform(-150, 250)
        options = [(along, node) for along, node in exits(rw, "right") if along >= need]
        a.exit_along, a.exit_node = options[0] if options else exits(rw, "right")[-1]
        roll = a.exit_along - TOUCHDOWN_M
        # Constant deceleration from approach speed to turn-off speed over the roll.
        a.vacate_s = t + 2 * roll / (v0 + EXIT_SPEED_MS) + 6
        a.ramp = rng.choice(ramps)
        last_on[a.runway] = a
    return arrivals


def approach_track(arrival, runway):
    """[(t_seconds, lat, lon)] from FINAL_NM out to the exit, sampled every second."""
    v0 = APPROACH_KT * KT_TO_MS
    final_s = FINAL_NM * NM_M / v0
    track = []
    t = arrival.touchdown_s - final_s
    while t < arrival.touchdown_s:
        along = TOUCHDOWN_M - (arrival.touchdown_s - t) * v0
        track.append((t, *runway.point(along)))
        t += 1
    roll = arrival.exit_along - TOUCHDOWN_M
    roll_s = arrival.vacate_s - 6 - arrival.touchdown_s
    decel = (v0 - EXIT_SPEED_MS) / roll_s
    for k in range(int(roll_s) + 1):
        along = TOUCHDOWN_M + v0 * k - decel * k * k / 2
        track.append((arrival.touchdown_s + k, *runway.point(min(along, arrival.exit_along))))
    end = math.ceil(arrival.vacate_s / DT) * DT
    track.append((end, *runway.point(arrival.exit_along)))
    return track


def simulate(n=15, seed=7):
    ap = get_airport()
    runways = load_runways()
    arrivals = schedule(n, seed)
    gate = RunwayGate([runways["22L"], runways["22R"]])
    lock_s = LOCK_NM * NM_M / (APPROACH_KT * KT_TO_MS)
    for a in arrivals:
        pair = runways[a.runway].pair
        gate.add_window(pair, int((a.touchdown_s - lock_s) // DT), math.ceil(a.vacate_s / DT), a.id)

    requests = [
        AircraftRequest(a.id, a.exit_node, a.ramp, ready_t=math.ceil(a.vacate_s / DT),
                        weight_class=a.weight, goal_hold=HANDOFF_HOLD_S // DT)
        for a in arrivals
    ]
    result = plan_batch(ap, requests, edge_allowed=gate)
    tracks = {a.id: approach_track(a, runways[a.runway]) for a in arrivals}
    return arrivals, requests, result, gate, tracks


def crossing_holds(gate, path):
    """Steps spent holding short right before entering a runway."""
    held = 0
    for (u, _), (v, _), (w, _) in zip(path, path[1:], path[2:]):
        if u == v and v != w and gate.runways_touched(v, w):
            held += 1
    return held


def report(arrivals, requests, result, gate):
    ap = get_airport()
    by_id = {r.id: r for r in requests}
    print(f"{'flight':<9}{'type':<6}{'rwy':<5}{'touchdown':>10}{'exit':>18}{'ramp':>14}"
          f"{'taxi':>7}{'crossing hold':>15}")
    for a in arrivals:
        path = result.paths.get(a.id)
        if path is None:
            print(f"{a.id:<9}{a.ac_type:<6}{a.runway:<5} FAILED: {result.failures[a.id]}")
            continue
        taxi = (path[-1][1] - path[0][1] - by_id[a.id].goal_hold) * DT
        hold = crossing_holds(gate, path) * DT
        exit_name = ap.label(a.exit_node).split(" (")[0]
        ramp_name = ap.label(a.ramp).split(" (")[0]
        print(f"{a.id:<9}{a.ac_type:<6}{a.runway:<5}{_mmss(a.touchdown_s):>10}{exit_name:>18}"
              f"{ramp_name:>14}{taxi:>6}s{hold:>14}s")

    conflicts = check_paths(result.paths, park_at_goal=False)
    incursions = runway_conflicts(gate, result.paths)
    print(f"\n{len(result.paths)}/{len(arrivals)} taxied to the ramp; "
          f"{len(conflicts)} aircraft conflicts; {len(incursions)} runway incursions; "
          f"delay from traffic {result.total_delay_steps * DT}s")
    return conflicts, incursions


def _mmss(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-gif", action="store_true")
    args = parser.parse_args(argv)

    arrivals, requests, result, gate, tracks = simulate(args.n, args.seed)
    conflicts, incursions = report(arrivals, requests, result, gate)
    if args.no_gif:
        return

    from ..routing.visualize_plan import OUT_DIR, animate

    runways = load_runways()
    crossers = sum(1 for a in arrivals if a.runway == "22L")
    held = sum(crossing_holds(gate, p) for p in result.paths.values()) * DT
    out, _ = animate(
        get_airport(), requests, result, OUT_DIR / "arrivals.gif",
        f"KEWR arrivals 22L/22R → ramp ({len(arrivals)} aircraft)",
        stride=0.5, zoom=False, runways=[runways["22L"], runways["22R"]], tracks=tracks,
        labels_for={a.id: f"{a.id} {a.ac_type}" for a in arrivals},
        subtitle=(f"{crossers} cross 22R, {held}s holding short; "
                  f"{len(conflicts)} conflicts, {len(incursions)} runway incursions"),
        fps=10,
    )
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
