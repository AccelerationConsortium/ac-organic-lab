import { ApiError } from "./api";

/**
 * Shared model for the inline "action-error" message bubble that every
 * control tile renders below its buttons (the pattern PlateSealerTile
 * pioneered). It carries a device refusal — a 412 precondition, a 423 claim
 * conflict, a 409 device-state conflict — back to the operator instead of
 * swallowing it.
 *
 * `kind` is a hint for future styling; today every kind shares the amber
 * tone in <ActionErrorBand>. These are refusals to act, not catastrophic
 * faults — device faults surface separately in the rose <LastErrorBadge>
 * (TileShell header).
 */
export interface ActionError {
  status: number;
  message: string;
  kind: "precondition" | "claim" | "state" | "other";
}

/**
 * Per-tile specializer for the 412 body. Each device ships its own
 * precondition body shape (plateloc stage/temperature, shaker temperature,
 * biostack plate_staged, …); a tile passes one of these so the generic
 * interpreter can render a device-specific sentence. Return a message string,
 * or `null` to fall back to the generic `detail`.
 *
 * Define these at module scope so their identity is stable across renders
 * (the hook memoizes on it).
 */
export type Parse412 = (
  body: Record<string, unknown>,
  ctx: { action?: string; retryAfterS: number | null },
) => string | null;

/**
 * Translate a thrown control-action error into a renderable {@link ActionError}.
 * Handles the shapes common to every STATUS_SPEC device (423 claim conflict,
 * 409 device-state conflict, non-HTTP failures); delegates the device-specific
 * 412 body to `opts.parse412` when supplied.
 */
export function interpretActionError(
  e: unknown,
  opts: { action?: string; parse412?: Parse412 } = {},
): ActionError {
  if (!(e instanceof ApiError)) {
    const message = e instanceof Error ? e.message : String(e);
    return { status: 0, message, kind: "other" };
  }
  const body = (e.body ?? {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : undefined;

  // 412 Precondition Failed — device declined an inapplicable request. The
  // body shape is device-specific, so let the tile's parse412 render it;
  // fall back to the device's `detail`.
  if (e.status === 412) {
    const custom = opts.parse412?.(body, {
      action: opts.action,
      retryAfterS: e.retryAfterS,
    });
    return {
      status: 412,
      message: custom ?? detail ?? "Device precondition not met.",
      kind: "precondition",
    };
  }

  // 423 Locked — another holder owns the cooperative claim.
  if (e.status === 423) {
    const claimedBy = body.claimed_by as { owner?: string } | undefined;
    const owner = claimedBy?.owner;
    return {
      status: 423,
      message: owner
        ? `Device claim is held by ${owner}. Try again later.`
        : detail ?? "Device is busy with another caller.",
      kind: "claim",
    };
  }

  // 409 Conflict — device-state conflict (typically "driver not connected").
  if (e.status === 409) {
    const msg = detail ?? "Action rejected.";
    const hint = /init|startup|connect/i.test(msg) ? " Click Startup first." : "";
    return { status: 409, message: msg + hint, kind: "state" };
  }

  // Fall-through: surface whatever the device sent.
  return { status: e.status, message: detail ?? e.message, kind: "other" };
}
