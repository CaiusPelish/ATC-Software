import { useEffect, useState } from "react";

import { hasNode } from "~/lib/chart";
import { advanceDemoState, createDemoState } from "~/lib/demo-traffic";
import type { SimulationState } from "~/lib/sim-state";

export type SimSource =
  | { mode: "connecting" }
  | { mode: "live" }
  | { mode: "demo"; reason: "offline" | "unmapped" };

const POLL_MS = 500;
const OFFLINE_RETRY_MS = 5000; // back off while the backend is down so the dev log isn't flooded

/** Every vehicle sits on a node the chart knows. Empty state counts as mapped. */
function isMapped(state: SimulationState) {
  return [...state.aircraft, ...state.vehicles].every((v) => hasNode(v.position));
}

/**
 * Polls GET /api/state (proxied to the backend by Vite). Falls back to demo traffic
 * when the backend is unreachable or sends node ids that aren't on the map.
 */
export function useSimState() {
  const [state, setState] = useState<SimulationState | null>(null);
  const [source, setSource] = useState<SimSource>({ mode: "connecting" });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let demo = createDemoState();
    let last = performance.now();
    let lastFetch = 0;
    let offline = false;

    async function fetchState(): Promise<SimulationState | "offline"> {
      try {
        const res = await fetch("/api/state", { signal: AbortSignal.timeout(2000) });
        if (!res.ok) return "offline";
        return (await res.json()) as SimulationState;
      } catch {
        return "offline";
      }
    }

    async function tick() {
      const now = performance.now();
      const dt = (now - last) / 1000;
      last = now;

      if (!document.hidden) {
        let live: SimulationState | "offline" | null = null;
        if (!offline || now - lastFetch > OFFLINE_RETRY_MS) {
          lastFetch = now;
          live = await fetchState();
          offline = live === "offline";
        }
        if (cancelled) return;

        if (live && live !== "offline" && isMapped(live)) {
          setState(live);
          setSource({ mode: "live" });
        } else {
          demo = advanceDemoState(demo, dt);
          setState(demo);
          setSource({ mode: "demo", reason: offline ? "offline" : "unmapped" });
        }
      }
      timer = setTimeout(tick, POLL_MS);
    }

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  return { state, source };
}
