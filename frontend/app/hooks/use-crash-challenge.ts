import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { chart, placeName } from "~/lib/chart";
import { assignRoute, holdVehicle, loadScenario } from "~/lib/sim-api";
import type { SimulationState } from "~/lib/sim-state";

// Timings of the backend's challenge_01 scenario (backend/app/simulation/scenarios.py):
// the arrival touches down on 22L at sim time 120 s, and the runway is closed from
// LOCK_NM = 2 nm out at 140 kt (arrivals.py): 2 * 1852 / (140 * 0.5144) ≈ 51.4 s before.
const SCENARIO = "challenge_01";
export const RUNWAY = "22L";
const TOUCHDOWN_S = 120;
const LOCK_S = 51.4;
const CLEAR_AFTER_S = TOUCHDOWN_S + 20;
// The arrival is on final at 140 kt (arrivals.py APPROACH_KT); the chart is Web Mercator
// zoom 16, where one pixel is about 1.81 m at Newark's latitude.
const V_APPROACH_MS = 140 * 0.5144;
const M_PER_PX = 1.81;

// Runway 22L's two ends on the chart: the arrival crosses the 22L end and rolls toward 04R.
const RUNWAY_22L = (() => {
  const r = chart.runways.find((rw) => rw.id.includes("22L"));
  const land = r?.ends.find((e) => e.label === "22L");
  const far = r?.ends.find((e) => e.label !== "22L");
  if (!land || !far) return null;
  const [dx, dy] = [land.at[0] - far.at[0], land.at[1] - far.at[1]];
  const len = Math.hypot(dx, dy);
  // Unit vector pointing out along the final approach (away from the runway).
  return { threshold: land.at, out: [dx / len, dy / len] as const };
})();

// How far up the approach line the inbound plane can be drawn and still be visible: the
// default view crops ~120 chart px off the top and the header covers ~180 more.
const APPROACH_TOP_Y = 330;
const APPROACH_SPAN_PX = RUNWAY_22L
  ? RUNWAY_22L.out[1] < 0
    ? Math.max(80, (RUNWAY_22L.threshold[1] - APPROACH_TOP_Y) / -RUNWAY_22L.out[1])
    : 400
  : 0;

export type Inbound = { x: number; y: number; heading: number; label: string; closed: boolean; threshold: [number, number] };

export type ChallengePhase = "idle" | "intro" | "starting" | "running" | "caught" | "clear";

export type Caught = {
  /** "runway": a vehicle on the closed runway. "separation": a vehicle within 20 m of a plane. */
  kind: "runway" | "separation";
  intruder: string;
  /** The other party: the landing arrival, or the plane the vehicle nearly hit. */
  other: string;
  /** Where it happened, e.g. "taxiway B" (separation only). */
  where: string;
  /** How long before touchdown the incursion was flagged. */
  secondsBefore: number;
};

/**
 * The judge demo: load challenge_01, clear the parked arrival off the runway, then watch
 * for a runway incursion caused by a ground vehicle before the next arrival lands.
 */
export function useCrashChallenge(state: SimulationState | null) {
  const [phase, setPhase] = useState<ChallengePhase>("idle");
  const [caught, setCaught] = useState<Caught | null>(null);
  const [busy, setBusy] = useState(false);

  const time = state?.time ?? 0;
  const touchdownIn = Math.max(0, TOUCHDOWN_S - time);
  const closesIn = Math.max(0, TOUCHDOWN_S - LOCK_S - time);
  const runwayClosed = closesIn === 0;

  const begin = useCallback(async () => {
    setBusy(true);
    setCaught(null);
    try {
      const fresh = await loadScenario(SCENARIO);
      // The first arrival is parked on the runway; send it to its ramp so only the
      // judge's truck can cause an incursion.
      await Promise.all(
        fresh.aircraft.filter((a) => a.status === "waiting").map((a) => assignRoute(a.id).catch(() => null)),
      );
      setPhase("starting");
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : "Couldn't start the challenge.");
    } finally {
      setBusy(false);
    }
  }, []);

  const exit = useCallback(async () => {
    setPhase("idle");
    setCaught(null);
    try {
      await loadScenario("default");
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : "Couldn't restore normal traffic.");
    }
  }, []);

  const holdShort = useCallback(async () => {
    if (!caught) return;
    try {
      await holdVehicle(caught.intruder, true);
      toast.success(`${caught.intruder}, hold short of runway ${RUNWAY}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Hold instruction failed.");
    }
  }, [caught]);

  useEffect(() => {
    if (!state) return;
    // Wait for the first poll of the freshly loaded scenario (its clock restarts at 0).
    if (phase === "starting" && state.time < 10 && state.vehicles.length > 0) setPhase("running");
    if (phase !== "running") return;

    // Caught = a vehicle in a critical conflict: on the closed runway, or nose to nose with a plane.
    const vehicleIds = new Set(state.vehicles.map((v) => v.id));
    const hit = state.conflicts.find(
      (c) => c.severity === "critical" && c.aircraft_ids.some((id) => vehicleIds.has(id)),
    );
    if (hit) {
      const intruder = hit.aircraft_ids.find((id) => vehicleIds.has(id))!;
      const otherId = hit.aircraft_ids.find((id) => id !== intruder);
      const runway = hit.location.includes("/");
      setCaught({
        kind: runway ? "runway" : "separation",
        intruder,
        other: state.aircraft.find((a) => a.id === otherId)?.callsign ?? (runway ? "the arrival" : otherId ?? "traffic"),
        where: runway ? `runway ${RUNWAY}` : placeName(hit.location),
        // The backend's own time-to-conflict when it sends one, else the scenario's touchdown time.
        secondsBefore: Math.max(0, Math.round(hit.seconds_to_conflict ?? TOUCHDOWN_S - state.time)),
      });
      setPhase("caught");
    } else if (state.time > CLEAR_AFTER_S) {
      setPhase("clear");
    }
  }, [state, phase]);

  // The landing arrival isn't in the backend state until touchdown, so draw it on final
  // ourselves, on the extended centreline. To scale it would sit above the top of the map
  // until the last ~12 s (22L is close to the chart edge), so the approach is compressed to
  // fit: it starts at the top of the map and reaches the runway exactly at touchdown.
  // The countdown stays real; only the distance isn't to scale.
  const inbound: Inbound | null =
    RUNWAY_22L && (phase === "running" || phase === "caught") && touchdownIn > 0
      ? (() => {
          const out = Math.min((touchdownIn * V_APPROACH_MS) / M_PER_PX, APPROACH_SPAN_PX * (touchdownIn / TOUCHDOWN_S));
          const [tx, ty] = RUNWAY_22L.threshold;
          const [ux, uy] = RUNWAY_22L.out;
          return {
            x: tx + ux * out,
            y: ty + uy * out,
            // Flying back toward the runway (-out), as a compass heading clockwise from up.
            heading: (Math.atan2(-ux, uy) * 180) / Math.PI,
            label: `ARRIVAL · ${RUNWAY} · ${Math.floor(touchdownIn / 60)}:${String(Math.floor(touchdownIn % 60)).padStart(2, "0")}`,
            closed: runwayClosed,
            threshold: [tx, ty],
          };
        })()
      : null;

  return {
    phase,
    caught,
    inbound,
    busy,
    touchdownIn,
    closesIn,
    runwayClosed,
    open: () => setPhase("intro"),
    start: begin,
    retry: begin,
    exit,
    cancel: () => setPhase("idle"),
    holdShort,
  };
}
