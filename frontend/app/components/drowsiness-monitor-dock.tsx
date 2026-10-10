import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  Camera,
  CameraOff,
  ChevronDown,
  ChevronUp,
  Eye,
  Info,
  UserRound,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useDrowsinessMonitor } from "~/hooks/use-drowsiness-monitor";

export function DrowsinessMonitorDock() {
  const [expanded, setExpanded] = useState(true);
  const {
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
    statusLabel,
    alarming,
  } = useDrowsinessMonitor();

  const status = monitoring
    ? alarming
      ? "Drowsiness detected"
      : result?.status === "no_face"
        ? "No face detected"
        : result?.status === "eyes_closed"
          ? "Eyes closed"
          : result
            ? "Alert and focused"
            : "Analyzing"
    : "Monitor standby";

  return (
    <aside className={`sim-monitor-dock ${alarming ? "sim-monitor-alert" : ""}`}>
      <div className="sim-monitor-topline">
        <span className="sim-monitor-avatar">
          <UserRound size={19} aria-hidden="true" />
        </span>
        <span className="sim-monitor-title">
          <strong>Controller wellness</strong>
          <span className={monitoring ? "sim-monitor-live" : ""}>
            <i /> {monitoring ? status : "Camera off"}
          </span>
        </span>
        <button
          className="sim-monitor-icon-button"
          type="button"
          aria-label={soundEnabled ? "Mute drowsiness alerts" : "Enable drowsiness alerts"}
          aria-pressed={soundEnabled}
          title={soundEnabled ? "Mute alerts" : "Enable alerts"}
          onClick={() => setSoundEnabled((enabled) => !enabled)}
        >
          {soundEnabled ? <Volume2 size={17} /> : <VolumeX size={17} />}
        </button>
        <button
          className="sim-monitor-icon-button"
          type="button"
          aria-label={expanded ? "Collapse controller wellness panel" : "Expand controller wellness panel"}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? <ChevronDown size={17} /> : <ChevronUp size={17} />}
        </button>
      </div>

      <div className={`sim-monitor-body ${expanded ? "" : "sim-monitor-body-collapsed"}`}>
        <div className="sim-monitor-person-row">
          <div className={`sim-monitor-camera ${monitoring ? "sim-monitor-camera-live" : ""}`}>
            <video
              aria-label="Controller camera preview"
              autoPlay
              muted
              playsInline
              ref={videoRef}
            />
            <canvas className="hidden" ref={canvasRef} />
            {!monitoring && <UserRound size={22} aria-hidden="true" />}
            {monitoring && <span className="sim-monitor-camera-dot" />}
          </div>
          <div className="sim-monitor-person-copy">
            <span>ATC CONTROLLER</span>
            <strong>{monitoring ? status : "Ready to monitor"}</strong>
            <small>{monitoring ? "Camera analysis active" : "Start when ready"}</small>
          </div>
          {alarming ? (
            <span className="sim-monitor-warning" aria-label="Drowsiness alarm active">
              <AlertTriangle size={17} />
            </span>
          ) : (
            <span className="sim-monitor-ok" aria-label="No active drowsiness alarm">
              <Activity size={17} />
            </span>
          )}
        </div>

        <div className="sim-monitor-metrics">
          <div>
            <span>Eyes closed</span>
            <strong>{(result?.closed_duration ?? 0).toFixed(1)}s</strong>
          </div>
          <div>
            <span>PERCLOS · 15s</span>
            <strong>{((result?.perclos ?? 0) * 100).toFixed(0)}%</strong>
          </div>
          <div>
            <span>Face</span>
            <strong>{monitoring ? (result?.face_detected ? "Detected" : "Searching") : "Off"}</strong>
          </div>
        </div>

        {error && <p className="sim-monitor-error" role="alert">{error}</p>}
        {soundError && <p className="sim-monitor-error" role="status">{soundError}</p>}

        <button
          className={`sim-monitor-action ${monitoring ? "sim-monitor-action-stop" : ""}`}
          type="button"
          onClick={() => {
            if (monitoring) void stopMonitoring();
            else void startMonitoring();
          }}
        >
          {monitoring ? <CameraOff size={16} /> : <Camera size={16} />}
          {monitoring ? "Stop monitoring" : "Start monitoring"}
        </button>

        <p className="sim-monitor-audio-note">
          <Eye size={13} aria-hidden="true" />
          {soundEnabled
            ? "Beeps repeat during an alarm; ElevenLabs gives a voice warning when drowsiness is detected."
            : "Beeps are muted. A voice warning already in progress will finish."}
        </p>

        <p className="sim-monitor-audio-note">
          <Info size={14} aria-hidden="true" />
          <span>
            Experimental aid, not safety-critical. Accuracy depends on good
            lighting and keeping your face in frame; poor lighting or framing
            can cause misjudgments.
          </span>
        </p>
      </div>

      <span className="sr-only" aria-live="polite">
        {statusLabel[monitoring ? (result?.status ?? "starting") : "stopped"]}
      </span>
    </aside>
  );
}
