import { useEffect, useRef, useState } from "react";

const MIN_MS = 2500; // always long enough for the plane to fly across
const FADE_MS = 400;
const HOLD_AT_100_MS = 250;

/**
 * Real loading for /sim: the chart image and the first sim state.
 * Progress is time-based but capped until each step is actually done, so it never jumps back.
 */
export function useLoadingProgress({ src, stateReady }: { src: string; stateReady: boolean }) {
  const [imageReady, setImageReady] = useState(false);
  const [progress, setProgress] = useState(0);
  const [done, setDone] = useState(false);
  const [visible, setVisible] = useState(true);
  const steps = useRef({ imageReady, stateReady });
  steps.current = { imageReady, stateReady };

  useEffect(() => {
    const img = new Image();
    // An error still counts as finished, so a missing image can't hang the screen.
    img.onload = img.onerror = () => setImageReady(true);
    img.src = src;
  }, [src]);

  useEffect(() => {
    let frame = 0;
    const start = performance.now();
    const loop = (now: number) => {
      const { imageReady, stateReady } = steps.current;
      const cap = 40 + (imageReady ? 30 : 0) + (stateReady ? 30 : 0);
      const next = Math.min((100 * (now - start)) / MIN_MS, cap);
      setProgress((p) => Math.max(p, next));
      if (next >= 100) return;
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (progress < 100) return;
    const fade = setTimeout(() => setDone(true), HOLD_AT_100_MS);
    const hide = setTimeout(() => setVisible(false), HOLD_AT_100_MS + FADE_MS);
    return () => {
      clearTimeout(fade);
      clearTimeout(hide);
    };
  }, [progress >= 100]);

  // Each message shows for its slice of the bar, and stays up while that step is still loading.
  const message =
    progress < 40 || !imageReady
      ? "Loading airport chart…"
      : progress < 70 || !stateReady
        ? "Contacting ground control…"
        : progress < 100
          ? "Preparing traffic…"
          : "Cleared to taxi";

  return { progress, message, done, visible, fadeMs: FADE_MS };
}
