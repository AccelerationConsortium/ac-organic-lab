import type { ControlAck } from "@/types/api";

/**
 * Gateway control endpoints can soft-fail: HTTP 200 with `ok: false` in the
 * ControlAck body (e.g. the camera's PTZ head is at a physical pan/tilt
 * limit — the ONVIF call succeeded, the hardware just has nowhere to go).
 * `controlPost` only throws on non-2xx, so mutations must check the ack.
 *
 * Returns the failure message for a soft-failed ack, or null when the ack
 * (or any non-ControlAck response shape) represents success.
 */
export function ackFailureMessage(ack: unknown): string | null {
  if (typeof ack !== "object" || ack === null) return null;
  const candidate = ack as ControlAck;
  if (candidate.ok === false) {
    return candidate.message || "Device reported the action failed";
  }
  return null;
}
