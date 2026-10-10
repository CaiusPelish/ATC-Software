import { useCallback, useEffect, useRef, useState } from "react";

export type DrowsinessResult = {
  status: "alert" | "eyes_closed" | "drowsy" | "no_face";
  face_detected: boolean;
  eyes_closed: boolean;
  closed_probability: number;
  closed_duration: number;
  perclos: number;
  alarm: boolean;
};

const pollIntervalMs = 1000;

export function useDrowsinessMonitor() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const beepIntervalRef = useRef<number | null>(null);
  const voiceRequestRef = useRef<AbortController | null>(null);
  const voiceSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [monitoring, setMonitoring] = useState(false);
  const [result, setResult] = useState<DrowsinessResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [soundError, setSoundError] = useState<string | null>(null);

  const stopBeepLoop = useCallback(() => {
    if (beepIntervalRef.current !== null) {
      window.clearInterval(beepIntervalRef.current);
      beepIntervalRef.current = null;
    }
  }, []);

  const stopVoiceAlert = useCallback(() => {
    voiceRequestRef.current?.abort();
    voiceRequestRef.current = null;
    voiceSourceRef.current?.stop();
    voiceSourceRef.current = null;
  }, []);

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
        const response = await fetch("/api/drowsiness/frame", {
          method: "POST",
          body: form,
          signal: controller.signal,
        });
        if (!response.ok) {
          if (response.status === 502 || response.status === 503) {
            throw new Error(
              "The drowsiness backend is not responding. Start the backend server, then try again.",
            );
          }
          const body = response.headers
            .get("content-type")
            ?.includes("application/json")
            ? ((await response.json()) as { detail?: string })
            : null;
          throw new Error(
            body?.detail ?? `Drowsiness analysis failed (HTTP ${response.status}).`,
          );
        }
        const payload = (await response.json()) as
          | DrowsinessResult
          | { detail: string };
        if ("detail" in payload) {
          throw new Error(payload.detail);
        }
        if (active) setResult(payload);
      } catch (caught) {
        if (!active || (caught instanceof Error && caught.name === "AbortError")) {
          return;
        }
        setError(
          caught instanceof TypeError
            ? "Could not reach the drowsiness backend. Check that it is running and refresh this page."
            : caught instanceof Error
              ? caught.message
              : "Drowsiness analysis failed.",
        );
        setMonitoring(false);
        stopBeepLoop();
        stopVoiceAlert();
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
  }, [monitoring, sessionId, stopBeepLoop, stopVoiceAlert]);

  useEffect(
    () => () => {
      requestRef.current?.abort();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      stopBeepLoop();
      stopVoiceAlert();
      if (audioContextRef.current && audioContextRef.current.state !== "closed") {
        void audioContextRef.current.close();
      }
      const session = sessionRef.current;
      if (session) {
        void fetch(
          `/api/drowsiness/session/${encodeURIComponent(session)}`,
          { method: "DELETE", keepalive: true },
        ).catch((caught: unknown) => {
          console.error("Could not clear the drowsiness session.", caught);
        });
      }
    },
    [stopBeepLoop, stopVoiceAlert],
  );

  const startMonitoring = useCallback(async () => {
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
    } catch {
      setSoundError(
        "Audio could not be enabled. Check your browser audio settings.",
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
  }, []);

  const stopMonitoring = useCallback(async () => {
    setMonitoring(false);
    requestRef.current?.abort();
    stopBeepLoop();
    stopVoiceAlert();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const session = sessionRef.current;
    sessionRef.current = null;
    setSessionId(null);

    if (session) {
      try {
        const response = await fetch(
          `/api/drowsiness/session/${encodeURIComponent(session)}`,
          { method: "DELETE" },
        );
        if (!response.ok) {
          throw new Error("Could not clear the drowsiness session on the backend.");
        }
      } catch (caught) {
        setError(
          caught instanceof TypeError
            ? "Could not reach the drowsiness backend to clear this session."
            : caught instanceof Error
              ? caught.message
              : "Could not clear the session.",
        );
      }
    }
  }, [stopBeepLoop, stopVoiceAlert]);

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
    if (!monitoring || !alarming || !soundEnabled || !audioContext) {
      stopBeepLoop();
      return;
    }

    let cancelled = false;
    const playAlarmBeep = () => {
      if (voiceSourceRef.current) return;
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
        stopBeepLoop();
        beepIntervalRef.current = window.setInterval(playAlarmBeep, 5000);
      })
      .catch(() => {
        if (!cancelled) {
          setSoundError("Alarm sound could not play. Check your browser audio settings.");
        }
      });

    return () => {
      cancelled = true;
      stopBeepLoop();
    };
  }, [alarming, monitoring, soundEnabled, stopBeepLoop]);

  useEffect(() => {
    if (!monitoring || !alarming || !soundEnabled) {
      stopVoiceAlert();
      return;
    }

    const controller = new AbortController();
    voiceRequestRef.current = controller;
    let active = true;

    async function playVoiceAlert() {
      try {
        const response = await fetch("/drowsiness/voice-alert", {
          method: "POST",
          signal: controller.signal,
        });
        if (!response.ok) {
          const body = (await response.json()) as { error?: string };
          throw new Error(body.error ?? "The voice alert could not be generated.");
        }

        const audioContext = audioContextRef.current;
        if (!audioContext) throw new Error("Browser audio is unavailable.");
        await audioContext.resume();
        const audioBuffer = await audioContext.decodeAudioData(
          await response.arrayBuffer(),
        );
        if (!active) return;

        const source = audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(audioContext.destination);
        voiceSourceRef.current = source;
        source.addEventListener(
          "ended",
          () => {
            if (voiceSourceRef.current === source) voiceSourceRef.current = null;
          },
          { once: true },
        );
        source.start();
      } catch (caught) {
        if (controller.signal.aborted) return;
        setSoundError(
          caught instanceof Error
            ? caught.message
            : "The ElevenLabs voice alert could not be played.",
        );
      }
    }

    void playVoiceAlert();
    return () => {
      active = false;
      controller.abort();
      if (voiceSourceRef.current) {
        voiceSourceRef.current.stop();
        voiceSourceRef.current = null;
      }
    };
  }, [alarming, monitoring, soundEnabled, stopVoiceAlert]);

  return {
    videoRef,
    canvasRef,
    monitoring,
    result,
    error,
    soundEnabled,
    setSoundEnabled,
    soundError,
    startMonitoring,
    stopMonitoring,
    status,
    statusLabel,
    alarming,
  };
}
