import { Plane } from "lucide-react";

import { Progress } from "~/components/ui/progress";
import { useLoadingProgress } from "~/hooks/use-loading-progress";
import { cn } from "~/lib/utils";

// Same dash as the hold-short marking on the homepage.
const contrail = "bg-[repeating-linear-gradient(90deg,var(--taxiway)_0_14px,transparent_14px_24px)]";

/** Full-screen loader for /sim. Owns its own progress state so the map underneath doesn't re-render per frame. */
export function LoadingScreen({ stateReady }: { stateReady: boolean }) {
  const { progress, message, done, visible, fadeMs } = useLoadingProgress({
    src: "/kewr-chart.jpg",
    stateReady,
  });
  if (!visible) return null;
  const pct = Math.round(progress);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`${message} ${pct}%`}
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-background px-4 transition-opacity",
        done && "pointer-events-none opacity-0",
      )}
      style={{ transitionDuration: `${fadeMs}ms` }}
    >
      <img src="/ewr-backdrop.png" alt="" className="absolute inset-0 size-full scale-105 object-cover blur-md" />
      <div className="absolute inset-0 bg-scrim" />
      <div className="absolute inset-0 bg-radial from-transparent to-background" />

      <div className="relative flex w-full max-w-2xl flex-col gap-8">
        <div className="flex flex-col gap-2">
          <p className="font-mono text-xs tracking-[0.3em] text-beacon uppercase">KEWR · Ground Control</p>
          <h1 className="text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">Hold Short</h1>
        </div>

        {/* Flight track: the plane's x position is the progress value */}
        <div className="relative h-12">
          <div
            className={cn("absolute top-1/2 left-0 h-0.5 -translate-y-1/2 opacity-70", contrail)}
            style={{ width: `${progress}%` }}
          />
          <div
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${progress}%` }}
          >
            {/* Bob and rotation are both transforms, so they live on separate elements */}
            <div className="motion-safe:animate-bob">
              <Plane aria-hidden className="size-9 rotate-45 fill-foreground stroke-background" strokeWidth={1} />
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <Progress value={pct} className="h-1.5 bg-secondary" />
          <div className="flex items-center justify-between font-mono text-xs tracking-widest uppercase">
            <span className={cn(progress >= 100 ? "text-beacon" : "text-muted-foreground")}>{message}</span>
            <span className="text-foreground tabular-nums">{pct}%</span>
          </div>
        </div>
      </div>
    </div>
  );
}
