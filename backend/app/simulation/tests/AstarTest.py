# backend/app/simulation/tests/AstarTest.py

"""A* on the real KEWR taxiway graph, checked against a departing flight.

Give it a flight ID from data/kewr_194_2h.csv. It first checks the recording
holds that flight's whole taxi, from first appearing on the ground to lining
up for takeoff. If not, it prints a warning and exits. If so, it asks A* for a
route between the same two points, saves data/flight_<id>_route.png (A* route
next to the raw GPS), and runs the tests: the route is valid, no longer than
what the plane actually taxied, and the same length on the simplified graph
as on the full OSM graph.

Run:  python3 AstarTest.py [flight_id]      (default 6509825)
"""

import functools
import math
import sys
import unittest
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.routing.astar import astar  # noqa: E402
from app.routing.osm_taxiways import (  # noqa: E402
    DATA_DIR,
    haversine_m,
    load_saved_graph,
    snap_to_nearest_edge,
    taxiway_sequence,
)
from app.simulation.plot_flight_route import (  # noqa: E402
    TAKEOFF_ROLL_SPEED,
    get_taxi_points,
    plot_flight_route,
)

# Taken off argv before unittest sees it.
FLIGHT_ID = int(sys.argv.pop(1)) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 6509825

TRAFFIC_CSV = DATA_DIR.parent / "kewr_194_2h.csv"

# Amelia reports 12.5 ft for aircraft on the ground at KEWR.
GROUND_ALTITUDE_FT = 20
# Less taxi than this isn't a route worth testing (e.g. first seen already lined up).
MIN_TAXI_SECONDS = 60
# Start and end points further than this from a taxiway (e.g. at a gate) can't be routed.
MAX_SNAP_M = 50
# KEWR magnetic variation (west), to turn the takeoff's true heading into a runway number.
MAGNETIC_VARIATION_DEG = 13


class NotEnoughHistory(Exception):
    """The recording doesn't hold this flight's whole taxi to takeoff."""


def path_length_m(points):
    return sum(
        haversine_m(a[0], a[1], b[0], b[1])
        for a, b in zip(points, points[1:])
    )


def runway_number(flight):
    """Runway number from the heading at the start of the takeoff roll, e.g. '04'."""
    roll = flight[flight["Speed"] > TAKEOFF_ROLL_SPEED].head(10)
    rad = roll["Heading"].map(math.radians)
    true_heading = math.degrees(math.atan2(rad.map(math.sin).mean(), rad.map(math.cos).mean()))
    magnetic = (true_heading + MAGNETIC_VARIATION_DEG) % 360
    number = round(magnetic / 10) % 36 or 36
    return f"{number:02d}"


def snap_to_graph_node(nodes, graph, geometry, lat, lon):
    """Nearest end of the nearest taxiway edge, and how far the point is from it."""
    hit = snap_to_nearest_edge(nodes, graph, lat, lon, MAX_SNAP_M, geometry)
    if hit is None:
        return None
    a, b, _, t = hit
    node = a if t < 0.5 else b
    return node, haversine_m(lat, lon, node.lat, node.lon)


@functools.lru_cache
def prepare_flight(flight_id):
    """Check the flight's history, then run A* for it. Raises NotEnoughHistory."""
    df = pd.read_csv(TRAFFIC_CSV)
    flight = df[df["ID"] == flight_id].sort_values("Frame")

    if flight.empty:
        raise NotEnoughHistory(f"is not in {TRAFFIC_CSV.name}")
    if (flight["Type"] != 0).all():
        raise NotEnoughHistory("is a ground vehicle or unknown target, not an aircraft")

    first = flight.iloc[0]
    if first["Altitude"] > GROUND_ALTITUDE_FT:
        raise NotEnoughHistory(
            f"is already in the air ({first['Altitude']:.0f} ft) when first seen, so it's an "
            "arrival or overflight; only departures are supported"
        )
    if first["Speed"] > TAKEOFF_ROLL_SPEED:
        raise NotEnoughHistory(f"is already on its takeoff roll ({first['Speed']:.0f} kt) when first seen")
    if not (flight["Speed"] > TAKEOFF_ROLL_SPEED).any():
        raise NotEnoughHistory(
            "never starts a takeoff roll before the recording ends, so its taxi has no known end "
            "(arrivals and planes still taxiing aren't supported)"
        )

    taxi = get_taxi_points(flight_id, TRAFFIC_CSV)
    if len(taxi) < MIN_TAXI_SECONDS:
        raise NotEnoughHistory(f"has only {len(taxi)} s of taxi before takeoff (need {MIN_TAXI_SECONDS} s)")

    nodes, graph, geometry = load_saved_graph("KEWR", simple=True)
    start = snap_to_graph_node(nodes, graph, geometry, *taxi[0])
    goal = snap_to_graph_node(nodes, graph, geometry, *taxi[-1])
    if start is None:
        raise NotEnoughHistory(f"starts more than {MAX_SNAP_M} m from any taxiway (e.g. at a gate)")
    if goal is None:
        raise NotEnoughHistory(f"lines up more than {MAX_SNAP_M} m from any taxiway")
    if start[0].id == goal[0].id:
        raise NotEnoughHistory("starts and lines up at the same taxiway node; there's no route to find")

    result = astar(graph, start[0].id, goal[0].id)
    if result is None:
        raise NotEnoughHistory("has no taxiway path in the graph between its start and line-up points")

    note = None
    if first["UnixTime"] == df["UnixTime"].min():
        note = "already on the ground when the recording starts, so its taxi may have begun earlier"

    return {
        "nodes": nodes,
        "graph": graph,
        "geometry": geometry,
        "points": flight[["Lat", "Lon"]].to_numpy(),
        "taxi": taxi,
        "start": start[0],
        "start_dist": start[1],
        "goal": goal[0],
        "goal_dist": goal[1],
        "result": result,
        "runway": runway_number(flight),
        "note": note,
    }


