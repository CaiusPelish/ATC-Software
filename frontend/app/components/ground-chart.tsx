import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize, Minus, Plus, X } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { VehicleLayer } from "~/components/vehicle-layer";
import { chart, describe, type HoverTarget, type Point } from "~/lib/chart";
import { vehiclesOf, type SimulationState, type VehicleInfo } from "~/lib/sim-state";
import { cn } from "~/lib/utils";

const { width, height } = chart.projection;
const MIN_ZOOM = 1;
const MAX_ZOOM = 10;
const DRAG_THRESHOLD_PX = 4;

const runwaysById = new Map(chart.runways.map((r) => [r.id, r]));
const taxiwaysById = new Map(chart.taxiways.map((t) => [t.id, t]));
const nodesById = new Map(chart.nodes.map((n) => [n.id, n]));

type View = { x: number; y: number; w: number; h: number };
const FULL_VIEW: View = { x: 0, y: 0, w: width, h: height };

/** What gets drawn at each zoom level, so the zoomed-out chart stays readable. */
function zoomTier(zoom: number) {
  if (zoom < 1.8) return 0; // runways + taxiway lines only
  if (zoom < 3.5) return 1; // + taxiway letters + hold-short points
  return 2; // + intersections and taxiway ends
}

function lookup(el: Element | null, vehicles: Map<string, VehicleInfo>): HoverTarget | null {
  const hit = el?.closest<SVGElement>("[data-kind]");
  const id = hit?.dataset.id;
  if (!hit || !id) return null;
  switch (hit.dataset.kind) {
    case "runway": {
      const item = runwaysById.get(id);
      return item ? { kind: "runway", item } : null;
    }
    case "taxiway": {
      const item = taxiwaysById.get(id);
      return item ? { kind: "taxiway", item } : null;
    }
    case "node": {
      const item = nodesById.get(id);
      return item ? { kind: "node", item } : null;
    }
    case "vehicle": {
      const item = vehicles.get(id);
      return item ? { kind: "vehicle", item } : null;
    }
  }
  return null;
}

const keyOf = (t: HoverTarget | null) => (t ? `${t.kind}:${t.item.id}` : null);

function clampView(v: View): View {
  const w = Math.min(Math.max(v.w, width / MAX_ZOOM), width);
  const h = (w / width) * height;
  return {
    w,
    h,
    x: Math.min(Math.max(v.x, 0), width - w),
    y: Math.min(Math.max(v.y, 0), height - h),
  };
}

/** Screen pixel -> chart units, matching preserveAspectRatio="xMidYMid slice". */
function toChart(view: View, box: DOMRect, clientX: number, clientY: number) {
  const scale = Math.max(box.width / view.w, box.height / view.h);
  const offsetX = (box.width - view.w * scale) / 2;
  const offsetY = (box.height - view.h * scale) / 2;
  return {
    x: view.x + (clientX - box.left - offsetX) / scale,
    y: view.y + (clientY - box.top - offsetY) / scale,
    scale,
  };
}

function zoomAround(view: View, cx: number, cy: number, factor: number): View {
  const w = view.w / factor;
  const k = w / view.w;
  return clampView({
    x: cx - (cx - view.x) * k,
    y: cy - (cy - view.y) * k,
    w,
    h: view.h * k,
  });
}

// Push a runway end label a little past the threshold so it sits off the pavement.
function labelPosition(at: Point, toward: Point, offset = 48): Point {
  const dx = at[0] - toward[0];
  const dy = at[1] - toward[1];
  const len = Math.hypot(dx, dy) || 1;
  return [at[0] + (dx / len) * offset, at[1] + (dy / len) * offset];
}

// Sizes divided by --z (the zoom level) stay the same on screen while the map scales.
const labelHalo = "[paint-order:stroke] stroke-background [stroke-linejoin:round]";

