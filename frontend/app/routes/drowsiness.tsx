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
import { useDrowsinessMonitor } from "~/hooks/use-drowsiness-monitor";

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
    status,
    statusLabel,
    alarming,
  } = useDrowsinessMonitor();

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
                  <span>{soundError ?? (soundEnabled ? "Two-tone beep repeats every 5 seconds; ElevenLabs speaks when drowsiness is first detected." : "Sound and voice alerts are muted.")}</span>
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
