"use client";

import { useEffect, useRef, useState } from "react";

import { absoluteWs } from "@/lib/go2rtc";

/**
 * WebRTC player wrapping go2rtc's reference WebSocket signaling protocol.
 *
 * This is the iPhone-Safari path. iPhone does not expose the unmanaged
 * `MediaSource` API, so the MSE player (`MsePlayer`) cannot run there;
 * WebRTC is the only first-class low-latency option Safari gives us.
 * `CameraPlayer` routes iPhone (and any MSE-less browser) here.
 *
 * Protocol (matches go2rtc's `www/video-rtc.js`, server side
 * `internal/api/ws.go` + `internal/webrtc`):
 *
 *   1. Browser opens `ws://host/api/ws?src=<stream_name>` (the SAME URL
 *      the MSE player uses - go2rtc dispatches on the first message type).
 *   2. Browser creates an `RTCPeerConnection`, adds recvonly transceivers,
 *      and sends `{"type":"webrtc/offer","value":"<sdp>"}`.
 *   3. Both sides trickle ICE via `{"type":"webrtc/candidate","value":...}`.
 *   4. go2rtc replies `{"type":"webrtc/answer","value":"<sdp>"}`; the
 *      negotiated media track lands on `pc.ontrack`, which we attach to
 *      the `<video>` element's `srcObject`.
 *
 * Media transport: go2rtc advertises its host candidate (the MagicDNS
 * host:8555/tcp set via `GO2RTC_WEBRTC_HOST` in the gateway's
 * `bootstrap_go2rtc.py`). Over the Tailnet the iPhone reaches that
 * directly, so no STUN/TURN is required and `iceServers` is empty.
 *
 * Reconnects on close / failure with a 2s backoff.
 */
export function WebRtcPlayer({
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
    if (typeof RTCPeerConnection === "undefined") {
      setError("Browser does not support WebRTC");
      return;
    }

    const wsUrl = absoluteWs(src);
    let cancelled = false;
    let socket: WebSocket | null = null;
    let pc: RTCPeerConnection | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const teardown = () => {
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      socket = null;
      try {
        pc?.close();
      } catch {
        /* ignore */
      }
      pc = null;
    };

    const scheduleReconnect = () => {
      if (cancelled || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!cancelled) {
          teardown();
          open();
        }
      }, 2000);
    };

    const open = () => {
      // recvonly: the browser only consumes the camera's media.
      pc = new RTCPeerConnection({ iceServers: [] });

      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });

      pc.ontrack = (ev) => {
        if (cancelled) return;
        // ev.streams[0] carries both tracks once negotiated.
        if (ev.streams && ev.streams[0]) {
          if (video.srcObject !== ev.streams[0]) {
            video.srcObject = ev.streams[0];
          }
        } else {
          let stream = video.srcObject as MediaStream | null;
          if (!stream) {
            stream = new MediaStream();
            video.srcObject = stream;
          }
          stream.addTrack(ev.track);
        }
        // Safari autoplay: muted + playsInline (set on the element) lets
        // this resolve without a user gesture.
        void video.play().catch(() => {
          /* autoplay rejected; the controls/poster still show */
        });
      };

      pc.onicecandidate = (ev) => {
        if (ev.candidate && socket?.readyState === WebSocket.OPEN) {
          socket.send(
            JSON.stringify({
              type: "webrtc/candidate",
              value: ev.candidate.candidate,
            }),
          );
        }
      };

      pc.onconnectionstatechange = () => {
        if (cancelled || !pc) return;
        if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          scheduleReconnect();
        }
      };

      socket = new WebSocket(wsUrl);

      socket.onopen = async () => {
        if (cancelled || !pc) return;
        try {
          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          socket?.send(
            JSON.stringify({
              type: "webrtc/offer",
              value: pc.localDescription?.sdp ?? offer.sdp,
            }),
          );
        } catch (err) {
          setError(`WebRTC offer failed: ${(err as Error).message}`);
        }
      };

      socket.onmessage = async (event) => {
        if (cancelled || !pc || typeof event.data !== "string") return;
        let payload: { type?: string; value?: string };
        try {
          payload = JSON.parse(event.data);
        } catch {
          return; // ignore non-JSON status frames
        }

        try {
          if (payload.type === "webrtc/answer" && payload.value) {
            await pc.setRemoteDescription({ type: "answer", sdp: payload.value });
            setError(null);
          } else if (payload.type === "webrtc/candidate" && payload.value) {
            await pc.addIceCandidate({ candidate: payload.value, sdpMid: "0" });
          } else if (payload.type === "error" && payload.value) {
            setError(`go2rtc: ${payload.value}`);
          }
        } catch (err) {
          setError(`WebRTC error: ${(err as Error).message}`);
        }
      };

      socket.onerror = () => setError("WebSocket error");
      socket.onclose = () => {
        if (!cancelled) scheduleReconnect();
      };
    };

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      teardown();
      try {
        video.srcObject = null;
      } catch {
        /* ignore */
      }
      video.removeAttribute("src");
      video.load();
    };
  }, [src, disabled]);

  // Sizing mirrors MsePlayer so the two are drop-in interchangeable.
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
