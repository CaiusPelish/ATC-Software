import { useEffect } from "react";
import type { Route } from "./+types/app";
import { Link, useFetcher, useRevalidator } from "react-router";
import { AlertCircle, Plane, RotateCcw, Truck } from "lucide-react";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";

type Aircraft = {
  id: string;
  callsign: string;
  position: string;
  destination: string;
  route: string[];
  speed: number;
  progress: number;
  status: "waiting" | "taxiing" | "holding" | "arrived";
};

type Vehicle = {
  id: string;
  position: string;
};

type Conflict = {
  aircraft_ids: string[];
  location: string;
  severity: "warning" | "critical";
};

type SimulationState = {
  time: number;
  running: boolean;
  aircraft: Aircraft[];
  vehicles: Vehicle[];
  conflicts: Conflict[];
  score: number;
};

const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function requestBackend(path: string, init?: RequestInit) {
  const response = await fetch(`${backendUrl}${path}`, init);
  if (!response.ok) {
    const body = (await response.json()) as { detail?: string };
    throw new Response(body.detail ?? "The backend request failed.", {
      status: response.status,
    });
  }
  return response;
}

export async function loader() {
  const response = await requestBackend("/api/state");
  return (await response.json()) as SimulationState;
}

export async function action({ request }: Route.ActionArgs) {
  const formData = await request.formData();
  const intent = formData.get("intent");

  if (intent === "reset") {
    await requestBackend("/api/reset", { method: "POST" });
    return null;
  }

  if (intent === "load-challenge") {
    await requestBackend("/api/scenario/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: "challenge_01" }),
    });
    return null;
  }

  throw new Response("Unknown simulation action.", { status: 400 });
}

export function meta() {
  return [
    { title: "ATC Simulation | Hold Short" },
    {
      name: "description",
      content: "Live airport taxiway simulation state and traffic.",
    },
  ];
}

export default function Simulation({ loaderData }: Route.ComponentProps) {
  const state = loaderData;
  const fetcher = useFetcher();
  const revalidator = useRevalidator();
  const isSubmitting = fetcher.state !== "idle";

  useEffect(() => {
    const timer = window.setInterval(() => {
      revalidator.revalidate();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [revalidator]);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-8 px-4 py-10 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <Link
            className="text-sm text-muted-foreground hover:text-foreground"
            to="/"
          >
            ← Home
          </Link>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight">
            ATC Simulation
          </h1>
          <p className="mt-1 text-muted-foreground">
            Airport surface traffic overview
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <fetcher.Form method="post">
            <input type="hidden" name="intent" value="load-challenge" />
            <Button disabled={isSubmitting} type="submit" variant="outline">
              Load challenge
            </Button>
          </fetcher.Form>
          <fetcher.Form method="post">
            <input type="hidden" name="intent" value="reset" />
            <Button disabled={isSubmitting} type="submit">
              <RotateCcw />
              Reset
            </Button>
          </fetcher.Form>
        </div>
      </header>

      <section
        aria-label="Simulation summary"
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        <SummaryCard label="Simulation time" value={`${state.time.toFixed(0)} s`} />
        <SummaryCard label="Aircraft" value={String(state.aircraft.length)} />
        <SummaryCard label="Ground vehicles" value={String(state.vehicles.length)} />
        <SummaryCard label="Score" value={String(state.score)} />
      </section>

      {state.conflicts.length > 0 && (
        <section aria-label="Traffic conflicts" className="grid gap-3">
          {state.conflicts.map((conflict) => (
            <div
              className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4"
              key={`${conflict.location}-${conflict.aircraft_ids.join("-")}`}
            >
              <AlertCircle className="mt-0.5 size-5 text-destructive" />
              <div>
                <p className="font-medium capitalize">
                  {conflict.severity} conflict at {conflict.location}
                </p>
                <p className="text-sm text-muted-foreground">
                  Aircraft: {conflict.aircraft_ids.join(", ")}
                </p>
              </div>
            </div>
          ))}
        </section>
      )}

      <section className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Plane className="size-5" />
              Aircraft
            </CardTitle>
          </CardHeader>
          <CardContent>
            {state.aircraft.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No aircraft in this scenario.
              </p>
            ) : (
              <ul className="divide-y">
                {state.aircraft.map((aircraft) => (
                  <li className="flex flex-wrap justify-between gap-2 py-4 first:pt-0 last:pb-0" key={aircraft.id}>
                    <div>
                      <p className="font-medium">{aircraft.callsign}</p>
                      <p className="text-sm text-muted-foreground">
                        {aircraft.position} → {aircraft.destination}
                      </p>
                    </div>
                    <span className="self-center rounded-full bg-secondary px-3 py-1 text-xs capitalize">
                      {aircraft.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Truck className="size-5" />
              Ground vehicles
            </CardTitle>
          </CardHeader>
          <CardContent>
            {state.vehicles.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No ground vehicles in this scenario.
              </p>
            ) : (
              <ul className="divide-y">
                {state.vehicles.map((vehicle) => (
                  <li className="flex justify-between gap-2 py-4 first:pt-0 last:pb-0" key={vehicle.id}>
                    <span className="font-medium">{vehicle.id}</span>
                    <span className="text-sm text-muted-foreground">
                      {vehicle.position}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </section>
    </main>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <Card className="gap-3 py-5">
      <CardHeader className="pb-0">
        <p className="text-sm text-muted-foreground">{label}</p>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold">{value}</p>
      </CardContent>
    </Card>
  );
}
