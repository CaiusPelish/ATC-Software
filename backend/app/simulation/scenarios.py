# backend/app/simulation/scenarios.py

"""Starting setups for the live simulation (POST /api/scenario/load)."""

from ..routing.astar import astar
from ..routing.graph import airport_graph, ground_kinds
from .live import LiveSim


def _default():
    """Endless traffic: arrivals on 22L, departures off 22R, all routed by the planner."""
    return LiveSim()


def _empty():
    """Nothing moves until aircraft are spawned (POST /api/aircraft/spawn) and routed."""
    return LiveSim(auto_spawn=False)


def _challenge_01():
    """An arrival stopped on 22L at its exit, a truck next to it, the next landing two minutes out.

    Nothing is routed for you: get the first arrival off the runway before the
    second one touches down, or it's a runway incursion.
    """
    sim = LiveSim(auto_spawn=False, auto_route=False, seed=1)
    first = sim.spawn_arrival(touchdown_s=-60)
    exit_node = first.route[-1]
    # Near the exit, but off the runway and off the way to the ramp.
    on_runway = {n for n, kind in ground_kinds.items() if kind.startswith("runway")}
    way_out = set(astar(airport_graph, exit_node, first.destination)["route"])
    truck_at = next(m for n in airport_graph[exit_node] if n not in on_runway
                    for m in airport_graph[n] if m not in on_runway | way_out)
    sim.add_vehicle("TRUCK01", [truck_at])
    sim.spawn_arrival(touchdown_s=120, force=True)
    sim.tick(0)
    return sim


SCENARIOS = {
    "default": _default,
    "empty": _empty,
    "challenge_01": _challenge_01,
}


def load_scenario(scenario_id: str):
    if scenario_id not in SCENARIOS:
        raise ValueError("Scenario not found")
    return SCENARIOS[scenario_id]()
