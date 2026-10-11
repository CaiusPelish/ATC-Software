import { Route as RouteIcon, TriangleAlert, Volume2, VolumeX, X } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import type { useConflictAlerts } from "~/hooks/use-conflict-alerts";
import { cn } from "~/lib/utils";

type Alerts = ReturnType<typeof useConflictAlerts>;

const swallow = {
  onPointerDown: (e: React.PointerEvent) => e.stopPropagation(),
  onPointerUp: (e: React.PointerEvent) => e.stopPropagation(),
};

/** Red screen flash while a conflict is happening, plus sticky alert cards. */
export function ConflictAlerts({ alerts: a, live }: { alerts: Alerts; live: boolean }) {
  return (
    <>
      {/* The flash sits over the map but under the UI, and never blocks clicks */}
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-0 bg-destructive transition-opacity",
          a.flashing ? "opacity-20 motion-safe:animate-alarm" : "opacity-0",
        )}
        data-flash={a.flashing ? "on" : "off"}
      />

      {a.alerts.length > 0 && (
        <div className="pointer-events-none absolute top-16 left-4 z-20 flex w-[min(22rem,calc(100%-2rem))] flex-col gap-2" role="alert">
          {a.alerts.map((alert) => (
            <Card
              key={alert.key}
              className={cn(
                "pointer-events-auto gap-0 bg-card py-3 transition-opacity duration-1000",
                alert.severity === "critical" ? "border-destructive ring-1 ring-destructive/50" : "border-taxiway",
                alert.fadedAt !== undefined ? "pointer-events-none opacity-0" : !alert.active && "opacity-80",
              )}
              data-fading={alert.fadedAt !== undefined ? "" : undefined}
              data-alert={alert.kind}
              {...swallow}
            >
              <CardHeader className="gap-1 px-4">
                <div className="flex items-center justify-between gap-2">
                  <p
                    className={cn(
                      "flex items-center gap-2 font-mono text-[11px] font-semibold tracking-[0.18em] uppercase",
                      alert.severity === "critical" ? "text-destructive" : "text-taxiway",
                      alert.active && "motion-safe:animate-pulse",
                    )}
                  >
                    <TriangleAlert className="size-3.5" /> {alert.title}
                  </p>
                  <div className="flex items-center gap-1">
                    {live && (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={a.muted ? "Unmute voice alerts" : "Mute voice alerts"}
                        onClick={a.toggleMute}
                      >
                        {a.muted ? <VolumeX /> : <Volume2 />}
                      </Button>
                    )}
                    <Button variant="ghost" size="icon-xs" aria-label="Dismiss alert" onClick={() => a.dismiss(alert.key)}>
                      <X />
                    </Button>
                  </div>
                </div>
                <CardTitle className="text-sm font-medium">{alert.text}</CardTitle>
                {alert.escalated && (
                  <p className="font-mono text-[10px] tracking-wider text-destructive uppercase" data-escalated="">
                    Escalated · controller fatigue {alert.escalated.fatigue}
                  </p>
                )}
                {a.canSolve(alert) && alert.fadedAt === undefined && (
                  <Button
                    size="sm"
                    className="mt-1 w-fit gap-1.5"
                    onClick={() => void a.solve(alert.ids)}
                    data-solve=""
                  >
                    <RouteIcon /> Solution path
                  </Button>
                )}
                <CardDescription className="text-xs">
                  {alert.kind === "predicted"
                    ? alert.active
                      ? `Predicted · in ${Math.max(1, Math.round(alert.eta ?? 0))} s unless someone holds`
                      : "No longer predicted"
                    : alert.active
                      ? "Happening now"
                      : "Cleared"}
                  {live && a.voiceError ? ` · Voice: ${a.voiceError}` : ""}
                </CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
