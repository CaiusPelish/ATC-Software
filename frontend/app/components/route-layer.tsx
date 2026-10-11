import { memo } from "react";

import type { Point } from "~/lib/chart";
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
