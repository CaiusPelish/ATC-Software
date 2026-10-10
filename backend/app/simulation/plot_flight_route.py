# backend/app/simulation/plot_flight_route.py

"""Draw the taxi route a real flight took, node to node, over the KEWR taxiway graph.

Uses the simplified graph (intersections, dead ends and hold-short points),
with each edge drawn along its real curve. Each GPS point while taxiing is
snapped to the nearest taxiway segment. Consecutive snapped nodes that aren't
neighbours are joined with A*, and any back-and-forth from GPS jitter is cut
out, so the result is one clean path through the graph.

Run:  python3 plot_flight_route.py [flight_id]
"""

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.routing.astar import astar  # noqa: E402
from app.routing.osm_taxiways import (  # noqa: E402
    edge_points,
    edge_taxiway,
    load_saved_graph,
    snap_to_nearest_edge,
    taxiway_sequence,
)
from app.simulation.traffic import (  # noqa: E402,F401  (re-exported for older callers)
    SNAP_DISTANCE_M,
    STOPPED_SPEED,
    TAKEOFF_ROLL_SPEED,
    TRAFFIC_CSV,
    taxi_points,
)


def get_taxi_points(flight_id, csv_path=TRAFFIC_CSV):
    """GPS points from the flight's first appearance until it lines up for takeoff, as an (n, 2) [lat, lon] array.

    By the time speed passes TAKEOFF_ROLL_SPEED the plane is already well down
    the runway, so the taxi ends at the last stop before that.
    """
    df = pd.read_csv(csv_path)
    flight = df[df["ID"] == flight_id]
    if flight.empty:
        raise ValueError(f"No rows for flight {flight_id} in {csv_path}")
    return taxi_points(flight)


def actual_node_route(nodes, graph, points, geometry=None):
    """Turn a GPS track into the list of graph node ids the plane passed through."""
    route = []
    for lat, lon in points:
        # Snap to the nearest segment, then take its nearer end. Snapping
        # straight to nodes would favour curves packed with nodes over long
        # straight segments the plane is actually on.
        hit = snap_to_nearest_edge(nodes, graph, lat, lon, SNAP_DISTANCE_M, geometry)
        if hit is None:
            continue
        a, b, _, t = hit
        node = a.id if t < 0.5 else b.id
        if route and route[-1] == node:
            continue
        if node in route:
            # Came back to a node already on the route: GPS jitter, not a real loop.
            del route[route.index(node) + 1:]
            continue
        if route and node not in graph[route[-1]]:
            gap = astar(graph, route[-1], node)
            if gap:
                route.extend(gap["route"][1:-1])
        route.append(node)
    return route


def route_polyline(nodes, geometry, route):
    """The route as one [lat, lon] polyline, following each edge's curve."""
    line = [[nodes[route[0]].lat, nodes[route[0]].lon]]
    for a, b in zip(route, route[1:]):
        line.extend(edge_points(nodes, geometry, a, b)[1:])
    return line


def draw_taxiway_background(ax, nodes, graph, geometry=None):
    """Whole taxiway network, faded so whatever is drawn on top stands out."""
    drawn = set()
    for a, neighbors in graph.items():
        for b in neighbors:
            if (b, a) in drawn:
                continue
            drawn.add((a, b))
            pts = edge_points(nodes, geometry, a, b)
            ax.plot([p[1] for p in pts], [p[0] for p in pts],
                    color="#c9ccd1", linewidth=1, zorder=1)
    ax.scatter([n.lon for n in nodes.values()], [n.lat for n in nodes.values()],
               s=10, color="#b3b8bf", zorder=2, label="taxiway node")


