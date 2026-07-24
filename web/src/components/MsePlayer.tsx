"use client";

import { useEffect, useRef, useState } from "react";

import { absoluteWs, supportedMseCodecs } from "@/lib/go2rtc";

/**
 * Tiny MSE player wrapping go2rtc's reference WebSocket protocol.
 *
 * Protocol (verified against go2rtc's `internal/mp4/ws.go` handler):
 *
 *   1. Browser opens `ws://host/api/ws?src=<stream_name>`.
 *   2. **Browser sends** `{"type": "mse", "value": "<codecs>"}` where
 *      `<codecs>` is the filtered candidate list above. THIS WAS
 *      MISSING - without it go2rtc never registers the consumer and
 *      no fMP4 frames are ever sent.
 *   3. go2rtc replies with `{"type": "mse", "value": "<actual codec
 *      mime>"}` naming the codec it picked.
 *   4. go2rtc starts pushing fMP4 segments as binary frames; we feed
 *      them to a `MediaSource` via `addSourceBuffer.appendBuffer`.
 *
 * Re-connects on close with a 1s backoff, capped at one reconnect per
 * `disabled` toggle. The component is self-contained: pass a stream
 * URL, get a `<video>` that plays it.
 */
export function MsePlayer({
  src,
  className,
  disabled = false,
}: {
  src: string | null;
  className?: string;
  disabled?: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const video = videoRef.current;
    if (!video || disabled || !src) return;

    const wsUrl = absoluteWs(src);
    let cancelled = false;
    let socket: WebSocket | null = null;
    let mediaSource: MediaSource | null = null;
    let sourceBuffer: SourceBuffer | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    const queue: ArrayBuffer[] = [];

    const flushQueue = () => {
      if (!sourceBuffer || sourceBuffer.updating || queue.length === 0) return;
      try {
        sourceBuffer.appendBuffer(queue.shift()!);
      } catch (err) {
        setError(`MSE error: ${(err as Error).message}`);
      }
    };

    const open = () => {
      const codecs = supportedMseCodecs();
      if (!codecs) {
        setError("Browser does not support MSE / MP4");
        return;
      }

      mediaSource = new MediaSource();
      video.src = URL.createObjectURL(mediaSource);

      mediaSource.addEventListener("sourceopen", () => {
        if (cancelled || !mediaSource) return;

        socket = new WebSocket(wsUrl);
        socket.binaryType = "arraybuffer";

        socket.onopen = () => {
          // Send the MSE handshake. go2rtc registers the consumer on
          // receipt and replies with the codec mime it picked.
          socket?.send(JSON.stringify({ type: "mse", value: codecs }));
        };

        socket.onmessage = (event) => {
          if (typeof event.data === "string") {
            try {
              const payload = JSON.parse(event.data) as {
                type: string;
                value: string;
              };
              if (payload.type === "mse" && mediaSource && !sourceBuffer) {
                // go2rtc returns a full mime string already (e.g.
                // `video/mp4; codecs="avc1.640015,mp4a.40.2"`). Older
                // builds returned just the codec list - handle both.
                const mime = payload.value.startsWith("video/")
                  ? payload.value
                  : `video/mp4; codecs="${payload.value}"`;
                if (!MediaSource.isTypeSupported(mime)) {
                  setError(`Browser cannot play ${mime}`);
                  return;
                }
                sourceBuffer = mediaSource.addSourceBuffer(mime);
                sourceBuffer.mode = "segments";
                sourceBuffer.addEventListener("updateend", flushQueue);
              } else if (payload.type === "error") {
                setError(`go2rtc: ${payload.value}`);
              }
            } catch {
              // Ignore non-JSON status frames.
            }
          } else if (event.data instanceof ArrayBuffer) {
            queue.push(event.data);
            flushQueue();
          }
        };

        socket.onerror = () => setError("WebSocket error");
        socket.onclose = () => {
          if (cancelled) return;
          reconnectTimer = setTimeout(() => {
            if (!cancelled) open();
          }, 1000);
        };
      });
    };

    open();

    // Keep playback near the live edge. A live MSE stream plays at 1x from
    // wherever decoding began, so any buffer accumulated at startup (or after a
    // stall / backgrounded tab) becomes permanent latency. Ease the rate up on
    // a small lead; hard-seek if we fall badly behind. Camera feeds have no
    // audio, so the slight speedup is imperceptible. Bounds delay to
    // ~LIVE_TARGET instead of letting it creep.
    const LIVE_TARGET = 0.35; // sit this far behind the live edge (s)
    const LIVE_NUDGE = 0.9; // ease toward live once the lead exceeds this (s)
    const LIVE_RESYNC = 3.0; // hard-seek to live once the lead exceeds this (s)
    const keepLiveEdge = () => {
      if (!sourceBuffer) return;
      const b = video.buffered;
      if (!b.length) return;
      const end = b.end(b.length - 1);
      const lead = end - video.currentTime;
      if (lead > LIVE_RESYNC) {
        try {
          video.currentTime = end - LIVE_TARGET;
        } catch {
          /* ignore */
        }
        video.playbackRate = 1.0;
      } else if (lead > LIVE_NUDGE) {
        video.playbackRate = 1.08; // smooth catch-up, no visible jump
      } else if (video.playbackRate !== 1.0) {
        video.playbackRate = 1.0;
      }
    };
    video.addEventListener("timeupdate", keepLiveEdge);

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      video.removeEventListener("timeupdate", keepLiveEdge);
      video.playbackRate = 1;
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      try {
        if (mediaSource && mediaSource.readyState === "open") mediaSource.endOfStream();
      } catch {
        /* ignore */
      }
      try {
        if (video.src) URL.revokeObjectURL(video.src);
      } catch {
        /* ignore */
      }
      video.removeAttribute("src");
      video.load();
    };
  }, [src, disabled]);

  // The default sizing (`aspect-video w-full`) gives a 16:9 box anchored
  // at the parent's full width - that's the right behaviour for inline
  // previews (e.g. `PlatformCameraPreview`). Inside fixed-row grids
  // (e.g. `CameraTile` on a platform detail page) the parent often has
  // surplus vertical space, so callers can override the wrapper sizing
  // by passing their own `className` (e.g. `flex-1 min-h-0 w-full`).
  // The `<video>` element itself is `object-contain`, so changing the
  // wrapper's aspect just letterboxes the 16:9 frame inside instead of
  // distorting it.
  const wrapperSizing = className ?? "aspect-video w-full";

  return (
    <div className={`relative overflow-hidden rounded-md bg-slate-900 ${wrapperSizing}`}>
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="absolute inset-0 h-full w-full object-contain"
      />
      {(disabled || !src) && (
        <div className="absolute inset-0 flex items-center justify-center text-xs uppercase tracking-wider text-slate-500">
          {disabled ? "Streaming disabled" : "No stream"}
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
