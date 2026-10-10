# backend/app/routing/astar.py

import heapq


def astar(graph, start, goal):
    if start not in graph or goal not in graph:
        raise ValueError("Unknown start or destination")

    queue = [(0, start)]
    costs = {start: 0}
    previous = {}

    while queue:
        current_cost, current = heapq.heappop(queue)

        if current_cost > costs[current]:
            continue

        if current == goal:
            path = [current]

            while current in previous:
                current = previous[current]
                path.append(current)

            path.reverse()

            return {
                "route": path,
                "estimated_time": current_cost
            }

        for neighbor, weight in graph[current].items():
            new_cost = current_cost + weight

            if new_cost < costs.get(neighbor, float("inf")):
                costs[neighbor] = new_cost
                previous[neighbor] = current

                heapq.heappush(
                    queue,
                    (new_cost, neighbor)
                )

    return None