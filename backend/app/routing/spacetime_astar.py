# backend/app/routing/spacetime_astar.py

"""A* over (node, timestep) that routes one aircraft around everyone else's reservations.

From each state the aircraft can wait a step at its node or start along an
edge, which takes airport.steps[u][v] steps. A move is allowed only if the
edge is free over the whole traversal and the next node is free on arrival.
Cost is arrival time, with a tiny penalty per wait so moving wins ties.
"""

import heapq
from collections import Counter
from dataclasses import dataclass

from .airport import DT, NODE_BUFFER
from .reservations import ConflictInfo

WAIT_PENALTY = 0.01
# Extra steps of horizon beyond 4x the straight-line estimate.
HORIZON_SLACK = 120


@dataclass
class PlanResult:
    path: list[tuple[str, int]] | None
    reason: str = ""
    blocking: ConflictInfo | None = None

    @property
    def ok(self):
        return self.path is not None


def plan(airport, aid, start, goal, t_start, reservations, *, edge_allowed=None, max_t=None,
         goal_hold=None):
    """Earliest-arrival conflict-free path for aid from start (at t_start) to goal.

    goal_hold=None means the aircraft parks at goal for good (an arrival at its
    gate). An int means it holds there that many steps and then leaves the map
    (a departure lining up and rolling); the returned path includes the hold.

    edge_allowed(u, v, t_enter) -> bool lets callers gate edges by time, e.g.
    runway crossings that are only open during a crossing window.
    """
    for node in (start, goal):
        if node not in airport.graph:
            return PlanResult(None, f"Unknown node {node}.")

    clash = reservations.node_conflict(start, t_start, ignore=aid)
    if clash:
        return PlanResult(None, clash.message(airport), clash)

    parked = reservations.parked_by(goal, ignore=aid)
    if parked:
        clash = ConflictInfo("node_conflict", aid, parked, t_start, t_start, node=goal, parked=True)
        return PlanResult(
            None,
            f"{aid} can't end at {airport.label(goal)}: {parked} is holding there indefinitely.",
            clash,
        )

    if max_t is None:
        max_t = t_start + int(4 * airport.heuristic_steps(start, goal)) + HORIZON_SLACK
    goal_free_after = reservations.last_reserved(goal, ignore=aid) + NODE_BUFFER

    def h(node):
        # 0.999 absorbs the tiny difference between the flat projection and haversine lengths.
        return 0.999 * airport.heuristic_steps(node, goal)

    start_state = (start, t_start)
    g = {start_state: 0.0}
    parent = {}
    closed = set()
    blockers = Counter()
    first_blocker = {}
    counter = 0
    queue = [(h(start), counter, start_state)]

    while queue:
        _, _, state = heapq.heappop(queue)
        if state in closed:
            continue
        closed.add(state)
        node, t = state

        if node == goal and _can_finish(reservations, aid, goal, t, goal_hold, goal_free_after):
            path = _rebuild(parent, state)
            if goal_hold:
                path.extend((goal, t + k) for k in range(1, goal_hold + 1))
            return PlanResult(path)

        moves = [(node, 1)] + [(v, d) for v, d in airport.steps[node].items()]
        for nxt, d in moves:
            t_next = t + d
            if t_next > max_t:
                continue
            nstate = (nxt, t_next)
            if nstate in closed:
                continue
            if nxt != node:
                if edge_allowed and not edge_allowed(node, nxt, t):
                    continue
                clash = reservations.edge_conflict(node, nxt, t, t_next, ignore=aid)
                if clash:
                    blockers[clash.other_id] += 1
                    first_blocker.setdefault(clash.other_id, clash)
                    continue
            clash = reservations.node_conflict(nxt, t_next, ignore=aid)
            if clash:
                blockers[clash.other_id] += 1
                first_blocker.setdefault(clash.other_id, clash)
                continue
            cost = g[state] + d + (WAIT_PENALTY if nxt == node else 0)
            if cost < g.get(nstate, float("inf")):
                g[nstate] = cost
                parent[nstate] = state
                counter += 1
                heapq.heappush(queue, (cost + h(nxt), counter, nstate))

    if blockers:
        other, _ = blockers.most_common(1)[0]
        clash = first_blocker[other]
        return PlanResult(
            None,
            f"No conflict-free route for {aid} to {airport.label(goal)} by t={max_t * DT}s; "
            f"mostly blocked by {other}: {clash.message(airport)}",
            clash,
        )
    return PlanResult(None, f"No route for {aid} to {airport.label(goal)} by t={max_t * DT}s.")


def _can_finish(reservations, aid, goal, t, goal_hold, goal_free_after):
    if goal_hold is None:
        return t > goal_free_after
    return all(
        reservations.node_conflict(goal, t + k, ignore=aid) is None
        for k in range(1, goal_hold + 1)
    )


def _rebuild(parent, state):
    path = [state]
    while state in parent:
        state = parent[state]
        path.append(state)
    path.reverse()
    return path
