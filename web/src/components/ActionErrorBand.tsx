import type { ActionError } from "@/lib/action-error";
import { MessageBand } from "./MessageBand";

/**
 * The shared "message bubble" for a control-action refusal — an amber inline
 * band showing the HTTP status + a human-readable message from a 412 / 423 /
 * 409 (or transport failure). Rendered below a tile's action buttons.
 *
 * Amber (not rose) is deliberate: this is the device declining an inapplicable
 * request, distinct from a device *fault* (which surfaces in the rose
 * <LastErrorBadge> in the TileShell header, or a rose <MessageBand>). Renders
 * nothing when `error` is null, so tiles can drop
 * `<ActionErrorBand error={actionError} />` in unconditionally.
 */
export function ActionErrorBand({ error }: { error: ActionError | null }) {
  if (error === null) return null;
  return (
    <MessageBand tone="amber" tag={error.status > 0 ? error.status : undefined}>
      {error.message}
    </MessageBand>
  );
}
