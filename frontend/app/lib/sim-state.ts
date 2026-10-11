// Mirrors backend/app/models.py (GET /api/state). Keep in sync with the backend.

export type AircraftStatus = "waiting" | "taxiing" | "holding" | "arrived";

export type Aircraft = {
  id: string;
  callsign: string;
  /** Node id the aircraft is at (or last passed). */
  position: string;
  destination: string;
  /** Node ids, start to finish. */
  route: string[];
  speed: number;
  /** Fraction of the whole route covered, 0 to 1. */
  progress: number;
  status: AircraftStatus;
};

export type GroundVehicle = {
  id: string;
  position: string;
  route: string[];
  speed: number;
  progress: number;
  status?: "waiting" | "taxiing" | "holding";
};

export type Conflict = {
  aircraft_ids: string[];
  location: string;
  severity: "warning" | "critical";
};

/** A collision the backend predicts: a vehicle's route meets someone within LOOKAHEAD (30 s). */
export type CollisionWarning = {
  /** [the vehicle, the aircraft or vehicle it will meet] */
  aircraft_ids: string[];
  /** Node where they'll meet. */
  location: string;
  /** Seconds until they're within 40 m. */
  eta: number;
};

export type SimulationState = {
  time: number;
  running: boolean;
  aircraft: Aircraft[];
  vehicles: GroundVehicle[];
  conflicts: Conflict[];
  /** Predicted collisions (newer backends only). */
  warnings?: CollisionWarning[];
  score: number;
};

/** Flattened view of a plane or ground vehicle for drawing and hover cards. */
export type VehicleInfo = {
  id: string;
  label: string;
  kind: "plane" | "truck";
  status: AircraftStatus | null;
  destination: string | null;
  route: string[];
  position: string;
  progress: number;
  inConflict: boolean;
};

export function vehiclesOf(state: SimulationState): VehicleInfo[] {
  const conflicted = new Set(state.conflicts.flatMap((c) => c.aircraft_ids));
  return [
    ...state.aircraft.map((a) => ({
      id: a.id,
      label: a.callsign,
      kind: "plane" as const,
      status: a.status,
      destination: a.destination,
      route: a.route,
      position: a.position,
      progress: a.progress,
      inConflict: conflicted.has(a.id),
    })),
    ...state.vehicles.map((v) => ({
      id: v.id,
      label: v.id,
      kind: "truck" as const,
      status: null,
      destination: null,
      route: v.route,
      position: v.position,
      progress: v.progress,
      inConflict: conflicted.has(v.id),
    })),
  ];
}
