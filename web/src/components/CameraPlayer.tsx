"use client";

import { useEffect, useState } from "react";

import { MsePlayer } from "./MsePlayer";
import { WebRtcPlayer } from "./WebRtcPlayer";
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
}) {
  const [useWebrtc, setUseWebrtc] = useState(false);

  useEffect(() => {
    setUseWebrtc(!mseSupported());
  }, []);

  return useWebrtc ? <WebRtcPlayer {...props} /> : <MsePlayer {...props} />;
}
