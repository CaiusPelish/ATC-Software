# backend/app/simulation/live.py

"""The live simulation behind /api/state and /api/ws.

Traffic spawns on its own, roughly the way EWR runs in south-west flow:
arrivals land on 22L and roll out to an exit, departures appear at a ramp
entry and taxi to 22R. Each aircraft is routed by the MAPF planner (space-time
A* against one shared reservation table, with the RunwayGate keeping taxiing
traffic off a runway while it is in use). With auto_route off, aircraft wait
for the controller instead (route()). hold() freezes an aircraft where it is;
releasing it replans from there.

The planner works on the simplified taxiway graph in steps of DT seconds.
Everything that goes into SimulationState is in KEWR_ground node ids and
seconds, so the frontend can draw it: a planned path is expanded to ground ids
(runway crossings split some taxiway edges) and turned into a Track, a
piecewise-linear (seconds, meters along the route) curve that progress is
read from.
"""

import functools
import heapq
import math
import random
from bisect import bisect_right
from collections import deque

from ..conflicts.detector import detect_conflicts
from ..models import Aircraft, SimulationState, Vehicle
from ..routing.airport import DT, TAXI_SPEED, get_airport
from ..routing.graph import airport_graph, runway_edges
from ..routing.mapf import AircraftRequest, plan_one
from ..routing.reservations import ReservationTable
from ..routing.runways import RunwayGate, exits, load_runways
from .arrivals import (
    APPROACH_KT,
    EXIT_SPEED_MS,
    FLEET,
    HANDOFF_HOLD_S,
    KT_TO_MS,
    LOCK_NM,
    NM_M,
    ROLLOUT_M,
    TOUCHDOWN_M,
)

ARRIVAL_RUNWAY = "22L"
DEPARTURE_RUNWAY = "22R"
SPAWN_EVERY_S = 40
FIRST_SPAWN_S = 2
# No new spawns while this many aircraft are on the ground or about to land.
MAX_ACTIVE = 8
# An arrival is scheduled this long before touchdown, so its runway window is known in advance.
LANDING_LEAD_S = 45
# A landing that can't have the runway is pushed back this much (a go-around), up to MAX_SLIPS times.
LANDING_SLIP_S = 30
MAX_SLIPS = 6
TAKEOFF_ROLL_S = 40
# A departure lines up and waits at most this long for its takeoff roll to be clear.
MAX_LINEUP_WAIT_S = 300
# Finished aircraft stay in state this long, so the frontend sees "arrived" before they vanish.
ARRIVED_LINGER_S = 8
# Faster than taxiing: on a landing or takeoff roll, where the aircraft can't be stopped.
ROLL_SPEED_MS = 1.5 * TAXI_SPEED


class Track:
    """Where something is along its route: ground node ids, meters to each, and (seconds, meters) points."""

    def __init__(self, route, points):
        self.route = list(route)
        self.cum = [0.0]
        for u, v in zip(self.route, self.route[1:]):
            self.cum.append(self.cum[-1] + airport_graph[u][v])
        self.points = points

    @property
    def length(self):
        return self.cum[-1]

    @property
    def end_t(self):
        return self.points[-1][0]

    def _segment(self, t):
        for (t0, d0), (t1, d1) in zip(self.points, self.points[1:]):
            if t0 <= t < t1:
                return t0, d0, t1, d1
        return None

    def dist(self, t):
        if t <= self.points[0][0]:
            return self.points[0][1]
        seg = self._segment(t)
        if seg is None:
            return self.points[-1][1]
        t0, d0, t1, d1 = seg
        return d0 + (d1 - d0) * (t - t0) / (t1 - t0)

    def speed(self, t):
        seg = self._segment(t)
        if seg is None:
            return 0.0
        t0, d0, t1, d1 = seg
        return (d1 - d0) / (t1 - t0)

    def time_at(self, d):
        """First time the track reaches d meters, or None if it never does."""
        if d <= self.points[0][1]:
            return self.points[0][0]
        for (t0, d0), (t1, d1) in zip(self.points, self.points[1:]):
            if d0 < d <= d1:
                return t0 + (t1 - t0) * (d - d0) / (d1 - d0)
        return None

    def index(self, d):
        """Index in route of the last node passed at d meters."""
        return max(0, bisect_right(self.cum, d + 1e-6) - 1)


