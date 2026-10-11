import { Link } from "react-router";
import { ArrowLeft, Eye, Flame } from "lucide-react";

import type { Route } from "./+types/sim";
import { ConflictAlerts } from "~/components/conflict-alerts";
import { CrashChallenge } from "~/components/crash-challenge";
import { DrowsinessMonitorDock } from "~/components/drowsiness-monitor-dock";
import { GroundChart } from "~/components/ground-chart";
import { LoadingScreen } from "~/components/loading-screen";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";
import { useConflictAlerts } from "~/hooks/use-conflict-alerts";
import { useCrashChallenge } from "~/hooks/use-crash-challenge";
import { useSimState, type SimSource } from "~/hooks/use-sim-state";
import { cn } from "~/lib/utils";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Hold Short · EWR Ground" }];
}

const legend = [
  { label: "Runway", swatch: <span className="h-1.5 w-5 rounded-sm bg-runway/60" /> },
  { label: "Taxiway", swatch: <span className="h-0.5 w-5 bg-taxiway" /> },
  { label: "Runway crossing", swatch: <span className="size-2.5 rounded-full bg-destructive" /> },
  { label: "Hold-short point (zoom in)", swatch: <span className="size-2.5 rounded-full bg-taxiway" /> },
  { label: "Intersection (zoom in more)", swatch: <span className="size-2 rounded-full bg-foreground" /> },
  { label: "Aircraft · taxiing", swatch: <span className="size-2.5 rotate-45 bg-beacon" /> },
  { label: "Conflict", swatch: <span className="size-2.5 animate-pulse rounded-full border-2 border-destructive" /> },
];

const sourceLabel: Record<string, { text: string; dot: string }> = {
  connecting: { text: "Connecting…", dot: "bg-muted-foreground" },
  live: { text: "Live", dot: "bg-beacon" },
  offline: { text: "Demo traffic · backend offline", dot: "bg-taxiway" },
  unmapped: { text: "Demo traffic · backend IDs not on map", dot: "bg-taxiway" },
};

const sourceKey = (s: SimSource) => (s.mode === "demo" ? s.reason : s.mode);

export default function Sim() {
  const { state, source } = useSimState();
  const challenge = useCrashChallenge(state);
  const live = source.mode === "live";
  const conflictAlerts = useConflictAlerts(state, live);
  const label = sourceLabel[sourceKey(source)];

  return (
    <main className="relative h-svh overflow-hidden bg-background">
      <GroundChart className="absolute inset-0" state={state} live={live} />
      <ConflictAlerts alerts={conflictAlerts} live={live} />

      <header className="pointer-events-none absolute inset-x-0 top-0 flex flex-wrap items-center gap-2 p-4 pr-16 sm:gap-3">
        <Button asChild variant="outline" size="icon" className="pointer-events-auto bg-scrim backdrop-blur">
          <Link to="/" aria-label="Back to start">
            <ArrowLeft />
          </Link>
        </Button>
        <Badge
          variant="outline"
          className="gap-2 bg-scrim px-3 py-1 font-mono tracking-widest text-muted-foreground uppercase backdrop-blur"
        >
          <span className="size-2 rounded-full bg-beacon" />
          KEWR · Ground
        </Badge>
        <Badge
          variant="outline"
          className="gap-2 bg-scrim px-3 py-1 font-mono tracking-wider text-muted-foreground uppercase backdrop-blur"
        >
          <span className={cn("size-2 rounded-full", label.dot, source.mode === "live" && "animate-pulse")} />
          {label.text}
        </Badge>
        <Button
          variant="outline"
          className="pointer-events-auto ml-auto gap-2 border-destructive/50 bg-scrim px-3 backdrop-blur"
          disabled={!live || challenge.phase !== "idle"}
          title={live ? "Try to cause a runway incursion" : "Needs the live backend"}
          onClick={challenge.open}
        >
          <Flame className="text-destructive" />
          <span className="hidden sm:inline">Crash challenge</span>
        </Button>
        <Button
          asChild
          variant="outline"
          className="pointer-events-auto gap-2 bg-scrim px-3 backdrop-blur"
        >
          <Link to="/drowsiness">
            <Eye />
            <span className="hidden sm:inline">Drowsiness monitor</span>
            <span className="sm:hidden">Monitor</span>
          </Link>
        </Button>
      </header>

      <DrowsinessMonitorDock />

      <CrashChallenge challenge={challenge} />

      <Card className="pointer-events-none absolute bottom-4 left-4 gap-0 border-border bg-scrim py-3 backdrop-blur-md">
        <CardContent className="flex flex-col gap-2 px-4">
          <p className="font-mono text-[10px] tracking-[0.2em] text-muted-foreground uppercase">
            Scroll to zoom · drag to move · click to pin
          </p>
          {legend.map(({ label, swatch }) => (
            <div key={label} className="flex items-center gap-3 text-xs text-foreground">
              <span className="flex w-5 justify-center">{swatch}</span>
              {label}
            </div>
          ))}
        </CardContent>
      </Card>

      <p className="pointer-events-none absolute right-3 bottom-2 text-[10px] text-muted-foreground">
        Imagery: USGS · Map data © OpenStreetMap contributors
      </p>

      {/* Covers the map while the chart image and first traffic update load behind it */}
      <LoadingScreen stateReady={source.mode !== "connecting"} />
    </main>
  );
}
