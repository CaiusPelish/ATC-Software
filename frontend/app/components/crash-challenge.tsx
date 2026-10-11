import { CheckCircle2, Flame, Hand, LogOut, RotateCcw, TriangleAlert } from "lucide-react";

import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { RUNWAY, type useCrashChallenge } from "~/hooks/use-crash-challenge";
import { cn } from "~/lib/utils";

type Challenge = ReturnType<typeof useCrashChallenge>;

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// Stops map clicks/drags from firing through the overlay.
const swallow = {
  onPointerDown: (e: React.PointerEvent) => e.stopPropagation(),
  onPointerUp: (e: React.PointerEvent) => e.stopPropagation(),
};

/** Intro, live HUD and result banner for the judge's "try to cause a crash" demo. */
export function CrashChallenge({ challenge }: { challenge: Challenge }) {
  const { phase, caught, busy, touchdownIn, closesIn, runwayClosed } = challenge;
  if (phase === "idle") return null;

  if (phase === "intro") {
    return (
      <div className="absolute inset-0 z-30 grid place-items-center bg-scrim px-4 backdrop-blur-sm" {...swallow}>
        <Card className="w-full max-w-md gap-4 border-border bg-card/95 py-5">
          <CardHeader className="gap-2 px-5">
            <p className="flex items-center gap-2 font-mono text-[10px] tracking-[0.2em] text-destructive uppercase">
              <Flame className="size-3.5" /> Crash challenge
            </p>
            <CardTitle className="text-xl">Try to cause a runway incursion</CardTitle>
            <CardDescription className="text-sm">
              A plane lands on runway {RUNWAY} in 2 minutes. Drag fire truck TRUCK01 onto the runway while it's closed
              for the landing and see if Hold Short catches it before touchdown.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2 px-5">
            <Button onClick={() => void challenge.start()} disabled={busy} className="flex-1">
              {busy ? "Setting up…" : "Start"}
            </Button>
            <Button variant="outline" onClick={challenge.cancel} disabled={busy}>
              Cancel
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div
      className="pointer-events-none absolute bottom-4 left-1/2 z-20 flex w-[min(34rem,calc(100%-2rem))] -translate-x-1/2 flex-col-reverse gap-2"
      data-challenge={phase}
    >
      {/* Live HUD: always visible while the challenge runs */}
      <Card className="gap-0 border-border bg-scrim py-3 backdrop-blur-md">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 px-4">
          <div className="font-mono text-xs tracking-widest text-muted-foreground uppercase">
            Arrival · runway {RUNWAY}
            <span className="ml-2 text-base font-semibold text-foreground tabular-nums" data-touchdown={touchdownIn.toFixed(0)}>
              {touchdownIn > 0 ? `touchdown in ${clock(touchdownIn)}` : "landed"}
            </span>
          </div>
          <Badge
            variant="outline"
            className={cn(
              "gap-2 font-mono tracking-wider uppercase",
              runwayClosed ? "border-destructive/60 text-destructive" : "border-beacon/50 text-foreground",
            )}
            data-runway={runwayClosed ? "closed" : "open"}
          >
            <span className={cn("size-2 rounded-full", runwayClosed ? "animate-pulse bg-destructive" : "bg-beacon")} />
            {runwayClosed ? "Runway closed · landing" : `Runway open · closes in ${clock(closesIn)}`}
          </Badge>
        </CardContent>
        {(phase === "starting" || phase === "running") && (
          <p className="px-4 pt-2 text-xs text-muted-foreground">
            Drag <span className="font-mono text-foreground">TRUCK01</span> onto runway {RUNWAY}.{" "}
            {runwayClosed ? "Now is when it counts." : "It only counts once the runway closes."}
          </p>
        )}
      </Card>

      {phase === "caught" && caught && (
        <Card
          className="pointer-events-auto gap-0 border-destructive bg-card py-4 ring-2 ring-destructive/60"
          data-result="caught"
          {...swallow}
        >
          <CardHeader className="gap-1.5 px-4">
            <p className="flex items-center gap-2 font-mono text-[10px] tracking-[0.2em] text-destructive uppercase motion-safe:animate-pulse">
              <TriangleAlert className="size-3.5" />{" "}
              {caught.kind === "runway" ? "Runway incursion caught" : "Conflict in path caught"}
            </p>
            <CardTitle className="text-lg">
              {caught.kind === "runway"
                ? `${caught.intruder} is on runway ${RUNWAY} with ${caught.other} landing`
                : `${caught.intruder} came within 20 m of ${caught.other} on ${caught.where}`}
            </CardTitle>
            <CardDescription className="text-sm">
              {caught.kind === "runway" ? (
                <>
                  Caught <span className="font-semibold text-foreground">{caught.secondsBefore} s</span> before touchdown.
                </>
              ) : (
                "Caught before they collided."
              )}
            </CardDescription>
            <p className="mt-1 rounded-md border border-border px-3 py-2 font-mono text-sm text-foreground">
              ATC: &ldquo;
              {caught.kind === "runway"
                ? `${caught.intruder}, hold short of runway ${RUNWAY}.`
                : `${caught.intruder}, hold position.`}
              &rdquo;
            </p>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 px-4 pt-3">
            <Button variant="destructive" onClick={() => void challenge.holdShort()}>
              <Hand /> Issue hold short
            </Button>
            <Button variant="outline" onClick={() => void challenge.retry()} disabled={busy}>
              <RotateCcw /> Try again
            </Button>
            <Button variant="ghost" onClick={() => void challenge.exit()}>
              <LogOut /> Exit
            </Button>
          </CardContent>
        </Card>
      )}

      {phase === "clear" && (
        <Card className="pointer-events-auto gap-0 border-beacon/50 bg-card/95 py-4" data-result="clear" {...swallow}>
          <CardHeader className="gap-1.5 px-4">
            <p className="flex items-center gap-2 font-mono text-[10px] tracking-[0.2em] text-beacon uppercase">
              <CheckCircle2 className="size-3.5" /> Runway stayed clear
            </p>
            <CardTitle className="text-lg">The arrival landed safely</CardTitle>
            <CardDescription>No vehicle entered runway {RUNWAY} while it was closed.</CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2 px-4 pt-3">
            <Button onClick={() => void challenge.retry()} disabled={busy}>
              <RotateCcw /> Try again
            </Button>
            <Button variant="ghost" onClick={() => void challenge.exit()}>
              <LogOut /> Exit
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
