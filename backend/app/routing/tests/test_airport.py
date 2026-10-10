import unittest

from app.routing.airport import DT, TAXI_SPEED, get_airport
from app.routing.osm_taxiways import haversine_m


class AirportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ap = get_airport()

    def test_loads_simplified_kewr(self):
        self.assertEqual(len(self.ap.nodes), 413)
        self.assertIs(get_airport(), self.ap)

    def test_every_edge_takes_at_least_one_step(self):
        for u, nbrs in self.ap.steps.items():
            for v, steps in nbrs.items():
                self.assertGreaterEqual(steps, 1)
                self.assertGreaterEqual(steps * TAXI_SPEED * DT, self.ap.graph[u][v])

    def test_projection_matches_haversine(self):
        ids = sorted(self.ap.nodes)
        for a, b in zip(ids[::7], ids[3::7]):
            na, nb = self.ap.nodes[a], self.ap.nodes[b]
            real = haversine_m(na.lat, na.lon, nb.lat, nb.lon)
            self.assertAlmostEqual(self.ap.distance_m(a, b), real, delta=real * 0.01 + 0.5)

    def test_heuristic_never_overestimates_an_edge(self):
        for u, nbrs in self.ap.steps.items():
            for v, steps in nbrs.items():
                self.assertLessEqual(0.999 * self.ap.heuristic_steps(u, v), steps)

    def test_node_classes(self):
        self.assertEqual(len(self.ap.hold_shorts()), 73)
        self.assertTrue(self.ap.spawn_nodes())
        self.assertEqual(self.ap.node_class(self.ap.hold_shorts()[0]), "runway_hold")
        self.assertEqual(self.ap.node_class("408963125"), "junction")

    def test_labels_unique_and_readable(self):
        labels = [self.ap.label(n) for n in self.ap.nodes]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(self.ap.label("408963125"), "A/RH")
