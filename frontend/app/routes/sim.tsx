import { Link } from "react-router";
import { ArrowLeft } from "lucide-react";

import type { Route } from "./+types/sim";
import { GroundChart } from "~/components/ground-chart";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Hold Short · EWR Ground" }];
}

const legend = [
  { label: "Runway", swatch: <span className="h-1.5 w-5 rounded-sm bg-runway/60" /> },
  { label: "Taxiway", swatch: <span className="h-0.5 w-5 bg-taxiway" /> },
  { label: "Hold-short point (zoom in)", swatch: <span className="size-2.5 rounded-full bg-taxiway" /> },
  { label: "Intersection (zoom in more)", swatch: <span className="size-2 rounded-full bg-foreground" /> },
];

export default function Sim() {
  return (
    <main className="relative h-svh overflow-hidden bg-background">
      <GroundChart className="absolute inset-0" />

      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-3 p-4">
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
      </header>

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
    </main>
  );
}
