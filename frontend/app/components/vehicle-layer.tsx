import { memo, useEffect, useRef } from "react";
import { Plane, Truck } from "lucide-react";

import { routePoint } from "~/lib/chart";
import type { VehicleInfo } from "~/lib/sim-state";
import { cn } from "~/lib/utils";

type Pose = { x: number; y: number; heading: number };

const FOLLOW_RATE = 6; // higher = icons catch up to new positions faster
const TELEPORT_PX = 250; // bigger jumps (route restart, new vehicle) snap instead of sliding

// lucide's Plane icon points to the top-right, so rotate it back to "up" before applying heading.
const PLANE_ICON_OFFSET = -45;

function targetOf(v: VehicleInfo): Pose | null {
  const p = routePoint(v.route, v.progress, v.position);
  return p ? { x: p.x, y: p.y, heading: p.heading ?? 0 } : null;
}

function shortestTurn(from: number, to: number) {
  return ((to - from + 540) % 360) - 180;
}

function statusColor(v: VehicleInfo) {
  if (v.inConflict) return "text-destructive";
  if (v.kind === "truck") return "text-foreground";
  if (v.status === "taxiing") return "text-beacon";
  if (v.status === "holding") return "text-taxiway";
  return "text-muted-foreground";
}

export const VehicleLayer = memo(function VehicleLayer({
  vehicles,
  activeId,
}: {
  vehicles: VehicleInfo[];
  activeId: string | null;
}) {
  const groups = useRef(new Map<string, SVGGElement>());
  const icons = useRef(new Map<string, SVGGElement>());
  const poses = useRef(new Map<string, Pose>());
  const targets = useRef(new Map<string, Pose>());

  // New poll data only updates targets; the animation loop does the moving.
  useEffect(() => {
    const next = new Map<string, Pose>();
    for (const v of vehicles) {
      const t = targetOf(v);
      if (t) next.set(v.id, t);
    }
    targets.current = next;
  }, [vehicles]);

  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const k = 1 - Math.exp(-((now - last) / 1000) * FOLLOW_RATE);
      last = now;
      for (const [id, target] of targets.current) {
        const prev = poses.current.get(id);
        const snap = !prev || Math.hypot(target.x - prev.x, target.y - prev.y) > TELEPORT_PX;
        const pose = snap
          ? target
          : {
              x: prev.x + (target.x - prev.x) * k,
              y: prev.y + (target.y - prev.y) * k,
              heading: prev.heading + shortestTurn(prev.heading, target.heading) * k,
            };
        poses.current.set(id, pose);
        const g = groups.current.get(id);
        if (g) g.style.transform = `translate(${pose.x}px, ${pose.y}px) scale(calc(1 / var(--z)))`;
        const icon = icons.current.get(id);
        if (icon) icon.style.transform = `rotate(${pose.heading}deg)`;
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <g>
      {vehicles.map((v) => {
        const active = activeId === v.id;
        return (
          <g
            key={v.id}
            data-kind="vehicle"
            data-id={v.id}
            className="cursor-pointer"
            ref={(el) => {
              if (el) groups.current.set(v.id, el);
              else groups.current.delete(v.id);
            }}
          >
            <circle r={46} className="fill-transparent" />
            {(active || v.inConflict) && (
              <circle
                r={40}
                className={cn(
                  "fill-none",
                  v.inConflict ? "animate-pulse stroke-destructive" : "stroke-beacon",
                )}
                strokeWidth={3}
              />
            )}
            <g
              className={statusColor(v)}
              ref={(el) => {
                if (el) icons.current.set(v.id, el);
                else icons.current.delete(v.id);
              }}
            >
              {v.kind === "plane" ? (
                <g transform={`rotate(${PLANE_ICON_OFFSET})`}>
                  <Plane x={-28} y={-28} width={56} height={56} fill="currentColor" className="stroke-background" strokeWidth={1} />
                </g>
              ) : (
                <g transform="rotate(-90)">
                  <Truck x={-22} y={-22} width={44} height={44} className="fill-background" strokeWidth={2.5} />
                </g>
              )}
            </g>
            <text
              x={40}
              y={-30}
              className={cn(
                "pointer-events-none fill-foreground font-mono text-[30px] font-semibold",
                "[paint-order:stroke] stroke-background [stroke-linejoin:round] [stroke-width:8px]",
              )}
            >
              {v.label}
            </text>
          </g>
        );
      })}
    </g>
  );
});
