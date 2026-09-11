"use client";

import type { PointerEvent, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { clampOffset, formatZoom, type Offset } from "@/lib/video-zoom";

/**
 * Digital zoom wrapper for a live camera player.
 *
 * Scales its child (`level` ×) about the centre and lets the operator drag
 * the enlarged frame around; the offset is clamped so no blank space is
 * ever revealed (see `lib/video-zoom.ts`). Double-click calls `onToggle`
 * so a tile can bind "zoom to 2× / back to 1×" without wiring its own
 * handler. Purely view-side: nothing here talks to the camera, which is
 * why it works for view-only visitors too.
 *
 * The child is expected to fill the box (`h-full w-full`); this wrapper
 * owns the outer sizing via `className` (e.g. `flex-1 min-h-[220px]`).
 */
export function VideoZoom({
  level,
  onToggle,
  className = "",
  children,
}: {
  level: number;
  onToggle?: () => void;
  className?: string;
  children: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [offset, setOffset] = useState<Offset>({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ pointerId: number; startX: number; startY: number; origin: Offset } | null>(null);

  const boxSize = () => {
    const rect = boxRef.current?.getBoundingClientRect();
    return { width: rect?.width ?? 0, height: rect?.height ?? 0 };
  };

  // Zooming out (or resetting) can leave a previously legal offset out of
  // bounds; re-clamp so the frame snaps back to cover the container.
  useEffect(() => {
    setOffset((current) => clampOffset(current, level, boxSize()));
  }, [level]);

  const zoomed = level > 1;

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!zoomed || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, origin: offset };
    setDragging(true);
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.pointerId !== event.pointerId) return;
    setOffset(
      clampOffset(
        { x: d.origin.x + (event.clientX - d.startX), y: d.origin.y + (event.clientY - d.startY) },
        level,
        boxSize(),
      ),
    );
  };

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
  };

  return (
    <div
      ref={boxRef}
      className={`relative overflow-hidden rounded-md ${className}`}
      style={{
        cursor: zoomed ? (dragging ? "grabbing" : "grab") : undefined,
        // Let the drag own touch gestures while zoomed; at 1× the page scrolls.
        touchAction: zoomed ? "none" : undefined,
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={onToggle}
      data-zoom-level={level}
    >
      <div
        className="absolute inset-0"
        style={{
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${level})`,
          transformOrigin: "center center",
          transition: dragging ? undefined : "transform 120ms ease-out",
        }}
      >
        {children}
      </div>
      {zoomed && (
        <div className="pointer-events-none absolute right-1.5 top-1.5 rounded bg-slate-900/70 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white">
          {formatZoom(level)} digital · drag to pan
        </div>
      )}
    </div>
  );
}