const Runways = memo(function Runways({ activeId, dimmed }: { activeId: string | null; dimmed: boolean }) {
  return (
    <g className={cn("transition-opacity", dimmed && "opacity-30")}>
      {chart.runways.map((r) => {
        const active = activeId === r.id;
        return (
          <g key={r.id} data-kind="runway" data-id={r.id} className="cursor-pointer">
            <line
              x1={r.from[0]} y1={r.from[1]} x2={r.to[0]} y2={r.to[1]}
              strokeWidth={r.widthPx * 1.6}
              className={cn("stroke-runway transition-opacity", active ? "opacity-40" : "opacity-15")}
            />
            <line
              x1={r.from[0]} y1={r.from[1]} x2={r.to[0]} y2={r.to[1]}
              strokeDasharray="6 6"
              vectorEffect="non-scaling-stroke"
              className={cn("stroke-runway transition-opacity", active ? "opacity-100" : "opacity-60")}
            />
            {r.ends.map((end) => {
              const [x, y] = labelPosition(end.at, end.toward);
              return (
                <text
                  key={end.label}
                  x={x} y={y}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className={cn(
                    "fill-runway font-mono font-semibold [font-size:calc(34px/var(--z))] [stroke-width:calc(8px/var(--z))]",
                    labelHalo,
                  )}
                >
                  {end.label}
                </text>
              );
            })}
          </g>
        );
      })}
    </g>
  );
});

const Taxiways = memo(function Taxiways({
  activeId,
  selectedId,
  tier,
}: {
  activeId: string | null;
  selectedId: string | null;
  tier: number;
}) {
  return (
    <g fill="none" strokeLinecap="round">
      {chart.taxiways.map((t) => {
        const active = activeId === t.id || selectedId === t.id;
        const faded = selectedId !== null && !active;
        return (
          <g key={t.id} data-kind="taxiway" data-id={t.id} className="cursor-pointer">
            {/* Wide invisible stroke so thin lines are easy to hover */}
            <path d={t.d} strokeWidth={14} vectorEffect="non-scaling-stroke" className="stroke-transparent" />
            <path
              d={t.d}
              strokeWidth={active ? 4 : t.name ? 2 : 1.25}
              vectorEffect="non-scaling-stroke"
              className={cn(
                "pointer-events-none stroke-taxiway transition-opacity",
                active ? "opacity-100" : faded ? "opacity-15" : t.name ? "opacity-70" : "opacity-35",
              )}
            />
          </g>
        );
      })}
      {/* Letters on top of all lines, like a paper airport diagram */}
      {chart.taxiways.map((t) => {
        const active = activeId === t.id || selectedId === t.id;
        if (!t.name || (tier < 1 && !active) || (selectedId !== null && !active)) return null;
        return (
          <text
            key={t.id}
            x={t.label[0]} y={t.label[1]}
            textAnchor="middle"
            dominantBaseline="middle"
            className={cn(
              "pointer-events-none font-mono font-semibold [font-size:calc(34px/var(--z))] [stroke-width:calc(8px/var(--z))]",
              active ? "fill-foreground" : "fill-taxiway",
              labelHalo,
            )}
          >
            {t.name}
          </text>
        );
      })}
    </g>
  );
});

const Nodes = memo(function Nodes({
  activeId,
  previewTaxiway,
  selectedTaxiway,
  tier,
}: {
  activeId: string | null;
  previewTaxiway: string | null;
  selectedTaxiway: string | null;
  tier: number;
}) {
  return (
    <g>
      {chart.nodes.map((n) => {
        const hold = n.kind === "hold";
        const onFocus = (id: string | null) => id !== null && n.on.includes(id);
        const visible = selectedTaxiway
          ? onFocus(selectedTaxiway) || onFocus(previewTaxiway)
          : onFocus(previewTaxiway) || tier >= 2 || (tier >= 1 && hold);
        if (!visible && activeId !== n.id) return null;
        const active = activeId === n.id;
        return (
          <g key={n.id} data-kind="node" data-id={n.id} className="cursor-pointer">
            <circle cx={n.x} cy={n.y} className="fill-transparent [r:calc(22px/var(--z))]" />
            <circle
              cx={n.x} cy={n.y}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
              className={cn(
                "pointer-events-none stroke-background",
                active
                  ? "fill-beacon [r:calc(16px/var(--z))]"
                  : hold
                    ? "fill-taxiway [r:calc(11px/var(--z))]"
                    : "fill-foreground [r:calc(8px/var(--z))]",
              )}
            />
          </g>
        );
      })}
    </g>
  );
});

