import math
import unittest

from app.alerts import fatigue
from app.alerts.manager import calculate_alert_severity
from app.routing.graph import airport_graph, ground_kinds, ground_nodes
from app.simulation.live import LiveSim
from app.simulation.scenarios import CHALLENGE_TOUCHDOWN_S, load_scenario

TICK = 0.25


def run(sim, seconds):
    for _ in range(int(seconds / TICK)):
        sim.tick(TICK)


class AlertTests(unittest.TestCase):
    def tearDown(self):
        fatigue.report(0)

    def test_fatigue_raises_warning_severity(self):
        self.assertEqual(calculate_alert_severity("warning", 0.2), "medium")
        self.assertEqual(calculate_alert_severity("warning", 0.8), "high")
        self.assertEqual(calculate_alert_severity("critical", 0.0), "critical")

    def test_fatigue_report_goes_stale(self):
        fatigue.report(80, now=1000.0)
        self.assertEqual(fatigue.snapshot(now=1005.0)["score"], 80)
        self.assertEqual(fatigue.snapshot(now=1000.0 + fatigue.STALE_AFTER_S + 1)["score"], 0.0)

    def _truck_on_runway(self):
        """challenge_01 with the truck driven onto 22L once the runway has closed."""
        sim = load_scenario("challenge_01")
        first = next(a for a in sim.state.aircraft if a.status == "waiting")
        sim.route(first.id, first.destination)
        truck = ground_nodes[sim.state.vehicles[0].position]
        crossing = min((n for n in airport_graph if n.startswith("x_4R/22L")),
                       key=lambda n: math.dist((ground_nodes[n].lat, ground_nodes[n].lon), (truck.lat, truck.lon)))
        run(sim, 15)  # the runway closes about 51 s before touchdown
        sim.route_vehicle("TRUCK01", crossing)
        for _ in range(int(30 / TICK)):
            sim.tick(TICK)
            conflict = next((c for c in sim.state.conflicts if "/" in c.location and "TRUCK01" in c.aircraft_ids), None)
            if conflict:
                return sim, conflict
        self.fail("truck on 22L should be a runway incursion")

    def test_runway_incursion_has_time_to_conflict_and_alert(self):
        sim, conflict = self._truck_on_runway()
        self.assertIsNotNone(conflict.seconds_to_conflict)
        self.assertGreater(conflict.seconds_to_conflict, 0)  # the arrival is still on final
        self.assertLess(conflict.seconds_to_conflict, CHALLENGE_TOUCHDOWN_S - 15)

        alert = next(a for a in sim.state.alerts if a.kind == "runway")
        self.assertEqual(alert.severity, "critical")
        self.assertIn("TRUCK01", alert.text)
        self.assertEqual(alert.atc_phrase, "TRUCK01, solution path available.")
        self.assertEqual(alert.seconds, conflict.seconds_to_conflict)

    def test_solution_path_takes_truck_off_the_runway(self):
        sim, conflict = self._truck_on_runway()
        vehicle, goal, via = sim.resolve_vehicle("TRUCK01", [i for i in conflict.aircraft_ids if i != "TRUCK01"])
        self.assertFalse(ground_kinds[goal].startswith("runway"))
        self.assertTrue(via, "the solution path should name the taxiway it uses")
        self.assertEqual(vehicle.route[-1], goal)
        run(sim, 5)  # it U-turns off the runway instead of driving on to the next node
        self.assertFalse([c for c in sim.state.conflicts if "TRUCK01" in c.aircraft_ids])
        run(sim, 15)
        self.assertEqual(sim.state.vehicles[0].position, goal)
        self.assertFalse([c for c in sim.state.conflicts if "TRUCK01" in c.aircraft_ids])

    def test_predicted_alert_escalates_with_fatigue(self):
        """A truck routed into a taxiing aircraft's path: medium when rested, high when tired."""
        sim = LiveSim(auto_spawn=False, seed=0)
        a = sim.add_waiting("AC1", "TST1", "A320", "large", sim.ramps[0], "rwy_22R")
        sim.route(a.id, a.destination)
        run(sim, 1)
        tr = sim.tracks[a.id]
        target = next(n for n, d in zip(tr.route, tr.cum) if tr.time_at(d) >= sim.now + 15)
        on_route = set(tr.route)
        start = next(m for n in airport_graph[target] if n not in on_route
                     for m in airport_graph[n] if m not in on_route)
        sim.add_vehicle("TRUCK01", [start])
        sim.route_vehicle("TRUCK01", target)

        fatigue.report(10)
        sim.tick(TICK)
        predicted = [x for x in sim.state.alerts if x.kind == "predicted"]
        self.assertEqual(len(predicted), 1)
        self.assertEqual(predicted[0].severity, "medium")
        self.assertIn("will meet TST1", predicted[0].text)
        self.assertEqual(predicted[0].atc_phrase, "TRUCK01, solution path available.")
        created = predicted[0].created_s

        fatigue.report(85)
        sim.tick(TICK)
        again = next(x for x in sim.state.alerts if x.kind == "predicted")
        self.assertEqual(again.severity, "high")
        self.assertEqual(again.fatigue, 85)
        self.assertEqual(again.id, predicted[0].id)
        self.assertEqual(again.created_s, created)  # same alert, not a new one


if __name__ == "__main__":
    unittest.main()
