import { useEffect, useRef, useState } from "react";
import type { Route } from "./+types/drowsiness";
import { Link } from "react-router";
import {
  AlertTriangle,
  ArrowLeft,
  AudioLines,
  Camera,
  CameraOff,
  Check,
  Eye,
  EyeOff,
  Info,
  Plane,
  ShieldCheck,
  Volume2,
  VolumeX,
} from "lucide-react";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";

type DrowsinessResult = {
  status: "alert" | "eyes_closed" | "drowsy" | "no_face";
  face_detected: boolean;
  eyes_closed: boolean;
  closed_probability: number;
  closed_duration: number;
  perclos: number;
  alarm: boolean;
};

const backendUrl = import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";
const pollIntervalMs = 1000;

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Drowsiness Monitor | Hold Short" },
    {
      name: "description",
      content: "Opt-in webcam monitoring for signs of controller drowsiness.",
    },
  ];
}

export default function DrowsinessMonitorPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [monitoring, setMonitoring] = useState(false);
  const [result, setResult] = useState<DrowsinessResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [soundError, setSoundError] = useState<string | null>(null);

  useEffect(() => {
    if (!monitoring || !sessionId) return;

    const activeSessionId = sessionId;
    let active = true;
    let timer: number | undefined;

    async function analyzeNextFrame() {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!active || !video || !canvas || video.videoWidth === 0) {
        if (active) timer = window.setTimeout(analyzeNextFrame, pollIntervalMs);
        return;
      }

      try {
        const scale = Math.min(1, 640 / video.videoWidth);
        canvas.width = Math.round(video.videoWidth * scale);
        canvas.height = Math.round(video.videoHeight * scale);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("This browser could not prepare a camera frame.");
        context.drawImage(video, 0, 0, canvas.width, canvas.height);

        const image = await new Promise<Blob>((resolve, reject) => {
          canvas.toBlob(
            (blob) =>
              blob
                ? resolve(blob)
                : reject(new Error("Could not capture a camera frame.")),
            "image/jpeg",
            0.8,
          );
        });
        const form = new FormData();
        form.append("session_id", activeSessionId);
        form.append("frame", image, "camera-frame.jpg");

        const controller = new AbortController();
        requestRef.current = controller;
        const response = await fetch(`${backendUrl}/api/drowsiness/frame`, {
          method: "POST",
          body: form,
          signal: controller.signal,
        });
        const payload = (await response.json()) as
          | DrowsinessResult
          | { detail: string };
        if (!response.ok || "detail" in payload) {
          throw new Error(
            "detail" in payload ? payload.detail : "Drowsiness analysis failed.",
          );
        }
        if (active) setResult(payload);
      } catch (caught) {
        if (!active || (caught instanceof Error && caught.name === "AbortError")) {
          return;
        }
        setError(
          caught instanceof Error ? caught.message : "Drowsiness analysis failed.",
        );
        setMonitoring(false);
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }

      if (active) timer = window.setTimeout(analyzeNextFrame, pollIntervalMs);
    }

    void analyzeNextFrame();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
      requestRef.current?.abort();
    };
  }, [monitoring, sessionId]);

  useEffect(
    () => () => {
      requestRef.current?.abort();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (audioContextRef.current && audioContextRef.current.state !== "closed") {
        void audioContextRef.current?.close();
      }
      const session = sessionRef.current;
      if (session) {
        void fetch(
          `${backendUrl}/api/drowsiness/session/${encodeURIComponent(session)}`,
          { method: "DELETE", keepalive: true },
        ).catch((caught: unknown) => {
          console.error("Could not clear the drowsiness session.", caught);
        });
      }
    },
    [],
  );

  async function startMonitoring() {
    setError(null);
    setSoundError(null);
    setResult(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Camera access is unavailable. Use HTTPS or open the app on localhost.");
      return;
    }

    try {
      if (!audioContextRef.current || audioContextRef.current.state === "closed") {
        audioContextRef.current = new AudioContext();
      }
      await audioContextRef.current.resume();
    } catch (caught) {
      setSoundError(
        caught instanceof Error
          ? "Audio could not be enabled. Check your browser audio settings."
          : "Audio is unavailable in this browser.",
      );
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
      streamRef.current = stream;
      if (!videoRef.current) throw new Error("The camera preview could not be started.");
      videoRef.current.srcObject = stream;
      await videoRef.current.play();

      const session = crypto.randomUUID();
      sessionRef.current = session;
      setSessionId(session);
      setMonitoring(true);
    } catch (caught) {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setError(
        caught instanceof Error
          ? caught.message
          : "Camera access was denied or could not be started.",
      );
    }
  }

  async function stopMonitoring() {
    setMonitoring(false);
    requestRef.current?.abort();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const session = sessionRef.current;
    sessionRef.current = null;
    setSessionId(null);

    if (session) {
      try {
        const response = await fetch(
          `${backendUrl}/api/drowsiness/session/${encodeURIComponent(session)}`,
          { method: "DELETE" },
        );
        if (!response.ok) {
          throw new Error("Could not clear the drowsiness session on the backend.");
        }
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Could not clear the session.",
        );
      }
    }
  }

  const status = result?.status ?? (monitoring ? "starting" : "stopped");
  const statusLabel: Record<string, string> = {
    alert: "Eyes open",
    drowsy: "Drowsiness detected",
    eyes_closed: "Eyes closed",
    no_face: "No face detected",
    starting: "Starting monitor",
    stopped: "Monitor stopped",
  };
  const alarming = monitoring && result?.alarm === true;

  useEffect(() => {
    const audioContext = audioContextRef.current;
    if (!monitoring || !alarming || !soundEnabled || !audioContext) return;

    let cancelled = false;
    let interval: number | undefined;
    const playAlarmBeep = () => {
      const now = audioContext.currentTime;
      for (const [index, frequency] of [880, 1046].entries()) {
        const start = now + index * 0.2;
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.16, start + 0.025);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
        oscillator.connect(gain);
        gain.connect(audioContext.destination);
        oscillator.start(start);
        oscillator.stop(start + 0.18);
      }
    };

    void audioContext
      .resume()
      .then(() => {
        if (cancelled) return;
        playAlarmBeep();
        interval = window.setInterval(playAlarmBeep, 5000);
      })
      .catch(() => {
        if (!cancelled) {
          setSoundError("Alarm sound could not play. Check your browser audio settings.");
        }
      });

    return () => {
      cancelled = true;
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [alarming, monitoring, soundEnabled]);

  return (
    <main className="drowsy-page">
      <header className="drowsy-header">
        <div className="drowsy-heading">
          <Button asChild variant="ghost" className="drowsy-back">
            <Link to="/">
              <ArrowLeft />
              <span>Home</span>
            </Link>
          </Button>
          <div className="drowsy-title">
            <div className="drowsy-kicker"><span /> ATC WELLNESS SYSTEM</div>
            <h1>Drowsiness monitor</h1>
            <p>Stay sharp. We&apos;ll watch for signs you need a break.</p>
          </div>
        </div>
        <div className="drowsy-actions">
          <Button asChild variant="outline" className="simulation-link">
            <Link to="/sim">
              <Plane />
              Flight simulation
            </Link>
          </Button>
          <Button
            variant="outline"
            className={`sound-toggle ${soundEnabled ? "sound-on" : ""}`}
            aria-pressed={soundEnabled}
            onClick={() => setSoundEnabled((enabled) => !enabled)}
          >
            {soundEnabled ? <Volume2 /> : <VolumeX />}
            <span>Sound {soundEnabled ? "on" : "off"}</span>
          </Button>
          {monitoring ? (
            <Button onClick={() => void stopMonitoring()} className="monitor-stop">
              <CameraOff />
              Stop monitor
            </Button>
          ) : (
            <Button onClick={() => void startMonitoring()} className="monitor-start">
              <Camera />
              Start monitoring
            </Button>
          )}
        </div>
      </header>

      <section className="drowsy-workspace">
        <div className="drowsy-overview">
          <div className={`session-status ${monitoring ? "session-live" : ""} ${alarming ? "session-alert" : ""}`}>
            <span className="session-indicator" />
            <span>{monitoring ? (alarming ? "Attention needed" : "Monitoring live") : "Monitor ready"}</span>
          </div>
          <p>The preview stays in your browser. Camera frames are sent to the connected detector for analysis and are not stored.</p>
        </div>

        <div className="drowsy-grid">
          <Card className={`camera-card ${alarming ? "camera-alarming" : ""}`}>
            <div className="camera-card-heading">
              <div>
                <div className="panel-kicker"><Eye size={14} /> LIVE CAMERA FEED</div>
                <h2>Driver awareness</h2>
                <p>Keep your face centered and well lit.</p>
              </div>
              <div className={`camera-state ${monitoring ? "camera-state-live" : ""}`}>
                <span />
                {monitoring ? "LIVE" : "STANDBY"}
              </div>
            </div>
            <CardContent className="camera-content">
              <div className={`camera-view ${monitoring ? "camera-view-live" : ""}`}>
                <video
                  aria-label="Webcam preview"
                  autoPlay
                  className="camera-video"
                  muted
                  playsInline
                  ref={videoRef}
                />
                <canvas className="hidden" ref={canvasRef} />
                {!monitoring && (
                  <div className="camera-placeholder">
                    <div className="camera-placeholder-icon"><Camera size={27} /></div>
                    <strong>Your camera preview will appear here</strong>
                    <span>Start monitoring when you&apos;re ready.</span>
                  </div>
                )}
                {monitoring && (
                  <>
                    <div className="camera-live-badge">
                      {alarming ? <AlertTriangle size={14} /> : <span className="live-pulse" />}
                      {statusLabel[status]}
                    </div>
                    <div className="camera-corners" aria-hidden="true"><i /><i /><i /><i /></div>
                    <span className="camera-time">FRAME CHECK · 1S</span>
                  </>
                )}
              </div>
              <div className="camera-footnote">
                <span><ShieldCheck size={14} /> Private camera session</span>
                <span><AudioLines size={14} /> Sound alerts {soundEnabled ? "enabled" : "muted"}</span>
              </div>
            </CardContent>
          </Card>

          <aside className="assessment-column">
            <Card className={`assessment-card ${alarming ? "assessment-alarming" : ""}`}>
              <CardHeader className="assessment-header">
                <div>
                  <div className="panel-kicker">REAL-TIME FEEDBACK</div>
                  <CardTitle>Live assessment</CardTitle>
                </div>
                <div className={`assessment-icon ${alarming ? "assessment-icon-alert" : ""}`}>
                  {alarming ? <AlertTriangle size={18} /> : <Eye size={18} />}
                </div>
              </CardHeader>
              <CardContent className="assessment-content">
                <div className={`assessment-state ${alarming ? "assessment-state-alert" : ""}`} aria-live="polite">
                  <span className="assessment-state-dot" />
                  <span>{statusLabel[status]}</span>
                </div>
                <div className="metrics">
                  <Metric
                    label="Closed-eye probability"
                    value={`${((result?.closed_probability ?? 0) * 100).toFixed(0)}%`}
                    progress={(result?.closed_probability ?? 0) * 100}
                    alert={alarming}
                  />
                  <Metric
                    label="PERCLOS · 15 sec"
                    value={`${((result?.perclos ?? 0) * 100).toFixed(0)}%`}
                    progress={(result?.perclos ?? 0) * 100}
                    alert={alarming}
                  />
                  <Metric
                    label="Continuous eye closure"
                    value={`${(result?.closed_duration ?? 0).toFixed(1)} s`}
                    progress={Math.min(((result?.closed_duration ?? 0) / 2) * 100, 100)}
                    alert={alarming}
                  />
                </div>
                <div className={`sound-notice ${soundError ? "sound-notice-error" : ""}`}>
                  {soundError ? <VolumeX size={15} /> : soundEnabled ? <Volume2 size={15} /> : <VolumeX size={15} />}
                  <span>{soundError ?? (soundEnabled ? "Two-tone alert repeats every 5 seconds while drowsy." : "Sound alerts are muted.")}</span>
                </div>
                {error && (
                  <p role="alert" className="drowsy-error">
                    <Info size={15} /> {error}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card className="interpretation-card">
              <CardHeader className="interpretation-header">
                <div className="interpretation-icon"><EyeOff size={17} /></div>
                <div><CardTitle>What we&apos;re checking</CardTitle><p>Small signs, early awareness.</p></div>
              </CardHeader>
              <CardContent className="interpretation-content">
                <div><span className="interpretation-index">01</span><span>Eye closure patterns</span><Check size={14} /></div>
                <div><span className="interpretation-index">02</span><span>Time spent with eyes closed</span><Check size={14} /></div>
                <div><span className="interpretation-index">03</span><span>Changes over the last 15 sec</span><Check size={14} /></div>
              </CardContent>
            </Card>
          </aside>
        </div>

        <section className="privacy-strip">
          <div className="privacy-icon"><ShieldCheck size={17} /></div>
          <div className="privacy-copy">
            <strong>Your camera stays yours.</strong>
            <span>Frames are checked by the connected backend and aren&apos;t saved. Only short-lived eye-state measurements are kept during this session.</span>
          </div>
          <div className="privacy-divider" />
          <div className="privacy-copy privacy-hint">
            <strong>First run can take a moment.</strong>
            <span>The detection model may download its weights the first time it sees a face.</span>
          </div>
        </section>

        <footer className="drowsy-disclaimer">
          <Info size={14} />
          <span>This experimental aid is not a medical or safety-critical system. Follow your organization&apos;s fatigue-management procedures.</span>
        </footer>
      </section>
    </main>
  );
}

function Metric({
  label,
  value,
  progress,
  alert,
}: {
  label: string;
  value: string;
  progress: number;
  alert: boolean;
}) {
  return (
    <div className="metric-row">
      <div className="metric-label-row"><span>{label}</span><strong>{value}</strong></div>
      <div className="metric-track"><span className={alert ? "metric-fill metric-fill-alert" : "metric-fill"} style={{ width: `${Math.max(0, Math.min(progress, 100))}%` }} /></div>
    </div>
  );
}
