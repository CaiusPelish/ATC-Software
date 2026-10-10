# backend/app/routing/ordering.py

"""Who gets planned first in prioritized planning, and the per-aircraft features for learning it.

heuristic_order is the default: earliest ready first, heavier first on ties.
order_features describes each aircraft in a batch for a learned ranker that
can replace it (see the learned-ordering phase of the MAPF plan).
"""

from functools import lru_cache

from .astar import astar

WEIGHT_RANK = {"small": 0, "large": 1, "b757": 2, "heavy": 3, "super": 4}


def heuristic_order(airport, requests):
    return [
        r.id for r in sorted(
            requests, key=lambda r: (r.ready_t, -WEIGHT_RANK[r.weight_class], r.id)
        )
    ]


@lru_cache(maxsize=4096)
def _free_flow(airport, start, goal):
    """(steps, route) of the shortest path with nobody else around."""
    result = astar(airport.steps, start, goal)
    if result is None:
        return float("inf"), ()
    return result["estimated_time"], tuple(result["route"])


def free_flow_steps(airport, start, goal):
    return _free_flow(airport, start, goal)[0]


def order_features(airport, requests):
    """{aircraft id: feature dict} for every request in the batch."""
    routes = {r.id: _free_flow(airport, r.start, r.goal) for r in requests}
    node_sets = {aid: set(route) for aid, (_, route) in routes.items()}
    edge_sets = {aid: set(zip(route, route[1:])) | set(zip(route[1:], route))
                 for aid, (_, route) in routes.items()}
    goals = [r.goal for r in requests]

    features = {}
    for r in requests:
        steps, route = routes[r.id]
        others = [o for o in requests if o.id != r.id]
        shared_nodes = [len(node_sets[r.id] & node_sets[o.id]) for o in others]
        shared_edges = [len(edge_sets[r.id] & edge_sets[o.id]) for o in others]
        features[r.id] = {
            "ready_t": r.ready_t,
            "free_flow_steps": steps,
            "weight_rank": WEIGHT_RANK[r.weight_class],
            "route_nodes": len(route),
            "overlapping_aircraft": sum(1 for s in shared_nodes if s),
            "shared_nodes": sum(shared_nodes),
            "shared_edges": sum(shared_edges),
            "earlier_ready_overlaps": sum(
                1 for o, s in zip(others, shared_nodes) if s and o.ready_t < r.ready_t
            ),
            "shared_goal": goals.count(r.goal) > 1,
            "start_degree": len(airport.graph[r.start]),
        }
    return features
