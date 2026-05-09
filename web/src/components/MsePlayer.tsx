"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Codec preference list mirrored from go2rtc's reference `video-rtc.js`.
 * Order matters - we filter this against `MediaSource.isTypeSupported`
 * and forward whatever the browser accepts to go2rtc as a single
 * comma-separated string. go2rtc then picks the best transcoder /
 * passthrough for the underlying RTSP source.
 */
const CANDIDATE_CODECS = [
  "avc1.640029", // H.264 high 4.1 (Chromecast 1st/2nd gen)
  "avc1.64002A", // H.264 high 4.2 (Chromecast 3rd gen)
  "avc1.640033", // H.264 high 5.1
  "avc1.4D401E", // H.264 main 3.0 (most Tapo C-series)
  "avc1.42E01E", // H.264 baseline 3.0
  "hvc1.1.6.L153.B0", // H.265 main 5.1
  "mp4a.40.2", // AAC LC
  "mp4a.40.5", // AAC HE
  "flac",
  "opus",
];

function supportedMseCodecs(): string {
  if (typeof MediaSource === "undefined") return "";
  return CANDIDATE_CODECS.filter((c) =>
    MediaSource.isTypeSupported(`video/mp4; codecs="${c}"`),
  ).join(",");
}

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

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
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

/**
 * Convert a relative MSE URL like `/streams/api/ws?src=...` into an
 * absolute `ws://`/`wss://` URL.
 *
 * Resolution order:
 *
 * 1. Absolute `ws://`/`wss://` URLs are returned unchanged.
 * 2. `NEXT_PUBLIC_GO2RTC_WS_URL` (if set at build time) replaces the
 *    leading `/streams` segment. This is how local dev talks directly
 *    to go2rtc on `:1984` — Next.js's `rewrites()` does an HTTP
 *    `101 Switching Protocols` but does NOT actually forward the WS
 *    frames, so we have to bypass it for WebSocket traffic.
 * 3. Otherwise we anchor the relative URL on the current page origin.
 *    This is what production deploys behind Caddy use — the reverse
 *    proxy handles the WS upgrade and frame forwarding correctly.
 */
function absoluteWs(src: string): string {
  if (src.startsWith("ws://") || src.startsWith("wss://")) return src;

  const explicitBase = process.env.NEXT_PUBLIC_GO2RTC_WS_URL;
  if (explicitBase && src.startsWith("/streams")) {
    return `${explicitBase.replace(/\/$/, "")}${src.slice("/streams".length)}`;
  }

  if (typeof window === "undefined") return src;
  const wsScheme = window.location.protocol === "https:" ? "wss" : "ws";
  const path = src.startsWith("/") ? src : `/${src}`;
  return `${wsScheme}://${window.location.host}${path}`;
}