@functools.cache
def _runway_adjacency():
    adj = {}
    for edge in runway_edges:
        a, b = edge.split("|")
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def runway_route(a, b):
    """Ground node ids from a to b along runway centreline edges only."""
    adj = _runway_adjacency()
    queue, best, prev = [(0.0, a)], {a: 0.0}, {}
    while queue:
        cost, n = heapq.heappop(queue)
        if n == b:
            path = [b]
            while path[-1] != a:
                path.append(prev[path[-1]])
            return path[::-1]
        if cost > best[n]:
            continue
        for m in adj.get(n, ()):
            c = cost + airport_graph[n][m]
            if c < best.get(m, math.inf):
                best[m], prev[m] = c, n
                heapq.heappush(queue, (c, m))
    raise ValueError(f"No runway path from {a} to {b}")


@functools.cache
def ground_link(u, v):
    """Ground node ids for one planner edge u -> v; a runway crossing splits it at the centreline."""
    if v in airport_graph[u]:
        return (u, v)
    planner_nodes = get_airport().graph
    prev, queue = {u: None}, deque([u])
    while queue:
        n = queue.popleft()
        for m in airport_graph[n]:
            if m in prev or (m in planner_nodes and m != v):
                continue
            prev[m] = n
            if m == v:
                path = [v]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return tuple(path[::-1])
            queue.append(m)
    raise ValueError(f"Planner edge {u}->{v} has no counterpart in KEWR_ground")


def expand(nodes):
    """(ground route, index in it of each planner node) for planner nodes without repeats."""
    route, idx = [nodes[0]], [0]
    for u, v in zip(nodes, nodes[1:]):
        route += ground_link(u, v)[1:]
        idx.append(len(route) - 1)
    return route, idx


def other_end(runway_name, runways):
    north, south = runways[runway_name].pair.split("/")
    return "rwy_" + (south if runway_name == north else north)


