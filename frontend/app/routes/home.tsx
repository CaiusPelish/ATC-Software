import { Link } from "react-router";
import { Play } from "lucide-react";

import type { Route } from "./+types/home";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "~/components/ui/card";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Hold Short" },
    {
      name: "description",
      content: "Ground control simulator for Newark Liberty International Airport.",
    },
  ];
}

// The real runway "hold short" paint: two solid lines on the taxiway side,
// two dashed lines on the runway side. Planes must stop before crossing it.
function HoldShortMarking() {
  return (
    <div aria-hidden className="flex w-64 max-w-full flex-col gap-1">
      <div className="h-0.5 bg-taxiway" />
      <div className="h-0.5 bg-taxiway" />
      <div className="h-0.5 bg-[repeating-linear-gradient(90deg,var(--taxiway)_0_14px,transparent_14px_24px)]" />
      <div className="h-0.5 bg-[repeating-linear-gradient(90deg,var(--taxiway)_0_14px,transparent_14px_24px)]" />
    </div>
  );
}

export default function Home() {
  return (
    <main className="relative grid min-h-svh place-items-center overflow-hidden bg-background px-4 py-20">
      <img
        src="/ewr-backdrop.png"
        alt=""
        className="absolute inset-0 size-full scale-105 object-cover blur-sm"
      />
      <div className="absolute inset-0 bg-scrim" />
      <div className="absolute inset-0 bg-radial from-transparent to-background" />

      <Badge
        variant="outline"
        className="absolute top-6 left-6 gap-2 bg-scrim px-3 py-1 font-mono tracking-widest text-muted-foreground uppercase backdrop-blur"
      >
        <span className="size-2 rounded-full bg-beacon" />
        KEWR · Newark Liberty Intl
      </Badge>

      <Card className="relative w-full max-w-xl gap-8 border-border bg-scrim text-center backdrop-blur-md">
        <CardHeader className="justify-items-center gap-4">
          <Badge
            variant="outline"
            className="border-beacon/40 font-mono tracking-[0.3em] text-beacon uppercase"
          >
            Ground Control Simulator
          </Badge>
          <h1 className="text-5xl font-semibold tracking-tight text-foreground sm:text-7xl">
            Hold Short
          </h1>
        </CardHeader>

        <CardContent className="flex flex-col items-center gap-6">
          <div className="flex flex-col items-center gap-2">
            <HoldShortMarking />
            <p className="text-xs text-muted-foreground">
              The painted line where planes stop before crossing a runway.
            </p>
          </div>

          <p className="text-base text-pretty text-muted-foreground sm:text-lg">
            Guide planes and ground vehicles around Newark Airport. The system
            watches for collisions and warns you out loud before they happen.
          </p>
        </CardContent>

        <CardFooter className="justify-center">
          <Button asChild size="lg" className="font-mono tracking-widest uppercase">
            <Link to="/sim">
              <Play className="fill-current" />
              Start Simulation
            </Link>
          </Button>
        </CardFooter>
      </Card>
    </main>
  );
}
