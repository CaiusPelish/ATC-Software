// Fake traffic on the real EWR taxiway graph, used when the backend is offline or its
// node ids aren't on the map yet. Emits the same SimulationState shape as GET /api/state,
// so the vehicle layer can't tell demo from live data.
import { chart, edgeLine, lineLength, routeLine, routePoint, type Point } from "~/lib/chart";
import type { SimulationState } from "~/lib/sim-state";

// Edge cost = length of the real curve, so demo routes match what's drawn.
const dist = (a: string, b: string) => lineLength(edgeLine(a, b));

/** Shortest path by taxiway curve length. Small graph, so a plain array queue is fine. */
function shortestPath(start: string, goal: string): string[] {
  const best = new Map([[start, 0]]);
  const prev = new Map<string, string>();
  const queue: [number, string][] = [[0, start]];
  while (queue.length) {
    queue.sort((a, b) => a[0] - b[0]);
    const [d, node] = queue.shift()!;
    if (node === goal) break;
    if (d > (best.get(node) ?? Infinity)) continue;
    for (const next of chart.graph[node] ?? []) {
      const nd = d + dist(node, next);
      if (nd < (best.get(next) ?? Infinity)) {
        best.set(next, nd);
        prev.set(next, node);
        queue.push([nd, next]);
      }
    }
  }
  const path = [goal];
  while (path[0] !== start) {
    const p = prev.get(path[0]);
    if (!p) return [];
    path.unshift(p);
  }
  return path;
}

const routeLength = (route: string[]) => lineLength(routeLine(route));

// Start/goal node ids picked on the real graph (see scripts/build_chart.py output).
const DEMO = [
  { id: "DEMO-1", callsign: "DAL123", from: "45460023", to: "8156977605", pxPerSec: 32 }, // Terminal C to runway 4L
  { id: "DEMO-2", callsign: "UAL789", from: "4698566093", to: "45452380", pxPerSec: 36 }, // north field down taxiway A
].map((d) => {
  const route = shortestPath(d.from, d.to);
  return { ...d, route, length: routeLength(route) };
});

// Fire truck crossing runway 4L/22R: the crash-challenge setup.
const TRUCK = (() => {
  const route = shortestPath("3961798055", "408973564");
  return { id: "FIRE-1", route, length: routeLength(route), pxPerSec: 20 };
})();

const RUNWAY_4L = chart.runways.find((r) => r.id === "04L/22R");

function distanceToSegment([px, py]: Point, [ax, ay]: Point, [bx, by]: Point) {
  const dx = bx - ax;
  const dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function pointAt(route: string[], progress: number): Point {
  const p = routePoint(route, progress);
  return p ? [p.x, p.y] : chart.positions[route[0]];
}

const wrap = (p: number) => (p >= 1 ? 0 : p);

export function createDemoState(): SimulationState {
  return {
    time: 0,
    running: true,
    aircraft: DEMO.map((d, i) => ({
      id: d.id,
      callsign: d.callsign,
      position: d.route[0],
      destination: d.to,
      route: d.route,
      speed: d.pxPerSec,
      progress: i * 0.3, // stagger so they don't start together
      status: "taxiing",
    })),
    vehicles: [{ id: TRUCK.id, position: TRUCK.route[0], route: TRUCK.route, speed: TRUCK.pxPerSec, progress: 0 }],
    conflicts: [],
    score: 1000,
  };
}

export function advanceDemoState(prev: SimulationState, dt: number): SimulationState {
  const aircraft = prev.aircraft.map((a) => {
    const demo = DEMO.find((d) => d.id === a.id)!;
    return { ...a, progress: wrap(a.progress + (demo.pxPerSec * dt) / demo.length) };
  });
  const vehicles = prev.vehicles.map((v) => ({
    ...v,
    progress: wrap(v.progress + (TRUCK.pxPerSec * dt) / TRUCK.length),
  }));

  // Truck on the runway = critical conflict with the plane taxiing alongside it.
  const truckOnRunway =
    RUNWAY_4L &&
    distanceToSegment(pointAt(TRUCK.route, vehicles[0].progress), RUNWAY_4L.from, RUNWAY_4L.to) <
      RUNWAY_4L.widthPx * 1.2;

  return {
    ...prev,
    time: prev.time + dt,
    aircraft,
    vehicles,
    conflicts: truckOnRunway
      ? [{ aircraft_ids: ["DEMO-2", TRUCK.id], location: "04L/22R", severity: "critical" }]
      : [],
  };
}