class LiveSim:
    def __init__(self, *, auto_spawn=True, auto_route=True, seed=None):
        self.ap = get_airport()
        self.runways = load_runways()
        self.gate = RunwayGate([self.runways[ARRIVAL_RUNWAY], self.runways[DEPARTURE_RUNWAY]])
        self.res = ReservationTable()
        self.state = SimulationState(running=True)
        self.auto_spawn = auto_spawn
        self.auto_route = auto_route
        self.rng = random.Random(seed)

        self.tracks = {}       # aircraft or vehicle id -> Track
        self.weight = {}       # aircraft id -> weight class
        self.routed = {}       # aircraft id -> True once it has a route to its destination
        self.leaves = {}       # aircraft id -> True if it leaves the map at its destination
        self.departs = {}      # aircraft id -> runway pair it takes off from
        self.rollout_until = {}  # arrival id -> seconds when it is off its landing roll
        self.finished_at = {}  # aircraft id -> seconds when it arrived
        self.pending = []      # [(appear_s, n, Aircraft)]: arrivals not on the runway yet
        self.callsigns = set()
        self.next_spawn_s = FIRST_SPAWN_S
        self.next_is_arrival = True

        self.ramps = self.ap.hold_shorts("movement")
        # Departures line up at the exit nearest the threshold.
        self.lineup = {name: exits(rw)[0][1] for name, rw in self.runways.items()}

    @property
    def now(self):
        return self.state.time

    @property
    def step(self):
        return math.floor(self.now / DT)

    def aircraft(self, aid):
        for a in self.state.aircraft:
            if a.id == aid:
                return a
        raise KeyError(aid)

    # ---- tick ------------------------------------------------------------

    def tick(self, dt):
        if not self.state.running:
            return self.state
        self.state.time = round(self.state.time + dt, 3)
        t = self.now

        if self.auto_spawn and t >= self.next_spawn_s:
            self.next_spawn_s = t + SPAWN_EVERY_S
            if self._active_count() < MAX_ACTIVE:
                (self.spawn_arrival if self.next_is_arrival else self.spawn_departure)()
                self.next_is_arrival = not self.next_is_arrival

        while self.pending and self.pending[0][0] <= t:
            self.state.aircraft.append(heapq.heappop(self.pending)[2])

        for a in self.state.aircraft:
            self._update_aircraft(a, t)
        for v in self.state.vehicles:
            self._update_vehicle(v, t)
        self._drop_finished(t)

        self.state.conflicts = detect_conflicts(self)
        return self.state

    def _update_aircraft(self, a, t):
        tr = self.tracks[a.id]
        d = tr.dist(t)
        a.position = tr.route[tr.index(d)]
        a.progress = round(d / tr.length, 4) if tr.length else 0.0
        if a.status in ("holding", "arrived"):
            a.speed = 0.0
            return
        a.speed = round(tr.speed(t), 2)
        if t >= tr.end_t:
            a.status = "arrived" if self.routed.get(a.id) else "waiting"
            if a.status == "arrived":
                a.progress = 1.0
                self.finished_at[a.id] = t
        else:
            a.status = "taxiing"

    def _update_vehicle(self, v, t):
        tr = self.tracks[v.id]
        d = tr.dist(t)
        v.position = tr.route[tr.index(d)]
        v.progress = round(d / tr.length, 4) if tr.length else 0.0

    def _drop_finished(self, t):
        gone = {aid for aid, done in self.finished_at.items()
                if self.leaves.get(aid) and t - done >= ARRIVED_LINGER_S}
        if not gone:
            return
        self.state.aircraft = [a for a in self.state.aircraft if a.id not in gone]
        for aid in gone:
            if aid in self.res:
                self.res.remove_path(aid)
            for d in (self.tracks, self.weight, self.routed, self.leaves, self.departs,
                      self.rollout_until, self.finished_at):
                d.pop(aid, None)
        # Runway windows that are over can't matter any more.
        for pair, windows in self.gate.windows.items():
            self.gate.windows[pair] = [w for w in windows if w[1] >= self.step]

    def _active_count(self):
        return len(self.pending) + sum(a.status != "arrived" for a in self.state.aircraft)

    # ---- spawning --------------------------------------------------------

    def _new_flight(self):
        prefix, weight, ac_type = self.rng.choice(FLEET)
        callsign = f"{prefix}{self.rng.randint(100, 2999)}"
        while callsign in self.callsigns:
            callsign = f"{prefix}{self.rng.randint(100, 2999)}"
        self.callsigns.add(callsign)
        return f"AC-{self.rng.getrandbits(32):08x}", callsign, ac_type, weight

    def node_free(self, node):
        """Nobody is on node, due there, or just leaving it (last_reserved is -1 for nobody)."""
        last = self.res.last_reserved(node)
        return last < 0 or last < self.step - 1

    def free_ramps(self):
        """Ramp entries nobody is on or due at: where a departure can appear."""
        taken = {v.position for v in self.state.vehicles}
        return [r for r in self.ramps if r not in taken and self.node_free(r)]

    def add_waiting(self, aid, callsign, ac_type, weight, node, destination):
        """An aircraft standing at a planner node with no route yet."""
        a = Aircraft(id=aid, callsign=callsign, aircraft_type=ac_type, position=node,
                     destination=destination, route=[node], status="waiting")
        self.weight[aid] = weight
        self.callsigns.add(callsign)
        self.tracks[aid] = Track([node], [(self.now, 0.0)])
        self.res.add_path(aid, [(node, self.step)], park_at_goal=True)
        self.state.aircraft.append(a)
        return a

    def spawn_departure(self):
        free = self.free_ramps()
        if not free:
            return None
        aid, callsign, ac_type, weight = self._new_flight()
        a = self.add_waiting(aid, callsign, ac_type, weight, self.rng.choice(free),
                             "rwy_" + DEPARTURE_RUNWAY)
        if self.auto_route:
            try:
                self.route(aid, a.destination)
            except ValueError:
                pass  # no route right now; it waits for the controller
        return a

    def spawn_arrival(self, *, touchdown_s=None, ramp=None, force=False):
        """Schedule a landing on ARRIVAL_RUNWAY; the aircraft appears at the threshold.

        The runway is closed to everyone else from LOCK_NM out until the
        arrival has vacated. A landing that would meet traffic on the runway
        slips back (force=True skips that check, for scenarios).
        """
        rw = self.runways[ARRIVAL_RUNWAY]
        aid, callsign, ac_type, weight = self._new_flight()
        v0 = APPROACH_KT * KT_TO_MS
        need = TOUCHDOWN_M + ROLLOUT_M[weight] + self.rng.uniform(-150, 250)
        right = exits(rw, "right")
        exit_along, exit_node = next(((al, n) for al, n in right if al >= need), right[-1])
        roll_s = 2 * (exit_along - TOUCHDOWN_M) / (v0 + EXIT_SPEED_MS) + 6
        lock_s = LOCK_NM * NM_M / v0

        td = self.now + LANDING_LEAD_S if touchdown_s is None else touchdown_s
        for _ in range(MAX_SLIPS):
            w0, w1 = math.floor((td - lock_s) / DT), math.ceil((td + roll_s) / DT)
            if force or not (self.gate.busy(rw.pair, w0, w1)
                             or self.runway_busy(rw.pair, w0, w1)
                             or self.res.node_conflict(exit_node, w1)):
                break
            td += LANDING_SLIP_S
        else:
            self.callsigns.discard(callsign)
            return None

        self.gate.add_window(rw.pair, w0, w1, aid)
        rollout = runway_route("rwy_" + ARRIVAL_RUNWAY, exit_node)
        appear = td - TOUCHDOWN_M / v0
        self.weight[aid] = weight
        self.rollout_until[aid] = w1 * DT
        tr = Track(rollout, [(appear, 0.0), (td, TOUCHDOWN_M)])
        tr.points.append((w1 * DT, tr.length))
        self.tracks[aid] = tr
        self.res.add_path(aid, [(exit_node, w1)], park_at_goal=True)

        ramps = [r for r in self.ramps if self.res.parked_by(r) is None] or self.ramps
        a = Aircraft(id=aid, callsign=callsign, aircraft_type=ac_type, position=rollout[0],
                     destination=ramp or self.rng.choice(ramps), route=tr.route, status="taxiing")
        if self.auto_route:
            try:
                self._plan_and_commit(a, a.destination, tr, from_d=tr.length)
            except ValueError:
                pass  # it stops at the exit and waits for the controller
        heapq.heappush(self.pending, (appear, id(a), a))
        return a

    def add_vehicle(self, vid, route, speed=0.0, start_s=None):
        start = self.now if start_s is None else start_s
        tr = Track(route, [(start, 0.0)])
        if speed > 0 and tr.length > 0:
            tr.points.append((start + tr.length / speed, tr.length))
        self.tracks[vid] = tr
        v = Vehicle(id=vid, position=route[0], route=list(route), speed=speed)
        self.state.vehicles.append(v)
        return v

    # ---- runway use ------------------------------------------------------

    def runway_busy(self, pair, t0, t1, ignore=None):
        """True if any reserved aircraft but `ignore` is on the runway during steps [t0, t1)."""
        on = self.gate.on[pair]
        for aid, path in self.res.paths().items():
            if aid == ignore:
                continue
            for (u, ta), (v, tb) in zip(path, path[1:]):
                if ta < t1 and t0 < tb and (u in on or v in on or pair in self.gate.runways_touched(u, v)):
                    return True
            node, t_last = path[-1]
            if node in on and t_last < t1 and not self.leaves.get(aid):
                return True  # parked on it, or waiting there for the controller
        return False

    def _clear_takeoff(self, aid):
        for pair, windows in self.gate.windows.items():
            self.gate.windows[pair] = [w for w in windows if not (w[2] == aid and w[3] == "takeoff")]

    # ---- controller actions ---------------------------------------------

    def destination_goal(self, destination):
        """(planner goal, departure runway name or None) for a destination node id."""
        if destination.startswith("rwy_") and destination[4:] in self.runways:
            name = destination[4:]
            return self.lineup[name], name
        if destination in self.ap.graph:
            return destination, None
        raise ValueError(f"{destination} is not a taxiway node or runway end")

    def route(self, aid, destination):
        """Plan aid from where it is now to destination and start it taxiing."""
        a = self.aircraft(aid)
        if a.status == "arrived" and self.leaves.get(aid):
            raise ValueError(f"{a.callsign} has already left")
        self._plan_and_commit(a, destination, self.tracks[aid])
        a.destination = destination
        if a.status in ("waiting", "arrived", "holding"):
            a.status = "taxiing"
        return a

    def hold(self, aid, hold):
        a = self.aircraft(aid)
        t = self.now
        if not hold:
            if a.status != "holding":
                return a
            if self.routed.get(aid):
                self._plan_and_commit(a, a.destination, self.tracks[aid])  # stays holding if it fails
                a.status = "taxiing"
            else:
                a.status = "waiting"
            return a

        if a.status == "arrived":
            raise ValueError(f"{a.callsign} has already arrived")
        tr = self.tracks[aid]
        if t < self.rollout_until.get(aid, -1) or tr.speed(t) > ROLL_SPEED_MS:
            raise ValueError(f"{a.callsign} is on its landing or takeoff roll")
        d = tr.dist(t)
        i = tr.index(d)
        planner = self.ap.graph
        prev = next((n for n in reversed(tr.route[:i + 1]) if n in planner), None)
        ahead = next((tr.route[k] for k in range(i, len(tr.route))
                      if tr.route[k] in planner and tr.cum[k] >= d - 1e-6), prev)
        prev = prev or ahead
        path = [(prev, self.step)] if prev == ahead else [(prev, self.step), (ahead, self.step + 1)]
        self.res.add_path(aid, path, park_at_goal=True)
        self._clear_takeoff(aid)
        self.leaves[aid] = False
        self.tracks[aid] = Track(tr.route, [(t, d)])
        a.status, a.speed = "holding", 0.0
        return a

    def _plan_and_commit(self, a, destination, tr, from_d=0.0):
        """Replace a's route with a plan to destination, continuing from where it is on tr.

        The plan starts at the first planner node at least from_d meters along
        tr (an arrival's exit, rather than the exits it rolls past).
        """
        aid, t = a.id, self.now
        goal, runway = self.destination_goal(destination)
        planner = self.ap.graph

        # The plan starts at the next planner node ahead; until then the aircraft keeps going.
        d = tr.dist(t)
        j = next((k for k in range(tr.index(d), len(tr.route))
                  if tr.route[k] in planner and tr.cum[k] >= max(d, from_d) - 1e-6), None)
        if j is None:
            raise ValueError(f"{a.callsign} can't be rerouted from here")
        start = tr.route[j]
        moving = a.status not in ("holding", "waiting")
        reach = tr.time_at(tr.cum[j]) if moving else None
        if reach is None or reach < t:
            reach = t + (tr.cum[j] - d) / TAXI_SPEED
        t_start = max(math.ceil(reach / DT), self.step)

        pair = self.runways[runway].pair if runway else None
        rolling = next((p for p, nodes in self.gate.on.items() if start in nodes), None)
        allowed = self.gate.for_aircraft(aid, departs_from=pair, rolling_out=rolling)
        if runway:
            hold = TAKEOFF_ROLL_S // DT
        elif goal in self.ramps:
            hold = HANDOFF_HOLD_S // DT
        else:
            hold = None

        def request(hold_until=None):
            return AircraftRequest(aid, start, goal, ready_t=t_start, weight_class=self.weight[aid],
                                   callsign=a.callsign, goal_hold=hold, hold_until=hold_until)

        planned = plan_one(self.ap, request(), self.res, edge_allowed=allowed)
        if not planned.ok:
            raise ValueError(planned.reason)
        if runway:
            # Line up and wait until nobody else is on the runway for the whole roll.
            first_end = planned.path[-1][1]
            extra = 0
            while self.runway_busy(pair, planned.path[-1][1] - hold, planned.path[-1][1], ignore=aid):
                extra += 2
                if extra * DT > MAX_LINEUP_WAIT_S:
                    raise ValueError(f"Runway {runway} won't be clear for {a.callsign}'s takeoff")
                retry = plan_one(self.ap, request(first_end + extra), self.res, edge_allowed=allowed)
                if retry.ok:
                    planned = retry

        path = planned.path
        nodes = [n for k, (n, _) in enumerate(path) if k == 0 or n != path[k - 1][0]]
        ground, idx = expand(nodes)
        if runway:
            ground += runway_route(goal, other_end(runway, self.runways))[1:]
        route = tr.route[:j] + ground
        new = Track(route, [])
        # Keep the old track until the plan takes over (e.g. the landing roll).
        points = [(t, d)]
        if moving:
            points += [p for p in tr.points if p[0] > t and p[1] < tr.cum[j] - 1e-6]
        k = 0
        for s, (n, step) in enumerate(path):
            if s > 0 and n != path[s - 1][0]:
                k += 1
            points.append((step * DT, new.cum[j + idx[k]]))
        if runway:
            end = path[-1][1]
            points = [p for p in points if p[0] <= (end - hold) * DT] + [(end * DT, new.length)]
        new.points = [p for p in points if p[0] >= t] or [(t, d)]

        self.res.add_path(aid, path, park_at_goal=hold is None)
        self._clear_takeoff(aid)
        if runway:
            self.gate.add_window(pair, path[-1][1] - hold, path[-1][1], aid, kind="takeoff")
            self.departs[aid] = pair
        else:
            self.departs.pop(aid, None)
        self.tracks[aid] = new
        self.routed[aid] = True
        self.leaves[aid] = hold is not None
        self.finished_at.pop(aid, None)
        a.route = new.route
        return new
