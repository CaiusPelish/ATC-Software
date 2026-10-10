import random
import unittest

from app.routing.airport import get_airport
from app.routing.astar import astar
from app.routing.reservations import ReservationTable, check_paths
from app.routing.spacetime_astar import plan

A_RJ, A_RH, A_RN = "408963114", "408963125", "408967521"


class SpacetimeAstarTest(unittest.TestCase):
    def setUp(self):
        self.ap = get_airport()
        self.table = ReservationTable()

    def test_empty_airport_matches_static_astar(self):
        rng = random.Random(3)
        ids = sorted(self.ap.nodes)
        for _ in range(25):
            start, goal = rng.sample(ids, 2)
            result = plan(self.ap, "N1", start, goal, 10, self.table)
            static = astar(self.ap.steps, start, goal)
            self.assertTrue(result.ok, result.reason)
            self.assertEqual(result.path[0], (start, 10))
            self.assertEqual(result.path[-1][0], goal)
            self.assertEqual(result.path[-1][1] - 10, static["estimated_time"])
            for (u, t0), (v, t1) in zip(result.path, result.path[1:]):
                self.assertEqual(t1 - t0, 1 if u == v else self.ap.steps[u][v])

    def test_blocked_junction_forces_wait_or_detour(self):
        free = plan(self.ap, "N1", A_RJ, A_RN, 0, self.table)
        arrive_rh = dict(free.path)[A_RH]
        self.table.add_path("X", [(A_RH, t) for t in range(arrive_rh + 6)], park_at_goal=False)
        blocked = plan(self.ap, "N1", A_RJ, A_RN, 0, self.table)
        self.assertTrue(blocked.ok, blocked.reason)
        self.assertGreater(blocked.path[-1][1], free.path[-1][1])
        self.assertEqual(check_paths({"N1": blocked.path, "X": self.table.path("X")}, park_at_goal=False), [])

    def test_head_on_pair_resolved(self):
        first = plan(self.ap, "N1", A_RJ, A_RN, 0, self.table)
        self.table.add_path("N1", first.path)
        second = plan(self.ap, "N2", A_RN, A_RJ, 0, self.table)
        self.assertTrue(second.ok, second.reason)
        self.assertEqual(check_paths({"N1": first.path, "N2": second.path}), [])

    def test_goal_held_forever_is_unreachable(self):
        self.table.add_path("X", [(A_RN, 0)])
        result = plan(self.ap, "N1", A_RJ, A_RN, 0, self.table)
        self.assertFalse(result.ok)
        self.assertIn("X is holding there indefinitely", result.reason)
        self.assertEqual(result.blocking.other_id, "X")

    def test_edge_gate_hook(self):
        closed = {(A_RJ, A_RH), (A_RH, A_RJ)}
        result = plan(self.ap, "N1", A_RJ, A_RN, 0, self.table,
                      edge_allowed=lambda u, v, t: (u, v) not in closed)
        self.assertTrue(result.ok)
        steps = [n for n, _ in result.path]
        self.assertFalse(any(pair in closed for pair in zip(steps, steps[1:])))