class AstarOnFlight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            run = prepare_flight(FLIGHT_ID)
        except NotEnoughHistory as err:
            raise unittest.SkipTest(f"flight {FLIGHT_ID} {err}")
        for key, value in run.items():
            setattr(cls, key, value)
        cls.full_nodes, cls.full_graph, _ = load_saved_graph("KEWR", simple=False)

    def test_taxi_history_long_enough(self):
        self.assertEqual(self.points.shape[1], 2)
        self.assertGreaterEqual(len(self.taxi), MIN_TAXI_SECONDS)
        self.assertLess(len(self.taxi), len(self.points))

    def test_route_found(self):
        self.assertIsNotNone(self.result)
        self.assertEqual(self.result["route"][0], self.start.id)
        self.assertEqual(self.result["route"][-1], self.goal.id)

    def test_route_follows_graph_edges(self):
        route = self.result["route"]
        for a, b in zip(route, route[1:]):
            self.assertIn(b, self.graph[a], f"{a} -> {b} is not an edge")

    def test_cost_matches_edges(self):
        route = self.result["route"]
        total = sum(self.graph[a][b] for a, b in zip(route, route[1:]))
        self.assertAlmostEqual(self.result["estimated_time"], total, places=3)

    def test_route_not_shorter_than_straight_line(self):
        straight = haversine_m(self.start.lat, self.start.lon, self.goal.lat, self.goal.lon)
        self.assertGreaterEqual(self.result["estimated_time"], straight)

    def test_route_not_longer_than_actual_taxi(self):
        # GPS jitter while the plane sits in the queue only adds distance,
        # so the shortest path should never beat what it actually taxied.
        actual = path_length_m(self.taxi)
        slack = self.start_dist + self.goal_dist
        self.assertLessEqual(self.result["estimated_time"], actual + slack)

    def test_simple_graph_keeps_distances(self):
        # Every simplified node is also in the full graph, so A* between the
        # same two nodes must cost the same in both if merging kept lengths exact.
        full = astar(self.full_graph, self.start.id, self.goal.id)
        self.assertAlmostEqual(self.result["estimated_time"], full["estimated_time"], delta=0.05)

    def test_simple_graph_only_keeps_meaningful_nodes(self):
        neighbours = {k: set() for k in self.graph}
        for a, nbrs in self.graph.items():
            for b in nbrs:
                neighbours[a].add(b)
                neighbours[b].add(a)
        for node_id, node in self.nodes.items():
            if len(neighbours[node_id]) == 2:
                self.assertTrue(node.tags, f"{node_id} is a plain shape point")
        self.assertLess(len(self.nodes), len(self.full_nodes) / 2)


def warn(message):
    colour = sys.stderr.isatty()
    print(f"{'\033[33m' if colour else ''}WARNING: {message}{'\033[0m' if colour else ''}",
          file=sys.stderr)


if __name__ == "__main__":
    try:
        run = prepare_flight(FLIGHT_ID)
    except NotEnoughHistory as err:
        warn(f"flight {FLIGHT_ID} {err}.\n"
             "Not enough history for a full taxi route; nothing was plotted or tested.")
        sys.exit(1)

    if run["note"]:
        warn(f"flight {FLIGHT_ID} was {run['note']}.")

    route = run["result"]["route"]
    print(f"Flight {FLIGHT_ID}: {len(run['points'])} points, {len(run['taxi'])} while taxiing")
    print(f"Actual taxi distance: {path_length_m(run['taxi']):.0f} m")
    print(f"A* route: {len(route)} nodes, {run['result']['estimated_time']:.0f} m, "
          f"via {taxiway_sequence(run['nodes'], route)} to runway {run['runway']}")

    out = plot_flight_route(
        run["nodes"], run["graph"], route, run["taxi"], FLIGHT_ID,
        DATA_DIR.parent / f"flight_{FLIGHT_ID}_route.png",
        runway=run["runway"], geometry=run["geometry"], route_label="A* route",
    )
    print(f"Saved {out}\n")

    unittest.main(verbosity=2)
