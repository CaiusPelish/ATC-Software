import random
import unittest

from app.routing.airport import FOLLOW_GAP, NODE_BUFFER, get_airport
from app.routing.reservations import ReservationTable, check_paths

A_RJ, A_RH, A_RN = "408963114", "408963125", "408967521"


def random_path(ap, rng, t0):
    node, t = rng.choice(sorted(ap.nodes)), t0
    path = [(node, t)]
    for _ in range(rng.randint(1, 8)):
        if rng.random() < 0.3:
            t += 1
        else:
            nxt = rng.choice(sorted(ap.graph[node]))
            t += ap.steps[node][nxt]
            node = nxt
        path.append((node, t))
    return path


class ReservationTest(unittest.TestCase):
    def setUp(self):
        self.ap = get_airport()
        self.table = ReservationTable()

    def test_node_conflict_within_buffer(self):
        self.table.add_path("N1", [(A_RH, 9)], park_at_goal=False)
        clash = self.table.node_conflict(A_RH, 9 + NODE_BUFFER, ignore="N2")
        self.assertEqual((clash.kind, clash.other_id, clash.other_t), ("node_conflict", "N1", 9))
        self.assertIsNone(self.table.node_conflict(A_RH, 9 + NODE_BUFFER + 1, ignore="N2"))
        self.assertIsNone(self.table.node_conflict(A_RH, 9, ignore="N1"))

    def test_node_message(self):
        self.table.add_path("N1", [(A_RH, 9)], park_at_goal=False)
        msg = self.table.node_conflict(A_RH, 10, ignore="N2").message(self.ap)
        self.assertEqual(msg, "N2 would occupy A/RH at t=50s — N1 reserved it at t=45s.")

    def test_parked_aircraft_blocks_forever(self):
        self.table.add_path("N1", [(A_RJ, 0), (A_RH, 3)])
        clash = self.table.node_conflict(A_RH, 1000, ignore="N2")
        self.assertTrue(clash.parked)
        self.assertIn("N1 is holding there", clash.message(self.ap))
        self.assertEqual(self.table.last_reserved(A_RH, ignore="N2"), float("inf"))
        self.assertEqual(self.table.last_reserved(A_RH, ignore="N1"), -1)

    def test_head_on_swap(self):
        d = self.ap.steps[A_RJ][A_RH]
        self.table.add_path("N1", [(A_RJ, 0), (A_RH, d)], park_at_goal=False)
        clash = self.table.edge_conflict(A_RH, A_RJ, 1, 1 + d, ignore="N2")
        self.assertEqual((clash.kind, clash.other_id), ("edge_swap", "N1"))
        msg = clash.message(self.ap)
        self.assertIn("Head-on conflict on A/RH→A/RJ", msg)
        self.assertIn("N1", msg)
        self.assertIn("N2", msg)
        self.assertIsNone(self.table.edge_conflict(A_RH, A_RJ, d, 2 * d, ignore="N2"))

    def test_follow_gap(self):
        d = self.ap.steps[A_RJ][A_RH]
        self.table.add_path("N1", [(A_RJ, 0), (A_RH, d)], park_at_goal=False)
        clash = self.table.edge_conflict(A_RJ, A_RH, FOLLOW_GAP - 1, FOLLOW_GAP - 1 + d, ignore="N2")
        self.assertEqual(clash.kind, "follow_gap")
        self.assertIn("minimum following gap", clash.message(self.ap))
        self.assertIsNone(self.table.edge_conflict(A_RJ, A_RH, FOLLOW_GAP, FOLLOW_GAP + d, ignore="N2"))

    def test_check_paths_agrees(self):
        d = self.ap.steps[A_RJ][A_RH]
        paths = {"N1": [(A_RJ, 0), (A_RH, d)], "N2": [(A_RH, 0), (A_RJ, d)]}
        kinds = {c.kind for c in check_paths(paths, park_at_goal=False)}
        self.assertIn("edge_swap", kinds)

    def test_random_add_remove_keeps_table_consistent(self):
        rng = random.Random(7)
        model = {}
        for i in range(1000):
            if model and rng.random() < 0.4:
                aid = rng.choice(sorted(model))
                self.table.remove_path(aid)
                del model[aid]
            else:
                aid = f"N{i}"
                path = random_path(self.ap, rng, rng.randint(0, 50))
                self.table.add_path(aid, path, park_at_goal=False)
                model[aid] = path
            self.assertEqual(self.table.paths(), model)

        for aid, path in model.items():
            for node, t in path:
                expected = any(
                    other != aid and on == node and abs(ot - t) <= NODE_BUFFER
                    for other, p in model.items() for on, ot in p
                )
                self.assertEqual(self.table.node_conflict(node, t, ignore=aid) is not None, expected)

        for aid in list(model):
            self.table.remove_path(aid)
        self.assertFalse(any(self.table._nodes.values()))
        self.assertFalse(any(self.table._edges.values()))
