# backend/app/routing/graph.py

"""The graph the API serves: KEWR_ground.json (taxiways plus runway nodes).

Same {node: {neighbor: meters}} shape as before, so /api/graph, /api/route
and the spawner use it as is. The frontend map is built from the same file,
so every node id here is drawable there.
"""

from .ground_graph import load_ground_graph

ground_nodes, airport_graph, ground_geometry, ground_kinds, runway_edges = load_ground_graph()
