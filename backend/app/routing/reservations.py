# backend/app/routing/reservations.py

"""Space-time reservations for planned taxi routes, with readable conflicts.

A path is a list of (node, t) steps with strictly increasing t. The same node
twice in a row is a wait; two different nodes mean the aircraft is on edge
u -> v from the first step's t to the second's. Planned aircraft stay parked on
their last node until their path is removed (a departure holding short, an
arrival at its gate).

Every conflict comes back as a ConflictInfo that names both aircraft, the
place and the times, because those messages are what the demo shows.
"""

import math
from dataclasses import dataclass

from .airport import DT, FOLLOW_GAP, NODE_BUFFER, get_airport

INF = math.inf


@dataclass(frozen=True)
class ConflictInfo:
    kind: str  # "node_conflict" | "edge_swap" | "follow_gap"
    aircraft_id: str
    other_id: str
    t: int
    other_t: float
    node: str | None = None
    edge: tuple[str, str] | None = None
    parked: bool = False

    def message(self, airport=None):
        airport = airport or get_airport()
        a, b = self.aircraft_id, self.other_id
        if self.kind == "node_conflict":
            place = airport.label(self.node)
            if self.parked:
                return (f"{a} would occupy {place} at t={self.t * DT}s — "
                        f"{b} is holding there from t={self.other_t * DT}s.")
            return (f"{a} would occupy {place} at t={self.t * DT}s — "
                    f"{b} reserved it at t={self.other_t * DT}s.")
        u, v = (airport.label(n) for n in self.edge)
        if self.kind == "edge_swap":
            return (f"Head-on conflict on {u}→{v}: {a} and {b} would meet on the same "
                    f"taxiway segment between t={self.t * DT}s and t={self.other_t * DT}s.")
        gap = abs(self.t - self.other_t) * DT
        return (f"{a} would enter {u}→{v} {gap}s behind {b}; "
                f"minimum following gap is {FOLLOW_GAP * DT}s.")


def path_occupancy(path):
    """(node_times, edges) of a path: [(node, t)] and [(u, v, t0, t1)]."""
    node_times = list(path)
    edges = [
        (u, v, t0, t1)
        for (u, t0), (v, t1) in zip(path, path[1:])
        if u != v
    ]
    return node_times, edges


def _edge_clash(aid, u, v, t0, t1, other, ou, ov, ot0, ot1, follow_gap=FOLLOW_GAP):
    """ConflictInfo if traversing u->v over [t0, t1] clashes with other on ou->ov."""
    if (ou, ov) == (v, u) and t0 < ot1 and ot0 < t1:
        return ConflictInfo("edge_swap", aid, other, max(t0, ot0), min(t1, ot1), edge=(u, v))
    if (ou, ov) == (u, v) and abs(t0 - ot0) < follow_gap:
        return ConflictInfo("follow_gap", aid, other, t0, ot0, edge=(u, v))
    return None


