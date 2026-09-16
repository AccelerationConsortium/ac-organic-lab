"use client";

import { useEffect, useRef, useState } from "react";

import { openMjpegSession } from "@/lib/camera-session";

/** Authenticated MJPEG player for registered non-go2rtc equipment cameras. */
export function MjpegPlayer({
  src,
  className,
  disabled = false,
  grantId,
}: {
  src: string | null;
  className?: string;
  disabled?: boolean;
  grantId?: string;
}) {
  const lease = useRef<AbortController | null>(null);
  const [stream, setStream] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    setStream(null);
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
          className="absolute inset-0 h-full w-full object-contain"
          onError={() => {
            lease.current?.abort();
            setError("Camera stream ended; hide and reopen it to retry");
          }}
        />
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
