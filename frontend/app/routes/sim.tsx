import type { Route } from "./+types/sim";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Hold Short · EWR Ground" }];
}

export default function Sim() {
  return (
    <main className="relative h-svh overflow-hidden bg-background">
      <img
        src="/ewr-backdrop.png"
        alt="Night satellite view of Newark Liberty International Airport"
        className="size-full object-cover"
      />
    </main>
  );
}
