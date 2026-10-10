"""The 22L/22R arrival push: everyone reaches the ramp, nobody conflicts, nobody enters a busy runway."""

import unittest

from app.routing.airport import get_airport
from app.routing.mapf import plan_batch
from app.routing.reservations import check_paths
from app.routing.runways import exits, load_runways, runway_conflicts
from app.simulation.arrivals import simulate


def u_turns(path):
    return sum(1 for (a, _), (b, _), (c, _) in zip(path, path[1:], path[2:]) if a == c and a != b)


class RunwayModelTest(unittest.TestCase):
    def test_fitted_runways_match_published_lengths(self):
        runways = load_runways()
        self.assertAlmostEqual(runways["22R"].length_m, 3353, delta=120)  # 11,000 ft
        self.assertAlmostEqual(runways["22L"].length_m, 3048, delta=120)  # 10,000 ft
        self.assertAlmostEqual(runways["22L"].heading_true, 206, delta=2)

    def test_terminal_side_exits(self):
        ap = get_airport()
        runways = load_runways()
        names = {ap.label(n).split(" (")[0] for _, n in exits(runways["22L"], "right")}
        self.assertTrue({"P1", "V", "N", "P2", "E", "G", "J"} <= names, names)


class ArrivalPushTest(unittest.TestCase):
    def test_arrivals_are_safe_across_seeds(self):
        for seed in range(1, 6):
            arrivals, requests, result, gate, _ = simulate(20, seed)
            self.assertEqual(result.failures, {}, f"seed {seed}")
            self.assertEqual(check_paths(result.paths, park_at_goal=False), [], f"seed {seed}")
            self.assertEqual(runway_conflicts(gate, result.paths), [], f"seed {seed}")
            self.assertEqual(sum(u_turns(p) for p in result.paths.values()), 0, f"seed {seed}")

    def test_gate_is_what_prevents_incursions(self):
        _, requests, _, gate, _ = simulate(20, 2)
        ungated = plan_batch(get_airport(), requests)
        self.assertGreater(len(runway_conflicts(gate, ungated.paths)), 0)

    def test_arrivals_are_separated(self):
        arrivals = simulate(15, 7)[0]
        for a, b in zip(arrivals, arrivals[1:]):
            self.assertGreaterEqual(b.touchdown_s - a.touchdown_s, 39)  # 1.5 nm at 140 kt
        last = {}
        for a in arrivals:
            if a.runway in last:
                self.assertGreater(a.touchdown_s, last[a.runway].vacate_s)
            last[a.runway] = a
