"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CameraPlayer } from "./CameraPlayer";
import { useUserAuth } from "@/lib/user-auth";

/** The session layer reads the registered feed from `?src=`, as every player does. */
export function cameraSource(stream: string) {
  return `/streams/api/ws?src=${encodeURIComponent(stream)}`;
}

export function EquipmentCameraButton({ stream, label, transport }: {
  stream: string; label: string; transport?: "go2rtc" | "mjpeg";
}) {
  const [open, setOpen] = useState(false);
  const { authenticated, requestLogin } = useUserAuth();
  const close = () => setOpen(false);
  return <>
    <button type="button" aria-pressed={open} onClick={() => {
      if (!authenticated) { requestLogin(); return; }
      setOpen(!open);
    }} title={open ? "Turn off and hide camera" : "Show attached camera"}
      className="flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100">Camera</button>
    {open && authenticated && createPortal(<CameraWindow stream={stream} label={label} transport={transport} onClose={close} />, document.body)}
  </>;
}
function CameraWindow({ stream, label, transport, onClose }: {
  stream: string; label: string; transport?: "go2rtc" | "mjpeg"; onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState(() => ({
    x: Math.max(8, window.innerWidth - 496), y: 64,
    width: Math.min(480, window.innerWidth - 16), height: Math.min(380, window.innerHeight - 80),
  }));
  const gesture = useRef<{ kind: "move" | "resize"; x: number; y: number; box: typeof box } | null>(null);
  const clamp = useCallback((b: typeof box) => {
    const width = Math.max(160, Math.min(b.width, window.innerWidth - 16));
    const height = Math.max(120, Math.min(b.height, window.innerHeight - 16));
    return { width, height, x: Math.max(8, Math.min(b.x, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(b.y, window.innerHeight - height - 8)) };
  }, []);
  useEffect(() => {
    panel.current?.focus();
    const resize = () => setBox((b) => clamp(b));
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
    };
  }, [clamp]);
  const begin = (event: React.PointerEvent<HTMLElement>, kind: "move" | "resize") => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { kind, x: event.clientX, y: event.clientY, box };
  };
  const move = (event: React.PointerEvent<HTMLElement>) => {
    const g = gesture.current;
    if (!g) return;
    const dx = event.clientX - g.x, dy = event.clientY - g.y;
    setBox(clamp(g.kind === "move" ? { ...g.box, x: g.box.x + dx, y: g.box.y + dy }
      : { ...g.box, width: g.box.width + dx, height: g.box.height + dy }));
  };
  const end = () => { gesture.current = null; };
  return <div ref={panel} role="dialog" aria-label="USB camera preview" tabIndex={-1}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}
    className="fixed z-50 flex flex-col overflow-hidden rounded-xl border border-slate-500 bg-slate-950 text-slate-100 shadow-2xl"
    style={{ left: box.x, top: box.y, width: box.width, height: box.height }}>
    <div className="flex shrink-0 items-center gap-2 border-b border-slate-700 px-3 py-2">
      <button type="button" aria-label="Move camera window" title="Drag to move; arrow keys also move"
        className="min-w-0 flex-1 cursor-move touch-none text-left text-sm font-semibold"
        onPointerDown={(e) => begin(e, "move")} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
        onKeyDown={(e) => {
          const d: Record<string, [number, number]> = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
          if (d[e.key]) { e.preventDefault(); const [x, y] = d[e.key]; setBox(clamp({ ...box, x: box.x + x, y: box.y + y })); }
        }}>Camera · {label}</button>
      <button type="button" onClick={onClose} aria-label="Turn off and hide camera" title="Turn off and hide"
        className="rounded px-2 py-1 text-lg hover:bg-slate-700">×</button>
    </div>
    <CameraPlayer src={cameraSource(stream)} transport={transport} className="min-h-0 flex-1 w-full" />
    <div className="flex h-6 shrink-0 items-center justify-between px-3 text-[11px] text-slate-400">
      <span>Drag title to move · × turns preview off</span>
      <button type="button" aria-label="Resize camera window" title="Drag to resize; arrow keys also resize"
        className="cursor-se-resize touch-none px-1 text-lg" onPointerDown={(e) => begin(e, "resize")}
        onPointerMove={move} onPointerUp={end} onPointerCancel={end}
        onKeyDown={(e) => {
          const d: Record<string, [number, number]> = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
          if (d[e.key]) { e.preventDefault(); const [w, h] = d[e.key]; setBox(clamp({ ...box, width: box.width + w, height: box.height + h })); }
        }}>◢</button>
    </div>
  </div>;
}
