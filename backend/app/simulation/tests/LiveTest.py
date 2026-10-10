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


if __name__ == "__main__":
    unittest.main()
