"use client";

import { useEffect, useRef, useState } from "react";

import { openMjpegSession } from "@/lib/camera-session";

/** Authenticated MJPEG player for registered non-go2rtc equipment cameras. */
export function MjpegPlayer({
  src,
  className,
  disabled = false,
  grantId,
  depth = false,
}: {
  src: string | null;
  className?: string;
  disabled?: boolean;
  grantId?: string;
  /** The lens has a registered depth readout: a click on the picture reads the distance there. */
  depth?: boolean;
}) {
  const lease = useRef<AbortController | null>(null);
  const [stream, setStream] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);

  useEffect(() => {
    setError(null);
    setStream(null);
    setPick(null);
    if (disabled || !src) return;
    const controller = new AbortController();
    lease.current = controller;
    openMjpegSession(src, controller.signal, setError, grantId)
      .then((path) => {
        if (!controller.signal.aborted) setStream(path);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError((err as Error).message);
      });
    return () => {
      controller.abort();
      if (lease.current === controller) lease.current = null;
    };
  }, [src, disabled, grantId]);

  const wrapperSizing = className ?? "aspect-video w-full";
  return (
    <div className={`relative overflow-hidden rounded-md bg-slate-900 ${wrapperSizing}`}>
      {stream && (
        // MJPEG is a long-lived multipart image response; Next Image cannot
        // consume it and would defeat the authenticated streaming session.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={stream}
          alt="Live camera stream"
          className={`absolute inset-0 h-full w-full object-contain ${depth ? "cursor-crosshair" : ""}`}
          onClick={depth ? (event) => void measure(event, stream, setPick) : undefined}
          onError={() => {
            lease.current?.abort();
            setError("Camera stream ended; hide and reopen it to retry");
          }}
        />
      )}
      {stream && pick && <DepthMarker pick={pick} />}
      {stream && depth && !pick && !error && (
        <div className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-slate-200">
          Click the picture to measure distance
        </div>
      )}
      {(disabled || !src || !stream) && !error && (
        <div className="absolute inset-0 flex items-center justify-center text-xs uppercase tracking-wider text-slate-500">
          {disabled ? "Streaming disabled" : !src ? "No stream" : "Opening stream"}
        </div>
      )}
      {error && !disabled && src && (
        <div className="absolute inset-x-0 bottom-0 bg-rose-900/70 px-2 py-1 text-center text-[11px] font-mono text-rose-100">
          {error}
        </div>
      )}
    </div>
  );
}

type Pick = { left: number; top: number; text: string };

/**
 * Map a click on the object-contain picture to the frame's own pixels (the
 * depth readout's coordinates; depth is aligned to color, so either picture
 * works), then ask the session's depth readout for the distance there.
 */
async function measure(
  event: React.MouseEvent<HTMLImageElement>,
  stream: string,
  setPick: (pick: Pick | null) => void,
) {
  const img = event.currentTarget;
  const rect = img.getBoundingClientRect();
  const { naturalWidth: nw, naturalHeight: nh } = img;
  if (!nw || !nh) return;
  const scale = Math.min(rect.width / nw, rect.height / nh);
  const left = event.clientX - rect.left;
  const top = event.clientY - rect.top;
  const x = Math.floor((left - (rect.width - nw * scale) / 2) / scale);
  const y = Math.floor((top - (rect.height - nh * scale) / 2) / scale);
  if (x < 0 || y < 0 || x >= nw || y >= nh) return;
  setPick({ left, top, text: "measuring…" });
  const endpoint = stream.replace(/\/mjpeg$/, "/depth");
  try {
    const r = await fetch(`${endpoint}?x=${x}&y=${y}`, { credentials: "same-origin", cache: "no-store" });
    const body = await r.json();
    if (!r.ok) {
      setPick({ left, top, text: body.detail ?? "depth unavailable" });
      return;
    }
    const d = body.distance_m as number | null;
    setPick({ left, top, text: d == null ? "no depth here" : `${(d * 1000).toFixed(0)} mm` });
  } catch {
    setPick({ left, top, text: "depth unavailable" });
  }
}

function DepthMarker({ pick }: { pick: Pick }) {
  return (
    <div className="pointer-events-none absolute" style={{ left: pick.left, top: pick.top }}>
      <div className="absolute -left-2 -top-2 h-4 w-4 rounded-full border-2 border-white shadow-[0_0_0_1px_black]" />
      <div className="absolute left-3 top-1 whitespace-nowrap rounded bg-black/75 px-1.5 py-0.5 font-mono text-[11px] text-white" data-testid="depth-reading">
        {pick.text}
      </div>
    </div>
  );
}
