"""Plan every routable departure in the real 2-hour KEWR recording at once."""

import time
import unittest

from app.routing.airport import DT, get_airport
from app.routing.mapf import plan_batch
from app.routing.reservations import check_paths

try:
    from app.simulation.traffic import TRAFFIC_CSV, load_departures
except ImportError as err:  # pandas missing
    load_departures, SKIP = None, f"needs pandas ({err})"
else:
    SKIP = None if TRAFFIC_CSV.exists() else f"{TRAFFIC_CSV.name} not found"


@unittest.skipIf(SKIP, SKIP)
class RealTrafficReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ap = get_airport()
        cls.departures = load_departures()
        t = time.perf_counter()
        cls.result = plan_batch(cls.ap, [r for r, _ in cls.departures])
        cls.elapsed = time.perf_counter() - t

    def test_found_departures(self):
        self.assertGreater(len(self.departures), 5)

    def test_no_conflicts(self):
        self.assertEqual(check_paths(self.result.paths, park_at_goal=False), [])

    def test_every_flight_planned_or_explained(self):
        for request, _ in self.departures:
            if request.id not in self.result.paths:
                self.assertTrue(self.result.failures[request.id])

    def test_report(self):
        planned_total = actual_total = 0
        print(f"\n{'flight':>9} {'ready':>6} {'planned':>8} {'actual':>7}")
        for request, actual_s in self.departures:
            path = self.result.paths.get(request.id)
            if path is None:
                print(f"{request.id:>9} {request.ready_t * DT:>5}s   FAILED  {self.result.failures[request.id]}")
                continue
            planned_s = (path[-1][1] - path[0][1] - (request.goal_hold or 0)) * DT
            planned_total += planned_s
            actual_total += actual_s
            print(f"{request.id:>9} {request.ready_t * DT:>5}s {planned_s:>7}s {actual_s:>6}s")
        print(f"{len(self.result.paths)}/{len(self.departures)} planned in {self.elapsed * 1000:.0f} ms; "
              f"taxi time planned {planned_total}s vs actual {actual_total}s; "
              f"delay from other traffic {self.result.total_delay_steps * DT}s")
