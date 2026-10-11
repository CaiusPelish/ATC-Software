"""Starting setups for the live simulation (POST /api/scenario/load)."""

import copy

from ..routing.graph import airport_graph, ground_geometry, ground_kinds, ground_nodes
from ..routing.osm_taxiways import edge_points, haversine_m
from ..routing.runways import _to_xy
from .live import ARRIVAL_RUNWAY, LiveSim

# How far challenge_01's truck stays from where the arrivals will taxi, so
# it raises nothing until the controller moves it.
TRUCK_CLEARANCE_M = 100


def _default():
    """Endless traffic: arrivals on 22L, departures off 22R, all routed by the planner.

    Two departures are already taxiing at the start, so the map isn't empty
    while the first arrival is still on final.
    """
    sim = LiveSim()
    for _ in range(2):
        sim.spawn_departure()
    sim.tick(0)
    return sim


def _empty():
    """Nothing moves until aircraft are spawned (POST /api/aircraft/spawn) and routed."""
    return LiveSim(auto_spawn=False)


def _truck_spot(sim, first):
    """A node one or two hops off 22L/4R, TRUCK_CLEARANCE_M from every arrival's way, nearest first's exit."""
    # Where first will really go once routed: the planner's path, not the shortest one.
    dry = copy.deepcopy(sim)
    dry.route(first.id, first.destination)
    routes = [dry.tracks[first.id].route] + [a.route for _, _, a in sim.pending]
    taken = [p for route in routes for u, v in zip(route, route[1:])
             for p in edge_points(ground_nodes, ground_geometry, u, v)]

    def clearance(n):
        g = ground_nodes[n]
        return min(haversine_m(g.lat, g.lon, *p) for p in taken)

    def gap(a, b):
        return haversine_m(ground_nodes[a].lat, ground_nodes[a].lon, ground_nodes[b].lat, ground_nodes[b].lon)

    on_runway = {n for n, kind in ground_kinds.items() if kind.startswith("runway")}
    rw = sim.runways[ARRIVAL_RUNWAY]
    runway = [n for n in on_runway if rw.on_runway_xy(*_to_xy(sim.ap, ground_nodes[n].lat, ground_nodes[n].lon))]
    near = {n for r in runway for n in airport_graph[r] if n not in on_runway}
    near |= {m for n in near for m in airport_graph[n] if m not in on_runway}
    exit_node = first.route[-1]
    return min((n for n in near if clearance(n) >= TRUCK_CLEARANCE_M), key=lambda n: gap(n, exit_node))


def _challenge_01():
    """An arrival stopped on 22L at its exit, a truck parked by the runway, the next landing two minutes out.

    Nothing is routed for you: get the first arrival off the runway before the
    second one touches down, or it's a runway incursion. The truck is out of
    everyone's way until it's driven onto the runway.
    """
    sim = LiveSim(auto_spawn=False, auto_route=False, seed=1)
    first = sim.spawn_arrival(touchdown_s=-60)
    sim.spawn_arrival(touchdown_s=120, force=True)
    sim.add_vehicle("TRUCK01", [_truck_spot(sim, first)])
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
