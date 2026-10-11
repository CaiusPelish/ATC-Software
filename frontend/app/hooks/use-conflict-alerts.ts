import { useCallback, useEffect, useRef, useState } from "react";

import { describeConflict, type ConflictAlert } from "~/lib/alert-speech";
import type { SimulationState } from "~/lib/sim-state";

const LINGER_MS = 10_000; // an alert stays up this long after its conflict ends
const FLASH_TAIL_MS = 1_500; // the red flash keeps going briefly after a conflict clears
const REANNOUNCE_MS = 30_000; // the same conflict isn't spoken again within this window

export type ShownAlert = ConflictAlert & { firstAt: number; lastAt: number; active: boolean };

/**
 * Conflicts from the backend become sticky on-screen alerts, a red flash while one is
 * happening, and (live traffic only) a spoken ElevenLabs announcement per new conflict.
 */
export function useConflictAlerts(state: SimulationState | null, live: boolean) {
  const [alerts, setAlerts] = useState<ShownAlert[]>([]);
  const [muted, setMuted] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const dismissed = useRef(new Set<string>());
  const announced = useRef(new Map<string, number>());
  const audio = useRef<AudioContext | null>(null);
  const queue = useRef<string[]>([]);
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
    const text = queue.current.shift();
    if (!text) return;
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
      source.connect(ctx.destination);
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
    const current = state.conflicts.map((c) => describeConflict(c, state));
    const currentKeys = new Set(current.map((c) => c.key));

    setAlerts((prev) => {
      const byKey = new Map(prev.map((a) => [a.key, a]));
      for (const c of current) {
        if (dismissed.current.has(c.key)) continue;
        const old = byKey.get(c.key);
        byKey.set(c.key, {
          ...c,
          // Once critical, stay critical while the alert is up.
          severity: old?.severity === "critical" ? "critical" : c.severity,
          firstAt: old?.firstAt ?? now,
          lastAt: now,
          active: true,
        });
      }
      return [...byKey.values()]
        .map((a) => (currentKeys.has(a.key) ? a : { ...a, active: now - a.lastAt < FLASH_TAIL_MS }))
        .filter((a) => now - a.lastAt < LINGER_MS)
        .sort((a, b) => b.firstAt - a.firstAt)
        .slice(0, 3);
    });

    // A dismissed conflict can alert again once it has actually ended.
    for (const key of dismissed.current) if (!currentKeys.has(key)) dismissed.current.delete(key);

    if (live && !muted) {
      for (const c of current) {
        const last = announced.current.get(c.key);
        if (last === undefined || now - last > REANNOUNCE_MS) {
          announced.current.set(c.key, now);
          queue.current.push(c.spoken);
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
    flashing: alerts.some((a) => a.active),
    muted,
    toggleMute: () => setMuted((m) => !m),
    voiceError,
    dismiss,
  };
}
