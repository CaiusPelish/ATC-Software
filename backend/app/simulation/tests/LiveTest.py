import random
import unittest

from app.routing.graph import airport_graph
from app.simulation.live import ROLL_SPEED_MS, LiveSim
from app.simulation.scenarios import load_scenario

TICK = 0.25


def run(sim, seconds, every=None):
    for k in range(int(seconds / TICK)):
        sim.tick(TICK)
        if every:
            every(sim, k)


def separation(sim):
    return [c for c in sim.state.conflicts if "/" not in c.location]


class LiveSimTest(unittest.TestCase):
    def test_state_is_drawable(self):
        """Every id is a KEWR_ground node, routes follow its edges, progress only moves forward."""
        sim = LiveSim(seed=0)
        last = {}

        def check(sim, k):
            for a in sim.state.aircraft + sim.state.vehicles:
                self.assertIn(a.position, airport_graph)
                for u, v in zip(a.route, a.route[1:]):
                    self.assertIn(v, airport_graph[u], f"{a.id} route {u}->{v}")
                self.assertTrue(0 <= a.progress <= 1)
                key = (a.id, tuple(a.route))
                self.assertGreaterEqual(a.progress, last.get(key, 0) - 1e-9)
                last[key] = a.progress

        run(sim, 10 * 60, check)
        self.assertGreater(len(sim.callsigns), 5)

    def test_planned_traffic_has_no_conflicts(self):
        for seed in range(3):
            sim = LiveSim(seed=seed)
            seen = []
            run(sim, 15 * 60, lambda sim, k: seen.extend(sim.state.conflicts))
            self.assertEqual(seen, [], f"seed {seed}")

    def test_challenge_01(self):
        ignored = load_scenario("challenge_01")
        incursions = []
        run(ignored, 120, lambda sim, k: incursions.extend(
            c for c in sim.state.conflicts if c.location == "4R/22L" and c.severity == "critical"))
        self.assertTrue(incursions)

        handled = load_scenario("challenge_01")
        first = handled.state.aircraft[0]
        handled.route(first.id, first.destination)
        incursions = []
        run(handled, 200, lambda sim, k: incursions.extend(
            c for c in sim.state.conflicts if c.location == "4R/22L"))
        self.assertEqual(incursions, [])

    def test_holds_are_repaired(self):
        """Random 45 s holds: nobody runs into a held aircraft, and nobody is left stuck."""
        for seed in (1, 2, 4):
            sim = LiveSim(seed=seed)
            rng = random.Random(100 + seed)
            held = {}

            def control(sim, k):
                t = sim.now
                if k % 360 == 0 and t > 60:
                    moving = [a for a in sim.state.aircraft if a.status == "taxiing"
                              and t >= sim.rollout_until.get(a.id, -1)
                              and sim.tracks[a.id].speed(t) <= ROLL_SPEED_MS]
                    if moving:
                        a = rng.choice(moving)
                        sim.hold(a.id, True)
                        held[a.id] = t + 45
                for aid, until in list(held.items()):
                    if t >= until:
                        del held[aid]
                        try:
                            sim.hold(aid, False)
                        except (ValueError, KeyError):
                            pass  # retried on its own
                critical = [c for c in separation(sim) if c.severity == "critical"]
                self.assertEqual(critical, [], f"seed {seed} t={t}")

            run(sim, 25 * 60, control)
            # Release the last hold, then everything should get moving again.
            for aid in held:
                try:
                    sim.hold(aid, False)
                except (ValueError, KeyError):
                    pass
            run(sim, 90)
            stuck = [a.callsign for a in sim.state.aircraft if a.status == "holding"]
            self.assertEqual(stuck, [], f"seed {seed}")

    def test_hold_and_release(self):
        sim = LiveSim(seed=3)
        run(sim, 150)
        a = next(x for x in sim.state.aircraft if x.status == "taxiing"
                 and sim.now >= sim.rollout_until.get(x.id, -1))
        sim.hold(a.id, True)
        frozen = a.progress
        run(sim, 30)
        self.assertEqual((a.status, a.progress), ("holding", frozen))
        sim.hold(a.id, False)
        run(sim, 20)
        self.assertEqual(a.status, "taxiing")
        self.assertGreater(a.progress, frozen)

    def test_bad_requests(self):
        sim = LiveSim(seed=0)
        run(sim, 60)
        with self.assertRaises(KeyError):
            sim.route("nope", "rwy_22R")
        a = sim.state.aircraft[0]
        with self.assertRaises(ValueError):
            sim.route(a.id, "not-a-node")

    def test_default_has_traffic_at_start(self):
        sim = load_scenario("default")
        self.assertGreaterEqual(len(sim.state.aircraft), 1)
        run(sim, 5)
        self.assertTrue(any(a.status == "taxiing" for a in sim.state.aircraft))

    def test_truck_crosses_runway(self):
        """A truck driven across 22L/4R by the shortest path shows up as a runway incursion."""
        sim = load_scenario("challenge_01")
        truck = sim.vehicle("TRUCK01")
        start = truck.position
        far_side = "rwy_22L"
        sim.route_vehicle("TRUCK01", far_side)
        self.assertEqual(truck.route[0], start)
        self.assertEqual(truck.route[-1], far_side)
        for u, v in zip(truck.route, truck.route[1:]):
            self.assertIn(v, airport_graph[u])
        self.assertEqual(truck.status, "taxiing")

        run(sim, 2)
        sim.hold_vehicle("TRUCK01", True)
        frozen = truck.progress
        run(sim, 5)
        self.assertEqual((truck.status, truck.progress, truck.speed), ("holding", frozen, 0.0))
        sim.hold_vehicle("TRUCK01", False)
        incursions = []
        run(sim, sim.tracks["TRUCK01"].end_t - sim.now + 1, lambda sim, k: incursions.extend(
            c for c in sim.state.conflicts if "TRUCK01" in c.aircraft_ids and "/" in c.location))
        self.assertEqual((truck.status, truck.position, truck.progress), ("waiting", far_side, 1.0))
        self.assertTrue(incursions)

    def test_truck_reroute_mid_edge(self):
        """Rerouting while between nodes keeps the truck where it is on the map."""
        sim = LiveSim(auto_spawn=False, seed=0)
        sim.add_vehicle("TRUCK01", [sim.ramps[0]])
        sim.route_vehicle("TRUCK01", "rwy_4L")
        run(sim, 3.1)
        before = sim.tracks["TRUCK01"]
        d = before.dist(sim.now)
        node = before.route[before.index(d)]
        sim.route_vehicle("TRUCK01", sim.ramps[-1])
        after = sim.tracks["TRUCK01"]
        self.assertEqual(after.route[0], node)
        self.assertAlmostEqual(after.dist(sim.now), d - before.cum[before.index(d)], places=6)

    def test_truck_routed_into_aircraft_path_is_predicted(self):
        """Driving a truck onto where a taxiing aircraft is going warns before they get close."""
        sim = LiveSim(auto_spawn=False, seed=0)
        a = sim.add_waiting("AC1", "TST1", "A320", "large", sim.ramps[0], "rwy_22R")
        sim.route(a.id, a.destination)
        run(sim, 1)
        tr = sim.tracks[a.id]
        # A node the aircraft reaches 15 s or more from now, and a start for the truck two hops off its route.
        target = next(n for n, d in zip(tr.route, tr.cum) if tr.time_at(d) >= sim.now + 15)
        on_route = set(tr.route)
        start = next(m for n in airport_graph[target] if n not in on_route
                     for m in airport_graph[n] if m not in on_route)
        sim.add_vehicle("TRUCK01", [start])
        run(sim, 1)
        self.assertFalse([c for c in sim.state.conflicts + sim.state.warnings if "TRUCK01" in c.aircraft_ids])

        sim.route_vehicle("TRUCK01", target)
        sim.tick(TICK)
        predicted = [w for w in sim.state.warnings if w.aircraft_ids == ["TRUCK01", a.id]]
        self.assertEqual(len(predicted), 1)
        self.assertGreater(predicted[0].eta, 0)
        self.assertFalse([c for c in sim.state.conflicts if "TRUCK01" in c.aircraft_ids])

    def test_bad_vehicle_requests(self):
        sim = LiveSim(auto_spawn=False, seed=0)
        sim.add_vehicle("TRUCK01", [sim.ramps[0]])
        with self.assertRaises(ValueError):
            sim.add_vehicle("TRUCK01", [sim.ramps[1]])
        with self.assertRaises(ValueError):
            sim.add_vehicle("TRUCK02", ["not-a-node"])
        with self.assertRaises(ValueError):
            sim.route_vehicle("TRUCK01", "not-a-node")
        with self.assertRaises(KeyError):
            sim.hold_vehicle("nope", True)


if __name__ == "__main__":
    unittest.main()
