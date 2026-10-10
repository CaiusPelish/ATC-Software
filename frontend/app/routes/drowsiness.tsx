import { useEffect, useRef, useState } from "react";
import type { Route } from "./+types/drowsiness";
import { Link } from "react-router";
import { AlertTriangle, ArrowLeft, Camera, CameraOff, Eye } from "lucide-react";
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
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [monitoring, setMonitoring] = useState(false);
  const [result, setResult] = useState<DrowsinessResult | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    setResult(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Camera access is unavailable. Use HTTPS or open the app on localhost.");
      return;
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
    alert: "Alert",
    drowsy: "Drowsiness detected",
    eyes_closed: "Eyes closed",
    no_face: "No face detected",
    starting: "Starting monitor",
    stopped: "Monitor stopped",
  };
  const alarming = result?.alarm === true;

  return (
    <main className="mx-auto flex min-h-svh w-full max-w-5xl flex-col gap-8 px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <Button asChild variant="ghost" className="-ml-3">
            <Link to="/">
              <ArrowLeft />
              Home
            </Link>
          </Button>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            Drowsiness monitor
          </h1>
          <p className="mt-1 text-muted-foreground">
            Opt-in webcam monitoring with computer vision
          </p>
        </div>
        {monitoring ? (
          <Button onClick={() => void stopMonitoring()} variant="outline">
            <CameraOff />
            Stop monitoring
          </Button>
        ) : (
          <Button onClick={() => void startMonitoring()}>
            <Camera />
            Start monitoring
          </Button>
        )}
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.8fr)]">
        <Card className="overflow-hidden">
          <CardContent className="p-0">
            <div className="relative aspect-video bg-black">
              <video
                aria-label="Webcam preview"
                autoPlay
                className="size-full object-cover"
                muted
                playsInline
                ref={videoRef}
              />
              <canvas className="hidden" ref={canvasRef} />
              {!monitoring && (
                <div className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">
                  Camera preview will appear after you start monitoring.
                </div>
              )}
              {monitoring && (
                <div
                  className={`absolute left-4 top-4 flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium ${
                    alarming
                      ? "bg-destructive text-white"
                      : "bg-background/85 text-foreground"
                  }`}
                >
                  {alarming ? (
                    <AlertTriangle className="size-4" />
                  ) : (
                    <span className="size-2 animate-pulse rounded-full bg-green-500" />
                  )}
                  {statusLabel[status]}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Eye className="size-5" />
              Live assessment
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5">
            <p
              aria-live="polite"
              className={`text-xl font-semibold ${
                alarming ? "text-destructive" : ""
              }`}
            >
              {statusLabel[status]}
            </p>
            <Metric
              label="Closed-eye probability"
              value={`${((result?.closed_probability ?? 0) * 100).toFixed(0)}%`}
            />
            <Metric
              label="PERCLOS (15-second window)"
              value={`${((result?.perclos ?? 0) * 100).toFixed(0)}%`}
            />
            <Metric
              label="Continuous eye closure"
              value={`${(result?.closed_duration ?? 0).toFixed(1)} s`}
            />
            {error && (
              <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                {error}
              </p>
            )}
            <p className="text-sm leading-relaxed text-muted-foreground">
              This experimental aid is not a medical or safety-critical system.
              Keep your attention on your work and follow your organization's
              fatigue-management procedures.
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Camera and data</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm text-muted-foreground">
          <p>
            Your browser requests camera permission only after you choose Start
            monitoring. The live preview remains in your browser.
          </p>
          <p>
            Camera frames are sent to the backend for face/eye detection and
            Hugging Face model inference. The backend does not save frame images;
            it keeps only short-lived eye-state measurements in memory.
          </p>
          <p>
            The first assessment with a detected face downloads the configured
            model and may take longer than later checks. Use a well-lit space
            and keep your face visible for best results.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b pb-3">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono font-medium tabular-nums">{value}</span>
    </div>
  );
}