export function GroundChart({ className, state }: { className?: string; state: SimulationState | null }) {
  const [view, setView] = useState<View>(FULL_VIEW);
  const [hovered, setHovered] = useState<HoverTarget | null>(null);
  const [selected, setSelected] = useState<HoverTarget | null>(null);
  const [dragging, setDragging] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ startX: number; startY: number; view: View; moved: boolean } | null>(null);

  const vehicles = useMemo(() => (state ? vehiclesOf(state) : []), [state]);
  const vehiclesById = useMemo(() => new Map(vehicles.map((v) => [v.id, v])), [vehicles]);

  const zoom = width / view.w;
  const tier = zoomTier(zoom);
  const hoveredKey = keyOf(hovered);
  const selectedKey = keyOf(selected);

  const selectedTaxiway = selected?.kind === "taxiway" ? selected.item.id : null;
  const hoveredTaxiway = hovered?.kind === "taxiway" ? hovered.item.id : null;
  const activeRunway =
    (hovered?.kind === "runway" && hovered.item.id) || (selected?.kind === "runway" && selected.item.id) || null;
  const activeVehicle =
    (hovered?.kind === "vehicle" && hovered.item.id) || (selected?.kind === "vehicle" && selected.item.id) || null;
  const activeNode =
    (hovered?.kind === "node" && hovered.item.id) || (selected?.kind === "node" && selected.item.id) || null;

  const zoomBy = useCallback((factor: number) => {
    setView((v) => zoomAround(v, v.x + v.w / 2, v.y + v.h / 2, factor));
  }, []);

  const reset = useCallback(() => {
    setView(FULL_VIEW);
    setSelected(null);
  }, []);

  // Wheel zoom needs a non-passive listener so the page itself doesn't zoom or scroll.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      const box = el!.getBoundingClientRect();
      const factor = Math.exp(-e.deltaY * 0.0015);
      setView((v) => {
        const p = toChart(v, box, e.clientX, e.clientY);
        return zoomAround(v, p.x, p.y, factor);
      });
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSelected(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function handlePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    drag.current = { startX: e.clientX, startY: e.clientY, view, moved: false };
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const d = drag.current;

    if (d) {
      const dx = e.clientX - d.startX;
      const dy = e.clientY - d.startY;
      if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) {
        d.moved = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        setDragging(true);
        setHovered(null);
      }
      if (d.moved) {
        const { scale } = toChart(d.view, box, 0, 0);
        setView(clampView({ ...d.view, x: d.view.x - dx / scale, y: d.view.y - dy / scale }));
        return;
      }
    }

    // Move the tooltip directly so pointer moves don't re-render the chart.
    const tip = tooltipRef.current;
    if (tip) {
      const x = e.clientX - box.left;
      const y = e.clientY - box.top;
      const flipX = x > box.width - 300;
      const flipY = y > box.height - 160;
      tip.style.transform = `translate(${flipX ? x - 16 : x + 16}px, ${flipY ? y - 16 : y + 16}px) translate(${flipX ? "-100%" : "0"}, ${flipY ? "-100%" : "0"})`;
    }
    const next = lookup(e.target as Element, vehiclesById);
    if (keyOf(next) !== hoveredKey) setHovered(next);
  }

  function handlePointerUp(e: React.PointerEvent<HTMLDivElement>) {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.moved) {
      setDragging(false);
      return;
    }
    // A click without dragging: select what's under the cursor, or clear on empty map.
    const target = lookup(document.elementFromPoint(e.clientX, e.clientY), vehiclesById);
    setSelected(target && keyOf(target) === selectedKey ? null : target);
  }

  // Vehicles change every poll, so re-read them by id instead of showing a stale snapshot.
  const fresh = (t: HoverTarget | null): HoverTarget | null =>
    t?.kind === "vehicle" ? { kind: "vehicle", item: vehiclesById.get(t.item.id) ?? t.item } : t;
  const tipTarget = fresh(hovered);
  const tip = tipTarget && keyOf(tipTarget) !== selectedKey ? describe(tipTarget) : null;
  const panelTarget = fresh(selected);
  const panel = panelTarget ? describe(panelTarget) : null;
  const selectedNodes = selectedTaxiway ? chart.nodes.filter((n) => n.on.includes(selectedTaxiway)) : [];

  return (
    <div
      ref={containerRef}
      className={cn("relative touch-none overflow-hidden select-none", dragging ? "cursor-grabbing" : "cursor-grab", className)}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerLeave={() => !drag.current && setHovered(null)}
    >
      <svg
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        preserveAspectRatio="xMidYMid slice"
        className="size-full"
        style={{ "--z": zoom } as React.CSSProperties}
        role="img"
        aria-label="Newark Liberty International Airport ground chart"
      >
        <image href="/kewr-chart.jpg" width={width} height={height} />
        <Runways activeId={activeRunway} dimmed={selectedTaxiway !== null} />
        <Taxiways activeId={hoveredTaxiway} selectedId={selectedTaxiway} tier={tier} />
        <Nodes
          activeId={activeNode}
          previewTaxiway={hoveredTaxiway}
          selectedTaxiway={selectedTaxiway}
          tier={tier}
        />
        <VehicleLayer vehicles={vehicles} activeId={activeVehicle} />
      </svg>

      <div ref={tooltipRef} className="pointer-events-none absolute top-0 left-0 z-10">
        {tip && !dragging && (
          <Card className="w-72 gap-0 border-border bg-scrim py-4 backdrop-blur-md">
            <CardHeader className="gap-1.5 px-4">
              <p className="font-mono text-[10px] tracking-[0.2em] text-beacon uppercase">{tip.eyebrow}</p>
              <CardTitle className="font-mono text-base">{tip.title}</CardTitle>
              <CardDescription>{tip.body}</CardDescription>
              <p className="pt-1 text-[11px] text-muted-foreground">Click to pin</p>
            </CardHeader>
          </Card>
        )}
      </div>

      {panel && (
        <Card
          className="absolute top-4 right-16 z-10 w-80 gap-0 border-border bg-scrim py-4 backdrop-blur-md"
          onPointerDown={(e) => e.stopPropagation()}
          onPointerUp={(e) => e.stopPropagation()}
        >
          <CardHeader className="gap-1.5 px-4">
            <div className="flex items-start justify-between gap-2">
              <p className="font-mono text-[10px] tracking-[0.2em] text-beacon uppercase">{panel.eyebrow}</p>
              <Button variant="ghost" size="icon-xs" aria-label="Clear selection" onClick={() => setSelected(null)}>
                <X />
              </Button>
            </div>
            <CardTitle className="font-mono text-lg">{panel.title}</CardTitle>
            <CardDescription>{panel.body}</CardDescription>
            {selectedTaxiway && (
              <p className="pt-2 font-mono text-xs text-muted-foreground">
                {selectedNodes.filter((n) => n.kind === "hold").length} hold-short points ·{" "}
                {selectedNodes.filter((n) => n.kind === "intersection").length} intersections
              </p>
            )}
          </CardHeader>
        </Card>
      )}

      <div
        className="absolute top-4 right-4 z-10 flex flex-col gap-1"
        onPointerDown={(e) => e.stopPropagation()}
        onPointerUp={(e) => e.stopPropagation()}
      >
        <Button variant="outline" size="icon" className="bg-scrim backdrop-blur" aria-label="Zoom in" onClick={() => zoomBy(1.6)}>
          <Plus />
        </Button>
        <Button variant="outline" size="icon" className="bg-scrim backdrop-blur" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}>
          <Minus />
        </Button>
        <Button variant="outline" size="icon" className="bg-scrim backdrop-blur" aria-label="Reset view" onClick={reset}>
          <Maximize />
        </Button>
      </div>
    </div>
  );
}
