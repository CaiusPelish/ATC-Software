import { useCallback, useEffect, useRef, useState } from "react";
import type { FaceLandmarker } from "@mediapipe/tasks-vision";
// Bundled by Vite, so face tracking works offline and in the Docker build.
import wasmLoaderPath from "@mediapipe/tasks-vision/vision_wasm_internal.js?url";
import wasmBinaryPath from "@mediapipe/tasks-vision/vision_wasm_internal.wasm?url";

import { FatigueTracker, pitchFromMatrix, type DrowsinessResult } from "~/lib/fatigue";

export type { DrowsinessResult } from "~/lib/fatigue";

const MODEL_PATH = "/mediapipe/face_landmarker.task";
const UI_UPDATE_MS = 100; // tracking runs every frame; React only re-renders ~10x a second

/** Loads MediaPipe once per page. GPU first, CPU if the browser can't do WebGL. */
let landmarkerPromise: Promise<FaceLandmarker> | null = null;
function loadLandmarker() {
  landmarkerPromise ??= (async () => {
    const { FaceLandmarker } = await import("@mediapipe/tasks-vision");
    const create = (delegate: "GPU" | "CPU") =>
      FaceLandmarker.createFromOptions(
        { wasmLoaderPath, wasmBinaryPath },
        {
          baseOptions: { modelAssetPath: MODEL_PATH, delegate },
          runningMode: "VIDEO",
          numFaces: 1,
          outputFaceBlendshapes: true,
          outputFacialTransformationMatrixes: true,
        },
      );
    try {
      return await create("GPU");
    } catch {
      return await create("CPU");
    }
  })();
  landmarkerPromise.catch(() => {
    landmarkerPromise = null; // allow a retry on the next Start
  });
  return landmarkerPromise;
}

export function useDrowsinessMonitor() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const beepIntervalRef = useRef<number | null>(null);
  const voiceRequestRef = useRef<AbortController | null>(null);
  const voiceSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const [landmarker, setLandmarker] = useState<FaceLandmarker | null>(null);
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

  // Every new video frame: MediaPipe face landmarks -> eye closure + head pitch -> fatigue.
  useEffect(() => {
    if (!monitoring || !landmarker) return;

    const tracker = new FatigueTracker();
    let frame = 0;
    let lastVideoTime = -1;
    let lastUiUpdate = 0;
    let lastStatus: DrowsinessResult["status"] | null = null;

    const loop = () => {
      frame = requestAnimationFrame(loop);
      const video = videoRef.current;
      if (!video || video.readyState < 2 || video.currentTime === lastVideoTime) return;
      lastVideoTime = video.currentTime;

      const now = performance.now();
      try {
        const detection = landmarker.detectForVideo(video, now);
        const blink = detection.faceBlendshapes[0]?.categories;
        const matrix = detection.facialTransformationMatrixes[0];
        const score = (name: string) => blink?.find((c) => c.categoryName === name)?.score ?? 0;
        const next = tracker.update({
          t: now / 1000,
          closure: blink ? (score("eyeBlinkLeft") + score("eyeBlinkRight")) / 2 : null,
          pitchDeg: blink && matrix ? pitchFromMatrix(matrix.data) : null,
        });
        // Throttle re-renders, but show a status change (e.g. an alarm) immediately.
        if (now - lastUiUpdate >= UI_UPDATE_MS || next.status !== lastStatus) {
          lastUiUpdate = now;
          lastStatus = next.status;
          setResult(next);
        }
      } catch (caught) {
        cancelAnimationFrame(frame);
        setError(caught instanceof Error ? caught.message : "Face tracking stopped unexpectedly.");
        setMonitoring(false);
        stopBeepLoop();
        stopVoiceAlert();
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [monitoring, landmarker, stopBeepLoop, stopVoiceAlert]);

  useEffect(
    () => () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      stopBeepLoop();
      stopVoiceAlert();
      if (audioContextRef.current && audioContextRef.current.state !== "closed") {
        void audioContextRef.current.close();
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

      setMonitoring(true);
      setLandmarker(await loadLandmarker());
    } catch (caught) {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setError(
        caught instanceof Error
          ? caught.message
          : "Camera access was denied or could not be started.",
      );
      setMonitoring(false);
    }
  }, []);

  const stopMonitoring = useCallback(() => {
    setMonitoring(false);
    stopBeepLoop();
    stopVoiceAlert();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, [stopBeepLoop, stopVoiceAlert]);

  const status = result?.status ?? (monitoring ? (landmarker ? "starting" : "loading") : "stopped");
  const statusLabel: Record<string, string> = {
    alert: "Eyes open",
    drowsy: "Drowsiness detected",
    eyes_closed: "Eyes closed",
    no_face: "No face detected",
    loading: "Loading face tracker",
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
      return;
    }

    if (voiceRequestRef.current || voiceSourceRef.current) return;

    const controller = new AbortController();
    voiceRequestRef.current = controller;

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
        if (controller.signal.aborted) return;

        const source = audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(audioContext.destination);
        voiceSourceRef.current = source;
        voiceRequestRef.current = null;
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
      } finally {
        if (voiceRequestRef.current === controller) {
          voiceRequestRef.current = null;
        }
      }
    }

    void playVoiceAlert();
  }, [alarming, monitoring, soundEnabled]);

  return {
    videoRef,
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
