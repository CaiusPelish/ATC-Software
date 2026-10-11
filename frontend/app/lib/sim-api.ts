// Controller actions sent to the backend (proxied by Vite to FastAPI).
import type { VehicleKind } from "~/lib/chart";
import type { Aircraft, GroundVehicle, SimulationState } from "~/lib/sim-state";

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Can't reach the backend. Is it running on port 8000?");
  }
  if (res.ok) return (await res.json()) as T;

  // FastAPI errors look like {"detail": "..."}; fall back to the status line.
  const detail = await res
    .json()
    .then((b: { detail?: unknown }) => (typeof b.detail === "string" ? b.detail : null))
    .catch(() => null);
  throw new Error(detail ?? `The backend refused the request (HTTP ${res.status}).`);
}

/** Ask the planner to route a plane or truck to a node; resolves with its new state. */
export function sendRoute(kind: VehicleKind, id: string, destination: string) {
  const path =
    kind === "plane"
      ? `/api/aircraft/${encodeURIComponent(id)}/route`
      : `/api/vehicles/${encodeURIComponent(id)}/route`;
  return post<Aircraft | GroundVehicle>(path, { destination });
}

/** Replace the running simulation with a scenario ("default", "empty", "challenge_01"). */
export function loadScenario(id: string) {
  return post<SimulationState>("/api/scenario/load", { scenario_id: id });
}

/** Route an aircraft to the destination it already has (e.g. a parked arrival to its ramp). */
export function assignRoute(aircraftId: string) {
  return post<{ route: string[]; status: string }>(`/api/aircraft/${encodeURIComponent(aircraftId)}/assign-route`, {});
}

/** Tell a ground vehicle to stop where it is (hold short) or carry on. */
export function holdVehicle(id: string, hold: boolean) {
  return post<GroundVehicle>(`/api/vehicles/${encodeURIComponent(id)}/hold`, { hold });
}

/** Put a ground vehicle (e.g. a fire truck) on a node; it waits there until routed. */
export function spawnVehicle(id: string, position: string) {
  return post<GroundVehicle>("/api/vehicles", { id, position });
}