def plot_flight_route(nodes, graph, route, points, flight_id, out, runway="4L", geometry=None,
                      route_label="Taxi route"):
    """Two panels with the same zoom: the node-to-node route, and the raw GPS it came from."""
    fig, (ax, gps_ax) = plt.subplots(1, 2, figsize=(14, 12), sharex=True, sharey=True,
                                     layout="constrained")
    draw_taxiway_background(ax, nodes, graph, geometry)
    draw_taxiway_background(gps_ax, nodes, graph, geometry)

    # The route itself, following each edge's curve, with a dot at each node.
    line = route_polyline(nodes, geometry, route)
    ax.plot([p[1] for p in line], [p[0] for p in line], color="#d9622b", linewidth=3.5,
            solid_capstyle="round", zorder=4, label=route_label)
    ax.scatter([nodes[n].lon for n in route], [nodes[n].lat for n in route], s=40,
               color="#d9622b", edgecolor="white", linewidth=1, zorder=5)

    start, end = nodes[route[0]], nodes[route[-1]]
    ax.scatter(start.lon, start.lat, s=180, color="#2e9e5b", edgecolor="white",
               linewidth=2, zorder=6, label="start")
    ax.scatter(end.lon, end.lat, s=200, marker="s", color="#b8322a", edgecolor="white",
               linewidth=2, zorder=6, label=f"line up, runway {runway}")

    # Label each taxiway once, at the middle of its middle edge on the route.
    sequence = taxiway_sequence(nodes, route)
    edges = list(zip(route, route[1:]))
    for name in sequence:
        on_it = [(a, b) for a, b in edges if edge_taxiway(nodes, a, b) == name]
        pts = edge_points(nodes, geometry, *on_it[len(on_it) // 2])
        lat, lon = pts[len(pts) // 2] if len(pts) > 2 else [(pts[0][i] + pts[-1][i]) / 2 for i in (0, 1)]
        ax.annotate(name, (lon, lat), xytext=(10, 0), textcoords="offset points",
                    fontsize=12, weight="bold", va="center",
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#d9622b"),
                    zorder=7)

    # Zoom to the route with some margin around it.
    all_lats = [p[0] for p in line] + list(points[:, 0])
    all_lons = [p[1] for p in line] + list(points[:, 1])
    pad = 0.0015
    ax.set_xlim(min(all_lons) - pad, max(all_lons) + pad)
    ax.set_ylim(min(all_lats) - pad, max(all_lats) + pad)
    ax.set_aspect(1 / math.cos(math.radians(sum(all_lats) / len(all_lats))))

    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(f"{route_label}: {' → '.join(sequence)} → RWY {runway}",
                 fontsize=14, weight="bold")
    ax.legend(loc="upper left")

    # Raw GPS, points[0] -> points[-1], one point per second, coloured by time.
    minutes = [i / 60 for i in range(len(points))]
    gps_ax.plot(points[:, 1], points[:, 0], color="#3b6fb6", linewidth=1, alpha=0.6,
                zorder=3, label="GPS track")
    dots = gps_ax.scatter(points[:, 1], points[:, 0], c=minutes, cmap="viridis", s=10,
                          zorder=4)
    gps_ax.scatter(points[0, 1], points[0, 0], s=180, color="#2e9e5b", edgecolor="white",
                   linewidth=2, zorder=6, label="points[0]")
    gps_ax.scatter(points[-1, 1], points[-1, 0], s=200, marker="s", color="#b8322a",
                   edgecolor="white", linewidth=2, zorder=6, label="points[-1]")
    fig.colorbar(dots, ax=gps_ax, shrink=0.6, label="minutes since points[0]")

    gps_ax.set_xlabel("longitude")
    gps_ax.set_title(f"Raw GPS: {len(points)} points, {len(points) / 60:.0f} min",
                     fontsize=14, weight="bold")
    gps_ax.legend(loc="upper left")

    fig.suptitle(f"Flight {flight_id}", fontsize=16, weight="bold")
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return out


if __name__ == "__main__":
    flight_id = int(sys.argv[1]) if len(sys.argv) > 1 else 6509825

    nodes, graph, geometry = load_saved_graph("KEWR")
    points = get_taxi_points(flight_id)
    route = actual_node_route(nodes, graph, points, geometry)

    out = DATA_DIR.parent / f"flight_{flight_id}_route.png"
    plot_flight_route(nodes, graph, route, points, flight_id, out, geometry=geometry)
    print(f"Flight {flight_id}: {len(points)} GPS points → {len(route)} route nodes, "
          f"via {taxiway_sequence(nodes, route)}")
    print(f"Saved {out}")