class ReservationTable:
    """node_buffer and follow_gap (in steps) default to the airport-wide spacing."""

    def __init__(self, node_buffer=NODE_BUFFER, follow_gap=FOLLOW_GAP):
        self.node_buffer = node_buffer
        self.follow_gap = follow_gap
        self._nodes = {}    # node -> {t: [aid, ...]}
        self._edges = {}    # frozenset{u, v} -> [(aid, u, v, t0, t1)]
        self._parked = {}   # node -> [(aid, t_from)]
        self._paths = {}    # aid -> (path, park_at_goal)

    def add_path(self, aid, path, park_at_goal=True):
        if aid in self._paths:
            self.remove_path(aid)
        node_times, edges = path_occupancy(path)
        for node, t in node_times:
            self._nodes.setdefault(node, {}).setdefault(t, []).append(aid)
        for u, v, t0, t1 in edges:
            self._edges.setdefault(frozenset((u, v)), []).append((aid, u, v, t0, t1))
        if park_at_goal and path:
            node, t = path[-1]
            self._parked.setdefault(node, []).append((aid, t))
        self._paths[aid] = (list(path), park_at_goal)

    def remove_path(self, aid):
        path, park_at_goal = self._paths.pop(aid)
        node_times, edges = path_occupancy(path)
        for node, t in node_times:
            at = self._nodes[node][t]
            at.remove(aid)
            if not at:
                del self._nodes[node][t]
        for u, v, t0, t1 in edges:
            self._edges[frozenset((u, v))].remove((aid, u, v, t0, t1))
        if park_at_goal and path:
            node, t = path[-1]
            self._parked[node].remove((aid, t))

    def paths(self):
        return {aid: path for aid, (path, _) in self._paths.items()}

    def path(self, aid):
        return self._paths[aid][0]

    def __contains__(self, aid):
        return aid in self._paths

    def node_conflict(self, node, t, ignore=None):
        """First reservation that stops `ignore` from being on node at t, or None."""
        at = self._nodes.get(node)
        if at:
            for dt in range(-self.node_buffer, self.node_buffer + 1):
                for other in at.get(t + dt, ()):
                    if other != ignore:
                        return ConflictInfo("node_conflict", ignore, other, t, t + dt, node=node)
        for other, t_from in self._parked.get(node, ()):
            if other != ignore and t >= t_from - self.node_buffer:
                return ConflictInfo("node_conflict", ignore, other, t, t_from, node=node, parked=True)
        return None

    def edge_conflict(self, u, v, t0, t1, ignore=None):
        """First reservation that stops `ignore` traversing u -> v over [t0, t1], or None."""
        for other, ou, ov, ot0, ot1 in self._edges.get(frozenset((u, v)), ()):
            if other == ignore:
                continue
            clash = _edge_clash(ignore, u, v, t0, t1, other, ou, ov, ot0, ot1, self.follow_gap)
            if clash:
                return clash
        return None

    def last_reserved(self, node, ignore=None):
        """Last t anyone but `ignore` is on node; INF if someone parks there, -1 if nobody."""
        if any(other != ignore for other, _ in self._parked.get(node, ())):
            return INF
        times = [t for t, aids in self._nodes.get(node, {}).items() if any(a != ignore for a in aids)]
        return max(times, default=-1)

    def reserved_times(self, node, ignore=None):
        """Every t at which anyone but `ignore` is on node."""
        return [t for t, aids in self._nodes.get(node, {}).items() if any(a != ignore for a in aids)]

    def parked_by(self, node, ignore=None):
        return next((a for a, _ in self._parked.get(node, ()) if a != ignore), None)


def check_paths(paths, park_at_goal=True, node_buffer=NODE_BUFFER, follow_gap=FOLLOW_GAP):
    """Every conflict between any two paths, by brute force. Independent of ReservationTable."""
    occ = {aid: path_occupancy(p) for aid, p in paths.items()}
    ids = sorted(paths)
    conflicts = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            conflicts.extend(_pair_conflicts(a, b, paths, occ, park_at_goal, node_buffer, follow_gap))
    return conflicts


def _pair_conflicts(a, b, paths, occ, park_at_goal, node_buffer, follow_gap):
    found = []
    (a_nodes, a_edges), (b_nodes, b_edges) = occ[a], occ[b]
    for node, t in a_nodes:
        for other_node, ot in b_nodes:
            if node == other_node and abs(t - ot) <= node_buffer:
                found.append(ConflictInfo("node_conflict", a, b, t, ot, node=node))
    if park_at_goal:
        for x, y, x_nodes in ((a, b, a_nodes), (b, a, b_nodes)):
            goal, t_from = paths[y][-1]
            for node, t in x_nodes:
                if node == goal and t >= t_from - node_buffer:
                    found.append(ConflictInfo("node_conflict", x, y, t, t_from, node=node, parked=True))
    for u, v, t0, t1 in a_edges:
        for ou, ov, ot0, ot1 in b_edges:
            clash = _edge_clash(a, u, v, t0, t1, b, ou, ov, ot0, ot1, follow_gap)
            if clash:
                found.append(clash)
    return found
