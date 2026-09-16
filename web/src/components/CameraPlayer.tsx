"use client";

import { useEffect, useState } from "react";

import { MsePlayer } from "./MsePlayer";
import { WebRtcPlayer } from "./WebRtcPlayer";
import { MjpegPlayer } from "./MjpegPlayer";
import { mseSupported } from "@/lib/go2rtc";

/**
 * Picks the right go2rtc player for the current browser.
 *
 * - Desktop / Android Chrome / Firefox: MSE (`MsePlayer`) - the proven,
 *   already-deployed path.
 * - iPhone Safari (and anything without the unmanaged `MediaSource` API):
 *   WebRTC (`WebRtcPlayer`). This is the fix for "can't view the stream on
 *   iPhone" - iPhone never exposes `MediaSource`, so the MSE player would
 *   just show "Browser does not support MSE / MP4".
 *
 * Both players take the same props and connect to the same `src`
 * (`/streams/api/ws?src=<stream>`), so this is a transparent swap.
 *
 * The capability check runs in an effect (post-mount) to avoid an SSR /
 * client hydration mismatch: the server can't know what the browser
 * supports, so the first paint renders the MSE wrapper and we flip to
 * WebRTC immediately after mount on iPhone.
 */
export function CameraPlayer(props: {
  src: string | null;
  className?: string;
  disabled?: boolean;
  /** Explicit, server-approved background monitoring; never browser-only authority. */
  grantId?: string;
  /** Registry-selected upstream. Defaults to the existing go2rtc path. */
  transport?: "go2rtc" | "mjpeg";
}) {
  const [useWebrtc, setUseWebrtc] = useState(false);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    setUseWebrtc(!mseSupported());
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const changed = () => {
      clearTimeout(timer);
      if (!document.hidden) setHidden(false);
      else timer = setTimeout(() => setHidden(true), 3000);
    };
    // A tab opened in the background must not briefly start a camera.
    setHidden(document.hidden);
    document.addEventListener("visibilitychange", changed);
    return () => { clearTimeout(timer); document.removeEventListener("visibilitychange", changed); };
  }, []);

  const paused = !props.grantId && hidden;
  const playerProps = { ...props, disabled: props.disabled || paused };
  if (paused) return <div className={props.className ?? "aspect-video"}>Video paused while this tab is hidden.</div>;
  if (props.transport === "mjpeg") return <MjpegPlayer {...playerProps} />;

  return useWebrtc ? <WebRtcPlayer {...playerProps} /> : <MsePlayer {...playerProps} />;
}
