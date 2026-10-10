# backend/app/routing/mapf.py

"""Prioritized multi-aircraft planning on top of space-time A*.

Aircraft are planned one at a time in priority order. Each finished path goes
into the reservation table, so later aircraft wait or detour around earlier
ones. If an aircraft can't get a route, it is delayed at its start (it
pushes back later) and retried with a longer horizon before giving up with a
reason.
"""

from dataclasses import dataclass, field

from . import spacetime_astar
from .ordering import free_flow_steps, heuristic_order
from .reservations import ReservationTable

WEIGHT_CLASSES = ("small", "large", "b757", "heavy", "super")


@dataclass
class AircraftRequest:
    id: str
    start: str
    goal: str
    ready_t: int = 0
    weight_class: str = "large"
    callsign: str = ""
    # Steps to hold at the goal before leaving the map (a departure's line-up
    # and takeoff roll); None parks there until the path is removed.
    goal_hold: int | None = None
    # Stay at the goal until at least this step (e.g. a departure's takeoff slot).
    hold_until: int | None = None

    def __post_init__(self):
        if self.weight_class not in WEIGHT_CLASSES:
            raise ValueError(f"Unknown weight class {self.weight_class!r}")
        self.callsign = self.callsign or self.id


@dataclass
class BatchResult:
    paths: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    delays: dict = field(default_factory=dict)  # aid -> steps later than free-flow arrival
    order: list = field(default_factory=list)
    reservations: ReservationTable | None = None

    @property
    def total_delay_steps(self):
        return sum(self.delays.values())

    @property
    def makespan(self):
        return max((p[-1][1] for p in self.paths.values()), default=0)


def plan_batch(airport, requests, reservations=None, *, edge_allowed=None, edge_allowed_for=None,
               order=None, max_delay_retries=6, hold_starts=0):
    """Plan every request; returns a BatchResult. Mutates `reservations` if given.

    order(airport, requests) -> [aircraft ids] picks who goes first; defaults
    to heuristic_order. edge_allowed_for(request) -> edge_allowed gives each
    aircraft its own hook (e.g. a runway gate that ignores its own roll);
    otherwise edge_allowed is shared.

    hold_starts=k reserves every aircraft's start node for k steps from its
    ready time before anyone is planned. Without it, an aircraft planned late
    can find its own starting spot taken by someone planned earlier, which
    matters when planning order isn't arrival order (e.g. by takeoff slot).
    """
    reservations = reservations if reservations is not None else ReservationTable()
    by_id = {r.id: r for r in requests}
    ids = (order or heuristic_order)(airport, requests)
    if sorted(ids) != sorted(by_id):
        raise ValueError("order must return each request id exactly once")

    result = BatchResult(order=list(ids), reservations=reservations)
    placeholder = {}
    if hold_starts:
        for r in requests:
            placeholder[r.id] = f"{r.id}@start"
            steps = [(r.start, r.ready_t + k) for k in range(hold_starts + 1)]
            reservations.add_path(placeholder[r.id], steps, park_at_goal=False)
    for aid in ids:
        r = by_id[aid]
        if aid in placeholder:
            reservations.remove_path(placeholder.pop(aid))
        gate = edge_allowed_for(r) if edge_allowed_for else edge_allowed
        planned = plan_one(airport, r, reservations, edge_allowed=gate,
                           max_delay_retries=max_delay_retries)
        if planned.ok:
            reservations.add_path(aid, planned.path, park_at_goal=r.goal_hold is None and r.hold_until is None)
            result.paths[aid] = planned.path
            ideal = r.ready_t + free_flow_steps(airport, r.start, r.goal) + (r.goal_hold or 0)
            if r.hold_until is not None:
                ideal = max(ideal, r.hold_until)
            result.delays[aid] = planned.path[-1][1] - ideal
        else:
            result.failures[aid] = planned.reason
    return result


def plan_one(airport, request, reservations, *, edge_allowed=None, max_delay_retries=6):
    """Space-time A* for one request, with delay-and-retry. Does not reserve the result."""
    r = request
    planned = None
    for k in range(max_delay_retries + 1):
        delay = 0 if k == 0 else 2 ** (k - 1)
        t_start = r.ready_t + delay
        horizon = int(4 * airport.heuristic_steps(r.start, r.goal)) + spacetime_astar.HORIZON_SLACK * (k + 1)
        planned = spacetime_astar.plan(
            airport, r.id, r.start, r.goal, t_start, reservations,
            edge_allowed=edge_allowed, max_t=max(t_start + horizon, (r.hold_until or 0) + horizon),
            goal_hold=r.goal_hold, hold_until=r.hold_until,
        )
        if planned.ok:
            return planned
        if planned.blocking and planned.blocking.parked and planned.blocking.node == r.goal:
            break  # someone holds the goal for good; waiting won't help
    return planned


def position_at(path, t):
    """(node, t_free): the node the aircraft is at, or heading to, at time t, and when it's there."""
    for node, step_t in path:
        if step_t >= t:
            return node, step_t
    return path[-1][0], t


def replan(airport, request, reservations, t_now, *, new_goal=None, edge_allowed=None):
    """Re-route an already-planned aircraft from where it is at t_now.

    Keeps everything the aircraft has done up to its current node, plans the
    rest (to new_goal if given), and restores the old path if no route is
    found. Returns the PlanResult; on success its path is the full new path.
    """
    old = reservations.path(request.id)
    node, t_from = position_at(old, t_now)
    done = [s for s in old if s[1] < t_from]
    goal = new_goal or request.goal

    reservations.remove_path(request.id)
    planned = spacetime_astar.plan(airport, request.id, node, goal, t_from, reservations,
                                   edge_allowed=edge_allowed, goal_hold=request.goal_hold,
                                   hold_until=request.hold_until)
    park = request.goal_hold is None and request.hold_until is None
    if planned.ok:
        planned.path = done + planned.path
        reservations.add_path(request.id, planned.path, park_at_goal=park)
    else:
        reservations.add_path(request.id, old, park_at_goal=park)
    return planned
