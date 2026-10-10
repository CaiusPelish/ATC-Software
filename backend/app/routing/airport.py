# backend/app/routing/airport.py

"""KEWR taxiway graph in the form the MAPF planner needs.

Wraps the simplified OSM graph from osm_taxiways with a flat x/y projection in
meters, edge traversal times in whole timesteps, human-readable node labels
for conflict messages, and a classification of every node (hold-short,
junction, dead end).
"""

import functools
import math
from collections import Counter

from .osm_taxiways import load_saved_graph

# Length of one simulation timestep, in seconds.
DT = 5
# Nominal taxi speed in m/s; 40 m per timestep.
TAXI_SPEED = 8.0
# A node reserved at t is also blocked at t - NODE_BUFFER .. t + NODE_BUFFER.
NODE_BUFFER = 1
# Minimum steps between two aircraft entering the same edge in the same direction.
FOLLOW_GAP = 2

M_PER_DEG_LAT = 111_320


class Airport:
    def __init__(self, icao="KEWR", taxi_speed=TAXI_SPEED):
        self.icao = icao
        self.taxi_speed = taxi_speed
        self.nodes, self.graph, self.geometry = load_saved_graph(icao, simple=True)

        lat0 = sum(n.lat for n in self.nodes.values()) / len(self.nodes)
        lon0 = sum(n.lon for n in self.nodes.values()) / len(self.nodes)
        m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
        self.origin = (lat0, lon0)
        self.xy = {
            k: ((n.lon - lon0) * m_per_deg_lon, (n.lat - lat0) * M_PER_DEG_LAT)
            for k, n in self.nodes.items()
        }

        step_m = taxi_speed * DT
        self.steps = {
            u: {v: max(1, math.ceil(length / step_m)) for v, length in nbrs.items()}
            for u, nbrs in self.graph.items()
        }

        names = Counter(n.name for n in self.nodes.values())
        self._labels = {
            k: n.name if names[n.name] == 1 else f"{n.name} ({k})"
            for k, n in self.nodes.items()
        }
        self.classes = {k: self._classify(k) for k in self.nodes}

    def _classify(self, node):
        hold = self.nodes[node].tags.get("holding_position:type")
        if hold == "runway":
            return "runway_hold"
        if hold == "ILS":
            return "ils_hold"
        if len(self.graph[node]) == 1:
            return "dead_end"
        if self.nodes[node].is_junction:
            return "junction"
        return "plain"

    def distance_m(self, a, b):
        (xa, ya), (xb, yb) = self.xy[a], self.xy[b]
        return math.hypot(xb - xa, yb - ya)

    def heuristic_steps(self, node, goal):
        """Straight-line lower bound on the steps from node to goal."""
        return self.distance_m(node, goal) / (self.taxi_speed * DT)

    def label(self, node):
        """Name for messages, e.g. 'A/RH'; ambiguous names get the node id appended."""
        return self._labels[node]

    def node_class(self, node):
        return self.classes[node]

    def hold_shorts(self, kind="runway"):
        return sorted(
            k for k, n in self.nodes.items()
            if n.tags.get("holding_position:type") == kind
        )

    def spawn_nodes(self):
        """Dead ends: where aircraft can appear without blocking a through route."""
        return sorted(k for k, c in self.classes.items() if c == "dead_end")


@functools.lru_cache
def get_airport(icao="KEWR", taxi_speed=TAXI_SPEED):
    return Airport(icao, taxi_speed)
