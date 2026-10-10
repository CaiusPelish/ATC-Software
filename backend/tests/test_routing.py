import unittest

from app.routing.astar import astar
from app.routing.graph import airport_graph
from app.routing.weights import load_weights


class RoutingTests(unittest.TestCase):
    def test_finds_lowest_cost_route(self):
        result = astar(airport_graph, "GATE_A1", "RWY_4")

        self.assertEqual(
            result,
            {
                "route": ["GATE_A1", "T1", "T3", "RWY_4"],
                "estimated_time": 17,
            },
        )

    def test_rejects_unknown_nodes(self):
        with self.assertRaisesRegex(ValueError, "Unknown start or destination"):
            astar(airport_graph, "UNKNOWN", "RWY_4")

    def test_loads_taxiway_costs(self):
        self.assertIn("segments", load_weights())


if __name__ == "__main__":
    unittest.main()
