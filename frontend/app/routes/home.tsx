import { Link } from "react-router";
import { BellRing, Eye, Play, Route as RouteIcon } from "lucide-react";

import type { Route } from "./+types/home";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
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

const features = [
  {
    icon: RouteIcon,
    title: "Route traffic",
    text: "Click a plane, click where it needs to go, and the fastest taxi route is drawn for you.",
  },
  {
    icon: BellRing,
    title: "Catch conflicts",
    text: "If two vehicles are about to share a taxiway or cross an active runway, you hear about it.",
  },
  {
    icon: Eye,
    title: "Stay sharp",
    text: "The webcam watches for fatigue. The more tired you look, the louder the warnings get.",
  },
];

// The real runway "hold short" paint: two solid lines on the taxiway side,
// two dashed lines on the runway side. Planes must stop before crossing it.
function HoldShortMarking() {
  return (
    <div aria-hidden className="flex w-40 max-w-full flex-col gap-1">
      <div className="h-0.5 bg-taxiway" />
      <div className="h-0.5 bg-taxiway" />
      <div className="h-0.5 bg-[repeating-linear-gradient(90deg,var(--taxiway)_0_14px,transparent_14px_24px)]" />
      <div className="h-0.5 bg-[repeating-linear-gradient(90deg,var(--taxiway)_0_14px,transparent_14px_24px)]" />
    </div>
  );
}

export default function Home() {
  return (
    <main className="relative flex min-h-svh flex-col overflow-hidden bg-background">
      <img
        src="/ewr-backdrop.png"
        alt=""
        className="absolute inset-0 size-full object-cover"
      />
      {/* Dark behind the text on the left and along the bottom, clear over the airport */}
      <div className="absolute inset-0 bg-linear-to-r from-background via-scrim to-transparent" />
      <div className="absolute inset-0 bg-linear-to-t from-background to-transparent to-50%" />

      <header className="relative flex items-center justify-between gap-4 px-4 pt-6 sm:px-12 lg:px-20">
        <Badge
          variant="outline"
          className="gap-2 bg-scrim px-3 py-1 font-mono tracking-widest text-muted-foreground uppercase backdrop-blur"
        >
          <span className="size-2 rounded-full bg-beacon" />
          KEWR · Newark Liberty Intl
        </Badge>
        <Badge
          variant="outline"
          className="hidden border-beacon/40 bg-scrim px-3 py-1 font-mono tracking-[0.3em] text-beacon uppercase backdrop-blur sm:inline-flex"
        >
          Ground Control Simulator
        </Badge>
      </header>

      <section className="relative flex flex-1 flex-col justify-center px-4 py-16 sm:px-12 lg:px-20">
        <div className="flex max-w-xl flex-col gap-10">
          <h1 className="text-6xl font-semibold tracking-tight text-foreground sm:text-8xl">
            Hold Short
          </h1>

          <div className="flex flex-col gap-3">
            <HoldShortMarking />
            <p className="font-mono text-xs tracking-wide text-taxiway">
              hold short: the painted line where planes stop before a runway
            </p>
          </div>

          <p className="text-lg text-pretty text-muted-foreground sm:text-xl">
            Guide planes and ground vehicles around Newark Airport. The system
            watches for collisions and warns you out loud before they happen.
          </p>

          <div className="flex flex-wrap gap-3">
            <Button
              asChild
              size="lg"
              className="w-fit font-mono tracking-widest uppercase"
            >
              <Link to="/sim">
                <Play className="fill-current" />
                Start flight simulation
              </Link>
            </Button>
            <Button
              asChild
              size="lg"
              variant="outline"
              className="font-mono tracking-widest uppercase"
            >
              <Link to="/drowsiness">
                <Eye />
                Drowsiness monitor
              </Link>
            </Button>
          </div>
        </div>
      </section>

      <footer className="relative grid gap-4 px-4 pb-8 sm:px-12 md:grid-cols-3 lg:px-20 lg:pb-12">
        {features.map(({ icon: Icon, title, text }) => (
          <Card
            key={title}
            className="gap-0 border-border bg-scrim py-5 backdrop-blur-md"
          >
            <CardHeader className="gap-2 px-5">
              <CardTitle className="flex items-center gap-2 text-foreground">
                <Icon className="size-4 text-beacon" />
                {title}
              </CardTitle>
              <CardDescription>{text}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </footer>
    </main>
  );
}
