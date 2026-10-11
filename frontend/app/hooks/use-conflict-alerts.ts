import { useCallback, useEffect, useRef, useState } from "react";

import { describeConflict, describeWarning, type ConflictAlert } from "~/lib/alert-speech";
import type { Alert, SimulationState } from "~/lib/sim-state";

const LINGER_MS = 10_000; // an alert stays up this long after its conflict ends
const FLASH_TAIL_MS = 1_500; // the red flash keeps going briefly after a conflict clears
const REANNOUNCE_MS = 30_000; // the same conflict isn't spoken again within this window
const ESCALATED_FATIGUE = 70; // backend fatigue score at which alerts get urgent
const LOUD_GAIN = 1.8;

export type ShownAlert = ConflictAlert & {
  firstAt: number;
  lastAt: number;
  active: boolean;
  /** Set when the backend raised this alert because the controller is tired. */
  escalated?: { severity: Alert["severity"]; fatigue: number };
};

/** The backend's alert for a pair is escalated when fatigue pushed it to high, or it's critical on a tired controller. */
function escalation(state: SimulationState, pair: string): ShownAlert["escalated"] {
  const a = state.alerts?.find((x) => [...x.vehicle_ids].sort().join("+") === pair);
  if (!a || a.fatigue < ESCALATED_FATIGUE || (a.severity !== "high" && a.severity !== "critical")) return undefined;
  return { severity: a.severity, fatigue: Math.round(a.fatigue) };
}

/**
 * Conflicts (happening now) and predicted collisions (the backend's 30 s lookahead) become
 * sticky on-screen alerts and, on live traffic, one spoken ElevenLabs announcement per pair.
 * Only real conflicts flash the screen red; a prediction is the early heads-up.
 */
export function useConflictAlerts(state: SimulationState | null, live: boolean) {
  const [alerts, setAlerts] = useState<ShownAlert[]>([]);
  const [muted, setMuted] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const dismissed = useRef(new Set<string>());
  const announced = useRef(new Map<string, number>());
  const audio = useRef<AudioContext | null>(null);
  const queue = useRef<{ text: string; gain: number }[]>([]);
  const speaking = useRef(false);

  // Browsers only allow sound after a user gesture: unlock audio on the first click.
  useEffect(() => {
    const unlock = () => {
      audio.current ??= new AudioContext();
      void audio.current.resume();
    };
    window.addEventListener("pointerdown", unlock);
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  const playNext = useCallback(async () => {
    if (speaking.current) return;
    const next = queue.current.shift();
    if (!next) return;
    const { text, gain } = next;
    speaking.current = true;
    try {
      const res = await fetch("/alerts/voice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { error?: string } | null;
        throw new Error(body?.error ?? `Voice alert failed (HTTP ${res.status}).`);
      }
      audio.current ??= new AudioContext();
      const ctx = audio.current;
      await ctx.resume();
      const buffer = await ctx.decodeAudioData(await res.arrayBuffer());
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      // Louder when the controller is tired (escalated alerts).
      const volume = ctx.createGain();
      volume.gain.value = gain;
      source.connect(volume).connect(ctx.destination);
      await new Promise<void>((resolve) => {
        source.onended = () => resolve();
        source.start();
      });
      setVoiceError(null);
    } catch (err) {
      setVoiceError(err instanceof Error ? err.message : "Voice alert failed.");
    } finally {
      speaking.current = false;
      void playNext();
    }
  }, []);

  useEffect(() => {
    if (!state) return;
    const now = performance.now();
    const real = state.conflicts.map((c) => describeConflict(c, state));
    const realPairs = new Set(real.map((c) => c.pair));
    // Once a predicted pair is actually close, its real conflict card takes over.
    const predicted = (state.warnings ?? [])
      .map((w) => describeWarning(w, state))
      .filter((w) => !realPairs.has(w.pair));
    const current = [...real, ...predicted];
    const currentKeys = new Set(current.map((c) => c.key));

    setAlerts((prev) => {
      const byKey = new Map(prev.map((a) => [a.key, a]));
      for (const c of current) {
        if (dismissed.current.has(c.key)) continue;
        const old = byKey.get(c.key);
        byKey.set(c.key, {
          ...c,
          escalated: escalation(state, c.pair),
          // Once critical, stay critical while the alert is up.
          severity: old?.severity === "critical" ? "critical" : c.severity,
          firstAt: old?.firstAt ?? now,
          lastAt: now,
          active: true,
        });
      }
      return [...byKey.values()]
        .filter((a) => !(a.kind === "predicted" && realPairs.has(a.pair)))
        .map((a) => (currentKeys.has(a.key) ? a : { ...a, active: now - a.lastAt < FLASH_TAIL_MS }))
        .filter((a) => now - a.lastAt < LINGER_MS)
        .sort((a, b) => b.firstAt - a.firstAt)
        .slice(0, 3);
    });

    // A dismissed conflict can alert again once it has actually ended.
    for (const key of dismissed.current) if (!currentKeys.has(key)) dismissed.current.delete(key);

    if (live && !muted) {
      // One announcement per pair: the heads-up and the real conflict that follows it
      // aren't both spoken.
      for (const c of current) {
        const last = announced.current.get(c.pair);
        if (last === undefined || now - last > REANNOUNCE_MS) {
          announced.current.set(c.pair, now);
          const urgent = escalation(state, c.pair);
          queue.current.push(urgent ? { text: `Urgent. ${c.spoken}`, gain: LOUD_GAIN } : { text: c.spoken, gain: 1 });
        }
      }
      void playNext();
    }
  }, [state, live, muted, playNext]);

  const dismiss = useCallback((key: string) => {
    dismissed.current.add(key);
    setAlerts((prev) => prev.filter((a) => a.key !== key));
  }, []);

  return {
    alerts,
    flashing: alerts.some((a) => a.active && a.kind !== "predicted"),
    /** Predicted meeting points still ahead, for the map. */
    predicted: alerts.filter((a) => a.kind === "predicted" && a.active),
    muted,
    toggleMute: () => setMuted((m) => !m),
    voiceError,
    dismiss,
  };
}
