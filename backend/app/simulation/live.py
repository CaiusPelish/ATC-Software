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

import copy
import functools
import heapq
import math
import random
from bisect import bisect_right
from collections import deque

from ..conflicts.detector import detect_conflicts
from ..models import Aircraft, SimulationState, Vehicle
from ..routing.airport import DT, TAXI_SPEED, get_airport
from ..routing.astar import astar
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
# Delay-and-retry rounds for a replan forced on someone by a hold; a failed plan is costly.
REPAIR_RETRIES = 2
# Faster than taxiing: on a landing or takeoff roll, where the aircraft can't be stopped.
ROLL_SPEED_MS = 1.5 * TAXI_SPEED
# Fire trucks and other ground vehicles, about 25 kt.
VEHICLE_SPEED_MS = 13.0


class NoRoute(ValueError):
    """No route right now; blockers are the aircraft in the way, when known."""

    def __init__(self, message, blockers=()):
        super().__init__(message)
        self.blockers = set(blockers)


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
        self.exit_d = {}       # arrival id -> meters along its route to the exit it rolls out to
        self.finished_at = {}  # aircraft id -> seconds when it arrived
        self.pending = []      # [(appear_s, n, Aircraft)]: arrivals not on the runway yet
        self.callsigns = set()
        self.next_spawn_s = FIRST_SPAWN_S
        self.next_is_arrival = True
        # Replan other aircraft when a hold blocks their plan (off only to measure what it buys).
        self.repair_holds = True
        self.repairs = 0        # plans changed by hold repair
        self.knock_on_holds = 0  # aircraft held because no replan could avoid a hold
        self.auto_held = set()   # those aircraft; they resume by themselves once a route opens up
        self.swaps = 0           # deadlocks broken by planning one aircraft through another's spot
        self.unrouted = set()    # spawned for auto-routing but no route yet; retried every step
        self.next_resume_step = 0

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
        """An aircraft in state, or an arrival still on final (pending)."""
        for a in self.state.aircraft:
            if a.id == aid:
                return a
        for _, _, a in self.pending:
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

        if (self.auto_held or self.unrouted) and self.step >= self.next_resume_step:
            self.next_resume_step = self.step + 1
            self._retry_stalled()

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
        v.speed = round(tr.speed(t), 2)
        if v.status == "taxiing" and t >= tr.end_t:
            v.status = "waiting"

    def _drop_finished(self, t):
        gone = {aid for aid, done in self.finished_at.items()
                if self.leaves.get(aid) and t - done >= ARRIVED_LINGER_S}
        if not gone:
            return
        self.state.aircraft = [a for a in self.state.aircraft if a.id not in gone]
        for aid in gone:
            self._unreserve(aid)
            for d in (self.tracks, self.weight, self.routed, self.leaves, self.departs,
                      self.rollout_until, self.exit_d, self.finished_at):
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
        self._reserve(aid, [(node, self.step)], park_at_goal=True)
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
                self.unrouted.add(aid)  # retried until a route opens up
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
        self.exit_d[aid] = tr.length
        self.tracks[aid] = tr
        self._reserve(aid, [(exit_node, w1)], park_at_goal=True)

        ramps = [r for r in self.ramps if self.res.parked_by(r) is None] or self.ramps
        a = Aircraft(id=aid, callsign=callsign, aircraft_type=ac_type, position=rollout[0],
                     destination=ramp or self.rng.choice(ramps), route=tr.route, status="taxiing")
        if self.auto_route:
            try:
                self._plan_and_commit(a, a.destination, tr)
            except ValueError:
                self.unrouted.add(aid)  # it stops at the exit until a route opens up
        heapq.heappush(self.pending, (appear, id(a), a))
        return a

    def add_vehicle(self, vid, route, speed=0.0, start_s=None):
        if vid in self.tracks:
            raise ValueError(f"{vid} already exists")
        if route[0] not in airport_graph:
            raise ValueError(f"{route[0]} is not a ground node")
        start = self.now if start_s is None else start_s
        tr = Track(route, [(start, 0.0)])
        if speed > 0 and tr.length > 0:
            tr.points.append((start + tr.length / speed, tr.length))
        self.tracks[vid] = tr
        v = Vehicle(id=vid, position=route[0], route=list(route), speed=speed,
                    status="taxiing" if len(tr.points) > 1 else "waiting")
        self.state.vehicles.append(v)
        return v

    # ---- vehicles ---------------------------------------------------------
    # Trucks are not planned: they drive the shortest way, runways included,
    # and reserve nothing. Running into traffic is what conflicts report.

    def vehicle(self, vid):
        for v in self.state.vehicles:
            if v.id == vid:
                return v
        raise KeyError(vid)

    def route_vehicle(self, vid, destination, speed=VEHICLE_SPEED_MS):
        """Drive vid by the shortest ground path from where it is to destination."""
        v = self.vehicle(vid)
        if destination not in airport_graph:
            raise ValueError(f"{destination} is not a ground node")
        tr, t = self.tracks[vid], self.now
        d = tr.dist(t)
        i = tr.index(d)
        # Between two nodes: keep going to the next one, then turn there.
        mid_edge = i < len(tr.route) - 1 and d > tr.cum[i] + 1e-6
        head = tr.route[i:i + 2] if mid_edge else tr.route[i:i + 1]
        found = astar(airport_graph, head[-1], destination)
        if found is None:
            raise ValueError(f"No ground path from {head[-1]} to {destination}")
        new = Track(head + found["route"][1:], [(t, d - tr.cum[i])])
        if new.length > new.points[0][1]:
            new.points.append((t + (new.length - new.points[0][1]) / speed, new.length))
        self.tracks[vid] = new
        v.route = new.route
        v.status = "taxiing" if len(new.points) > 1 else "waiting"
        self._update_vehicle(v, t)
        return v

    def hold_vehicle(self, vid, hold, speed=VEHICLE_SPEED_MS):
        """Stop vid where it is (hold=True), or carry on along its route (hold=False)."""
        v = self.vehicle(vid)
        tr, t = self.tracks[vid], self.now
        d = tr.dist(t)
        if hold:
            self.tracks[vid] = Track(tr.route, [(t, d)])
            v.status = "holding"
        elif v.status == "holding":
            new = Track(tr.route, [(t, d)])
            if tr.length > d:
                new.points.append((t + (tr.length - d) / speed, tr.length))
            self.tracks[vid] = new
            v.status = "taxiing" if len(new.points) > 1 else "waiting"
        self._update_vehicle(v, t)
        return v

    # ---- reservations ----------------------------------------------------

    @staticmethod
    def _behind(aid):
        """Id of the extra reservation for the node behind an aircraft held mid-edge."""
        return f"{aid}@behind"

    def _reserve(self, aid, path, park_at_goal, behind=None):
        self._unreserve(aid)
        self.res.add_path(aid, path, park_at_goal=park_at_goal)
        if behind:
            self.res.add_path(self._behind(aid), [behind], park_at_goal=True)

    def _unreserve(self, aid):
        for key in (aid, self._behind(aid)):
            if key in self.res:
                self.res.remove_path(key)

    def _walled_off(self, aid, start, goal):
        """Ids of who blocks every way from start to goal by parking (holding, waiting), or None.

        Space-time A* only gives up after searching to its horizon, which takes
        seconds; this answers the common hopeless case (a held aircraft on the
        only way in) at once.
        """
        blockers = set()

        def parked(node):
            other = self.res.parked_by(node, ignore=aid)
            if other:
                blockers.add(other.split("@")[0])
            return other

        if parked(goal):
            return blockers
        seen, queue = {start}, deque([start])
        while queue:
            n = queue.popleft()
            for m in self.ap.graph[n]:
                if m == goal:
                    return None
                if m not in seen and not parked(m):
                    seen.add(m)
                    queue.append(m)
        return blockers

    def _callsign(self, aid):
        try:
            return self.aircraft(aid).callsign
        except KeyError:
            return aid

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
        self.auto_held.discard(aid)
        a.destination = destination
        if a.status in ("waiting", "arrived", "holding"):
            a.status = "taxiing"
        return a

    def hold(self, aid, hold):
        """Stop aid where it is (hold=True), or replan it from there (hold=False).

        Holding also replans every aircraft whose plan now runs into it; see repair().
        """
        a = self.aircraft(aid)
        self.auto_held.discard(aid)
        if not hold:
            if a.status != "holding":
                return a
            if self.routed.get(aid):
                try:
                    self._plan_and_commit(a, a.destination, self.tracks[aid])
                except ValueError as exc:
                    self.auto_held.add(aid)  # keeps holding, and goes as soon as it can
                    raise ValueError(f"{exc} It will continue when the way is clear.") from exc
                a.status = "taxiing"
            else:
                a.status = "waiting"
            return a

        self._stop(a)
        if self.repair_holds:
            self.repair(aid)
        return a

    def _stop(self, a):
        """Freeze a where it is and reserve that spot until further notice."""
        aid, t = a.id, self.now
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
        # Stopped between two nodes: nobody may use either until it moves again.
        self._reserve(aid, [(ahead, self.step)], park_at_goal=True,
                      behind=(prev, self.step) if prev != ahead else None)
        self._clear_takeoff(aid)
        self.leaves[aid] = False
        self.tracks[aid] = Track(tr.route, [(t, d)])
        a.status, a.speed = "holding", 0.0

    def blocked_by(self, aid):
        """Other aircraft whose reserved path, from now on, runs into aid's reservation."""
        mine = ReservationTable(self.res.node_buffer, self.res.follow_gap)
        mine.add_path(aid, self.res.path(aid), park_at_goal=not self.leaves.get(aid))
        if self._behind(aid) in self.res:
            mine.add_path(self._behind(aid), self.res.path(self._behind(aid)))
        hits = []
        for other, path in self.res.paths().items():
            if other == aid or "@" in other:
                continue
            for k, (node, t) in enumerate(path):
                if t < self.step:
                    continue
                u, t0 = path[k - 1] if k else (node, t)
                if mine.node_conflict(node, t, ignore=other) or (
                        u != node and mine.edge_conflict(u, node, t0, t, ignore=other)):
                    hits.append((t, other))
                    break
        return [other for _, other in sorted(hits)]

    def repair(self, aid):
        """Replan everyone whose plan runs into aid, soonest conflict first.

        Each replan is against the current reservations, so it avoids aid and
        everyone else. An aircraft that can't be replanned (it is already
        committed to the blocked node) holds where it is instead, and its own
        hold is repaired in turn. Aircraft on a landing or takeoff roll can't
        stop; if one of those is blocked the conflict stays and is reported.
        """
        queue, held = deque([aid]), {aid}
        while queue:
            cause = queue.popleft()
            for other in self.blocked_by(cause):
                a = self.aircraft(other)
                if a.status == "holding" or not self.routed.get(other):
                    continue  # nothing planned to change
                try:
                    self._plan_and_commit(a, a.destination, self.tracks[other], retries=REPAIR_RETRIES)
                    self.repairs += 1
                    continue
                except ValueError:
                    pass
                if other in held:
                    continue
                try:
                    self._stop(a)
                except ValueError:
                    continue  # on a roll
                self.knock_on_holds += 1
                self.auto_held.add(other)
                held.add(other)
                queue.append(other)

    def _retry_stalled(self):
        """Try again to route aircraft the planner held or couldn't route automatically."""
        for group, stalled_status in ((self.auto_held, "holding"), (self.unrouted, "waiting")):
            for aid in sorted(group):
                try:
                    a = self.aircraft(aid)
                except KeyError:
                    group.discard(aid)
                    continue
                if self.routed.get(aid) if group is self.unrouted else a.status != stalled_status:
                    group.discard(aid)  # the controller took over
                    continue
                try:
                    self._plan_and_commit(a, a.destination, self.tracks[aid], retries=REPAIR_RETRIES)
                except NoRoute as exc:
                    if not self._swap_through(a, exc.blockers):
                        continue
                except ValueError:
                    continue
                if a.status in ("holding", "waiting"):
                    a.status = "taxiing"
                group.discard(aid)

    def _swap_through(self, a, blockers):
        """Plan a through stalled aircraft in its way, then replan them around it; all or nothing.

        Two aircraft stopped facing each other on one taxiway each wait for
        the other forever. Lifting the blockers' reservations lets a's plan
        pass where they stand; their replans against it then have to get out
        of the way in time (into a side taxiway, say), or nothing changes.
        Only aircraft the planner stopped or couldn't route are moved this
        way, never one the controller is holding.
        """
        movable = [b for b in blockers if b in self.auto_held or b in self.unrouted]
        if not movable or len(movable) < len(blockers):
            return False
        saved = self._snapshot()
        try:
            for b in movable:
                self._unreserve(b)
            self._plan_and_commit(a, a.destination, self.tracks[a.id], retries=REPAIR_RETRIES)
            for b in movable:
                other = self.aircraft(b)
                self._plan_and_commit(other, other.destination, self.tracks[b], retries=REPAIR_RETRIES)
                other.status = "taxiing"
                self.auto_held.discard(b)
                self.unrouted.discard(b)
        except ValueError:
            self._restore(saved)
            return False
        self.swaps += 1
        return True

    def _snapshot(self):
        everyone = self.state.aircraft + [p[2] for p in self.pending]
        return dict(
            res=copy.deepcopy(self.res), tracks=dict(self.tracks), routed=dict(self.routed),
            leaves=dict(self.leaves), departs=dict(self.departs),
            windows={k: list(v) for k, v in self.gate.windows.items()},
            auto_held=set(self.auto_held), unrouted=set(self.unrouted),
            aircraft={a.id: (a, a.status, a.route, a.destination) for a in everyone},
        )

    def _restore(self, saved):
        self.res, self.tracks, self.routed = saved["res"], saved["tracks"], saved["routed"]
        self.leaves, self.departs = saved["leaves"], saved["departs"]
        self.gate.windows = saved["windows"]
        self.auto_held, self.unrouted = saved["auto_held"], saved["unrouted"]
        for a, status, route, destination in saved["aircraft"].values():
            a.status, a.route, a.destination = status, route, destination

    def _plan_and_commit(self, a, destination, tr, from_d=0.0, retries=6):
        # A held aircraft's reservation behind it is its own; it mustn't block its replan.
        behind = self._behind(a.id)
        kept = self.res.path(behind) if behind in self.res else None
        if kept:
            self.res.remove_path(behind)
        try:
            return self._plan_and_commit_inner(a, destination, tr, from_d, retries)
        except ValueError:
            if kept and behind not in self.res:
                self.res.add_path(behind, kept, park_at_goal=True)
            raise

    def _plan_and_commit_inner(self, a, destination, tr, from_d, retries):
        """Replace a's route with a plan to destination, continuing from where it is on tr.

        The plan starts at the first planner node at least from_d meters along
        tr (an arrival's exit, rather than the exits it rolls past).
        """
        aid, t = a.id, self.now
        goal, runway = self.destination_goal(destination)
        planner = self.ap.graph
        if t < self.rollout_until.get(aid, -1):
            from_d = max(from_d, self.exit_d[aid])  # it can't turn off before its exit

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

        blockers = self._walled_off(aid, start, goal)
        if blockers is not None:
            names = ", ".join(sorted(self._callsign(b) for b in blockers)) or "traffic"
            raise NoRoute(f"{a.callsign} can't get to {destination}: {names} in the way", blockers)
        planned = plan_one(self.ap, request(), self.res, edge_allowed=allowed, max_delay_retries=retries)
        if not planned.ok:
            other = planned.blocking.other_id.split("@")[0] if planned.blocking else None
            raise NoRoute(planned.reason, [other] if other else [])
        if runway:
            # Line up and wait until nobody else is on the runway for the whole roll.
            first_end = planned.path[-1][1]
            extra = 1
            while self.runway_busy(pair, planned.path[-1][1] - hold, planned.path[-1][1], ignore=aid):
                extra *= 2  # each try is a full plan, so back off fast
                if extra * DT > MAX_LINEUP_WAIT_S:
                    raise ValueError(f"Runway {runway} won't be clear for {a.callsign}'s takeoff")
                retry = plan_one(self.ap, request(first_end + extra), self.res, edge_allowed=allowed,
                                 max_delay_retries=retries)
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

        # Until the plan takes over at `start`, the aircraft is still on its old path.
        old = self.res.path(aid) if aid in self.res else []
        driving = [step for step in old if self.step - 1 <= step[1] < t_start]
        self._reserve(aid, driving + path, park_at_goal=hold is None)
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
