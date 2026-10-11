import unittest

from app.routing.astar import astar
from app.routing.graph import airport_graph
from app.routing.weights import load_weights


class RoutingTests(unittest.TestCase):
    def test_finds_route_on_ground_graph(self):
        # A/RH intersection to the CC ramp end, on the real KEWR ground graph.
        start, goal = "408963125", "45452380"
        result = astar(airport_graph, start, goal)

        route = result["route"]
        self.assertEqual(route[0], start)
        self.assertEqual(route[-1], goal)
        for a, b in zip(route, route[1:]):
            self.assertIn(b, airport_graph[a])
        self.assertAlmostEqual(
            result["estimated_time"],
            sum(airport_graph[a][b] for a, b in zip(route, route[1:])),
        )

    def test_rejects_unknown_nodes(self):
        with self.assertRaisesRegex(ValueError, "Unknown start or destination"):
            astar(airport_graph, "UNKNOWN", "RWY_4")

    def test_loads_taxiway_costs(self):
        self.assertIn("segments", load_weights())


if __name__ == "__main__":
    unittest.main()
