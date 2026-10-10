// Turns per-frame face measurements (from MediaPipe, in the browser) into the
// drowsiness result the monitor UI shows. Pure logic: no DOM, no React.
//
// PERCLOS = share of recent time the eyes were closed. Closures are judged
// against a per-person baseline learned in the first seconds, with hysteresis
// so a borderline frame can't flicker the state. Looking far down/up (a nod)
// counts as eyes closed, and losing the face keeps the history instead of
// resetting it, because nodding off often takes the face out of frame.

export type DrowsinessStatus = "alert" | "eyes_closed" | "drowsy" | "no_face";

export type DrowsinessResult = {
  status: DrowsinessStatus;
  face_detected: boolean;
  eyes_closed: boolean;
  /** Smoothed eye closure, 0 (wide open) to 1 (shut). */
  closed_probability: number;
  /** Seconds the eyes have been closed right now. */
  closed_duration: number;
  /** Share of the PERCLOS window spent with eyes closed, 0 to 1. */
  perclos: number;
  alarm: boolean;
  /** Combined fatigue score, 0 to 100. */
  fatigue: number;
};

/** One camera frame. `closure` and `pitchDeg` are null when no face was found. */
export type FaceSample = {
  t: number; // seconds
  closure: number | null;
  pitchDeg: number | null;
};

const CALIBRATION_S = 2;
const CLOSURE_MARGIN = 0.35;
const CLOSE_AT_MIN = 0.45;
const CLOSE_AT_MAX = 0.75;
const HYSTERESIS = 0.15;
const NOD_DEG = 20;
const SMOOTHING = 0.4; // EMA weight of the newest frame
const FACE_LOST_GRACE_S = 2;

const PERCLOS_WINDOW_S = 30;
const MIN_WINDOW_S = 5;
const PERCLOS_ALARM = 0.2;
const CLOSED_ALARM_S = 1.0;
const EVENT_WINDOW_S = 60;
const FATIGUE_SMOOTHING = 0.1;

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
};

export class FatigueTracker {
  private calibration: { closure: number[]; pitch: number[]; until: number | null } = {
    closure: [],
    pitch: [],
    until: null,
  };
  private baseline: { closure: number; pitch: number } | null = null;
  private smoothed: number | null = null;
  private closed = false;
  private nodding = false;
  private closedSince: number | null = null;
  private lastFaceT: number | null = null;
  /** (time, closed) samples inside the PERCLOS window. */
  private history: [number, boolean][] = [];
  /** Start times of closures ≥ CLOSED_ALARM_S and of nods, for the fatigue score. */
  private longClosures: number[] = [];
  private nods: number[] = [];
  private longClosureCounted = false;
  private fatigue = 0;

  reset() {
    Object.assign(this, new FatigueTracker());
  }

  update({ t, closure, pitchDeg }: FaceSample): DrowsinessResult {
    const faceVisible = closure !== null && pitchDeg !== null;
    if (faceVisible) {
      this.lastFaceT = t;
      this.observeFace(t, closure, pitchDeg);
    }

    const faceLost = this.lastFaceT === null || t - this.lastFaceT > FACE_LOST_GRACE_S;
    // During the grace period the last state carries on; after it, nothing new is recorded.
    if (!faceLost) this.record(t, this.closed || this.nodding);

    const perclos = this.perclos(t);
    const closedDuration = this.closedSince !== null && !faceLost ? t - this.closedSince : 0;
    const windowSpan = this.history.length ? Math.min(PERCLOS_WINDOW_S, t - this.history[0][0]) : 0;
    const alarm =
      !faceLost &&
      (closedDuration >= CLOSED_ALARM_S || (windowSpan >= MIN_WINDOW_S && perclos >= PERCLOS_ALARM));

    if (closedDuration >= CLOSED_ALARM_S && !this.longClosureCounted) {
      this.longClosures.push(t);
      this.longClosureCounted = true;
    }
    this.longClosures = this.longClosures.filter((s) => t - s <= EVENT_WINDOW_S);
    this.nods = this.nods.filter((s) => t - s <= EVENT_WINDOW_S);

    const target =
      60 * Math.min(perclos / PERCLOS_ALARM, 1) +
      25 * Math.min(this.longClosures.length / 3, 1) +
      15 * Math.min(this.nods.length / 3, 1);
    this.fatigue += (target - this.fatigue) * FATIGUE_SMOOTHING;

    const eyesClosed = !faceLost && (this.closed || this.nodding);
    return {
      status: faceLost ? "no_face" : alarm ? "drowsy" : eyesClosed ? "eyes_closed" : "alert",
      face_detected: !faceLost,
      eyes_closed: eyesClosed,
      closed_probability: faceLost ? 0 : (this.smoothed ?? 0),
      closed_duration: closedDuration,
      perclos,
      alarm,
      fatigue: Math.round(this.fatigue),
    };
  }

  private observeFace(t: number, closure: number, pitchDeg: number) {
    this.smoothed = this.smoothed === null ? closure : this.smoothed + (closure - this.smoothed) * SMOOTHING;

    // Learn this person's open-eye baseline and neutral head pitch first.
    if (!this.baseline) {
      this.calibration.until ??= t + CALIBRATION_S;
      this.calibration.closure.push(closure);
      this.calibration.pitch.push(pitchDeg);
      if (t < this.calibration.until) return;
      this.baseline = { closure: median(this.calibration.closure), pitch: median(this.calibration.pitch) };
    }

    const closeAt = clamp(this.baseline.closure + CLOSURE_MARGIN, CLOSE_AT_MIN, CLOSE_AT_MAX);
    const wasClosed = this.closed || this.nodding;
    if (this.closed ? this.smoothed < closeAt - HYSTERESIS : this.smoothed >= closeAt) {
      this.closed = !this.closed;
    }

    const nodding = Math.abs(pitchDeg - this.baseline.pitch) >= NOD_DEG;
    if (nodding && !this.nodding) this.nods.push(t);
    this.nodding = nodding;

    const isClosed = this.closed || this.nodding;
    if (isClosed && !wasClosed) {
      this.closedSince = t;
      this.longClosureCounted = false;
    } else if (!isClosed) {
      this.closedSince = null;
    }
  }

  private record(t: number, closed: boolean) {
    this.history.push([t, closed]);
    const cutoff = t - PERCLOS_WINDOW_S;
    while (this.history.length > 1 && this.history[1][0] <= cutoff) this.history.shift();
  }

  /** Time-weighted share of the window spent closed (each sample lasts until the next). */
  private perclos(t: number) {
    if (!this.history.length) return 0;
    const cutoff = t - PERCLOS_WINDOW_S;
    const span = Math.min(PERCLOS_WINDOW_S, t - this.history[0][0]);
    if (span <= 0) return 0;
    let closed = 0;
    for (let i = 0; i < this.history.length; i++) {
      const [start, isClosed] = this.history[i];
      const end = i + 1 < this.history.length ? this.history[i + 1][0] : t;
      if (isClosed) closed += Math.max(0, end - Math.max(start, cutoff));
    }
    return clamp(closed / span, 0, 1);
  }
}

/**
 * Head pitch in degrees from MediaPipe's facial transformation matrix
 * (4×4, column-major, as three.js Matrix4.fromArray reads it).
 */
export function pitchFromMatrix(data: number[]): number {
  return (Math.atan2(-data[9], data[10]) * 180) / Math.PI;
}
