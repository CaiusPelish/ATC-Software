import random
import time
import unittest

from app.routing.airport import get_airport
from app.routing.mapf import AircraftRequest, plan_batch, replan
from app.routing.reservations import check_paths

A_RH = "408963125"


def random_batch(ap, n, seed):
    rng = random.Random(seed)
    starts = rng.sample(ap.spawn_nodes(), n)
    goals = rng.sample(ap.hold_shorts(), n)
    return [
        AircraftRequest(f"N{i}", s, g, ready_t=rng.randint(0, 10),
                        weight_class=rng.choice(["small", "large", "heavy"]))
        for i, (s, g) in enumerate(zip(starts, goals))
    ]


class MapfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ap = get_airport()

    def test_eight_aircraft_conflict_free_and_fast(self):
        requests = random_batch(self.ap, 8, seed=1)
        t = time.perf_counter()
        result = plan_batch(self.ap, requests)
        elapsed = time.perf_counter() - t
        self.assertEqual(result.failures, {})
        self.assertEqual(len(result.paths), 8)
        self.assertEqual(check_paths(result.paths), [])
        self.assertLess(elapsed, 0.5)

    def test_many_seeds_never_conflict(self):
        for seed in range(10):
            result = plan_batch(self.ap, random_batch(self.ap, 12, seed))
            self.assertEqual(check_paths(result.paths), [], f"seed {seed}")
            for aid, reason in result.failures.items():
                self.assertTrue(reason, f"{aid} failed without a reason")

    def test_pile_up_at_junction_is_serialized(self):
        neighbours = sorted(self.ap.graph[A_RH])
        requests = [
            AircraftRequest(f"N{i}", start, neighbours[(i + 2) % 4], ready_t=0)
            for i, start in enumerate(neighbours)
        ]
        result = plan_batch(self.ap, requests)
        self.assertEqual(result.failures, {})
        self.assertEqual(check_paths(result.paths), [])
        at_rh = sorted(t for p in result.paths.values() for n, t in p if n == A_RH)
        self.assertTrue(all(b - a > 1 for a, b in zip(at_rh, at_rh[1:])), at_rh)
        self.assertGreater(result.total_delay_steps, 0)

    def test_heavy_first_on_tie(self):
        a = AircraftRequest("A", "408963114", "408967521", weight_class="small")
        b = AircraftRequest("B", "408967521", "408963114", weight_class="heavy")
        self.assertEqual(plan_batch(self.ap, [a, b]).order, ["B", "A"])

    def test_deterministic(self):
        first = plan_batch(self.ap, random_batch(self.ap, 10, seed=5))
        second = plan_batch(self.ap, random_batch(self.ap, 10, seed=5))
        self.assertEqual(first.paths, second.paths)

    def test_replan_to_new_goal_keeps_history(self):
        requests = random_batch(self.ap, 6, seed=2)
        result = plan_batch(self.ap, requests)
        r = requests[0]
        old = result.paths[r.id]
        t_now = old[len(old) // 2][1]
        new_goal = next(g for g in self.ap.hold_shorts()
                        if g not in {q.goal for q in requests})
        replanned = replan(self.ap, r, result.reservations, t_now, new_goal=new_goal)
        self.assertTrue(replanned.ok, replanned.reason)
        self.assertEqual(replanned.path[-1][0], new_goal)
        self.assertEqual([s for s in replanned.path if s[1] < t_now], [s for s in old if s[1] < t_now])
        self.assertEqual(check_paths(result.reservations.paths()), [])

    def test_departures_release_their_goal(self):
        goal = self.ap.hold_shorts()[0]
        starts = self.ap.spawn_nodes()[:3]
        requests = [AircraftRequest(f"D{i}", s, goal, goal_hold=12) for i, s in enumerate(starts)]
        result = plan_batch(self.ap, requests)
        self.assertEqual(result.failures, {})
        self.assertEqual(check_paths(result.paths, park_at_goal=False), [])
        arrivals = sorted(p[-1][1] - 12 for p in result.paths.values())
        self.assertTrue(all(b - a > 12 for a, b in zip(arrivals, arrivals[1:])), arrivals)
