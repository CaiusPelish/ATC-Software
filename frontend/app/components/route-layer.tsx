import { memo } from "react";
import { Plane } from "lucide-react";

import { chart, type Point } from "~/lib/chart";
import { cn } from "~/lib/utils";

export type DestinationHover = { id: string; x: number; y: number; label: string };

// Marks drawn in screen-ish units: translate to the point, then undo the map zoom.
const pinned = (x: number, y: number) => ({ transform: `translate(${x}px, ${y}px) scale(calc(1 / var(--z)))` });

/**
 * The selected vehicle's route still ahead of it (dashed, ends in a flag), plus the
 * destination under the cursor while the controller is picking where to send it.
 */
export const RouteLayer = memo(function RouteLayer({
  remaining,
  pickHover,
  pending,
  ghost = null,
}: {
  remaining: Point[] | null;
  pickHover: DestinationHover | null;
  pending: boolean;
  /** While a truck is being dragged: from the truck to the cursor. */
  ghost?: [Point, Point] | null;
}) {
  const end = remaining && remaining.length > 1 ? remaining[remaining.length - 1] : null;
  const d = remaining && remaining.length > 1 ? "M" + remaining.map(([x, y]) => `${x} ${y}`).join("L") : null;

  return (
    <g className="pointer-events-none">
      {d && (
        <g data-route="selected">
          {/* Dark halo under the dashes so the route reads over taxiways and imagery */}
          <path d={d} fill="none" strokeWidth={7} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" className="stroke-background opacity-70" />
          <path
            d={d}
            fill="none"
            strokeWidth={3.5}
            strokeDasharray="10 7"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
            className="stroke-beacon"
          />
        </g>
      )}

      {end && (
        <g style={pinned(end[0], end[1])} data-route-end="">
          <circle r={14} className="fill-beacon/25 stroke-beacon" strokeWidth={2} />
          <circle r={5} className="fill-beacon" />
        </g>
      )}

      {ghost && (
        <line
          x1={ghost[0][0]} y1={ghost[0][1]} x2={ghost[1][0]} y2={ghost[1][1]}
          strokeWidth={2.5}
          strokeDasharray="6 6"
          vectorEffect="non-scaling-stroke"
          className="stroke-taxiway"
          data-drag-ghost=""
        />
      )}

      {pickHover && (
        <g style={pinned(pickHover.x, pickHover.y)} data-pick-target={pickHover.id}>
          <circle r={18} className={cn("fill-none stroke-taxiway", pending && "animate-pulse")} strokeWidth={2.5} strokeDasharray="5 4" />
          <circle r={4} className="fill-taxiway" />
          <text
            x={26}
            y={6}
            className="fill-foreground font-mono text-[22px] font-semibold [paint-order:stroke] stroke-background [stroke-linejoin:round] [stroke-width:7px]"
          >
            {pending ? "Planning route…" : pickHover.label}
          </text>
        </g>
      )}
    </g>
  );
});

export type PredictedMeeting = { key: string; location: string; eta?: number };

/** Where the backend predicts a vehicle will meet traffic: a pulsing amber ring + countdown. */
export const WarningLayer = memo(function WarningLayer({ warnings }: { warnings: PredictedMeeting[] }) {
  return (
    <g className="pointer-events-none">
      {warnings.map((w) => {
        const p = chart.positions[w.location];
        if (!p) return null;
        return (
          <g key={w.key} style={pinned(p[0], p[1])} data-predicted={w.location}>
            <circle r={26} className="fill-taxiway/15 stroke-taxiway motion-safe:animate-pulse" strokeWidth={3} />
            <circle r={6} className="fill-taxiway" />
            {w.eta !== undefined && (
              <text
                x={34}
                y={-18}
                className="fill-taxiway font-mono text-[24px] font-bold [paint-order:stroke] stroke-background [stroke-linejoin:round] [stroke-width:7px]"
              >
                {`${Math.max(1, Math.round(w.eta))} s`}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
});

type InboundPlane = {
  x: number;
  y: number;
  heading: number;
  label: string;
  closed: boolean;
  threshold: [number, number];
};

/** The landing arrival on final approach (crash challenge), drawn before the backend has it. */
export const InboundLayer = memo(function InboundLayer({ inbound }: { inbound: InboundPlane | null }) {
  if (!inbound) return null;
  const [tx, ty] = inbound.threshold;
  const tone = inbound.closed ? "destructive" : "foreground";
  return (
    <g className="pointer-events-none" data-inbound={inbound.closed ? "closed" : "open"}>
      <line
        x1={inbound.x} y1={inbound.y} x2={tx} y2={ty}
        strokeWidth={2}
        strokeDasharray="4 10"
        vectorEffect="non-scaling-stroke"
        className={inbound.closed ? "stroke-destructive/70" : "stroke-foreground/40"}
      />
      {/* Glides between 500 ms polls instead of jumping ~36 m each time */}
      <g style={{ ...pinned(inbound.x, inbound.y), transition: "transform 500ms linear" }} data-inbound-x={inbound.x.toFixed(0)} data-inbound-y={inbound.y.toFixed(0)}>
        <circle r={48} className={cn("fill-none", inbound.closed ? "stroke-destructive motion-safe:animate-pulse" : "stroke-foreground/50")} strokeWidth={2.5} />
        <g style={{ transform: `rotate(${inbound.heading - 45}deg)` }}>
          <Plane x={-34} y={-34} width={68} height={68} fill="currentColor" strokeWidth={1} className={cn(tone === "destructive" ? "text-destructive" : "text-foreground", "stroke-background")} />
        </g>
        <text
          x={58}
          y={-34}
          className={cn(
            "font-mono text-[32px] font-bold [paint-order:stroke] stroke-background [stroke-linejoin:round] [stroke-width:7px]",
            inbound.closed ? "fill-destructive" : "fill-foreground",
          )}
        >
          {inbound.label}
        </text>
      </g>
    </g>
  );
});
