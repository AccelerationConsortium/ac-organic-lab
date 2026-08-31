/**
 * Reading `details.claimed_by` off a live `/status` envelope.
 *
 * STATUS_SPEC v1.1 §5: a claim is a cooperative, heartbeated lock. While one
 * is held, the device rejects `/control/*` from anyone but the holder — and
 * the dashboard is *always* "anyone but the holder", because its passthrough
 * takes a fresh per-request claim for each action and releases it in a
 * `finally` (ARCHITECTURE design decision #1). So a claim visible on a poll
 * is one the dashboard cannot acquire, and every control on that tile would
 * come back 423.
 *
 * Hence this module exists to *disable* controls, not merely to label them.
 * Explaining "a workflow holds this" up front is strictly better than letting
 * the operator click and read a 423 out of a toast.
 *
 * Deliberately not filtered by owner. The dashboard's own transient claim can
 * in principle be caught mid-flight by a poll, but it is held for about three
 * round-trips against a 60 s poll interval, and when it *is* caught the label
 * is still true. Suppressing claims by owner name would instead hide the real
 * case this exists for: a signed-in operator's long-running workflow, which
 * carries their identity as the owner.
 */

import type { EquipmentStatus } from "@/types/api";

/** `details.claimed_by` on a v1.1+ device (STATUS_SPEC §2 `ClaimedBy`). */
export interface ClaimHolder {
  session_id: string;
  owner: string;
  expires_at: string;
}

/**
 * The live claim on a status envelope, or null when unclaimed — which
 * includes every v1.0 device, since they never publish the field.
 */
export function claimHolder(status: EquipmentStatus): ClaimHolder | null {
  const raw = (status.details ?? {})["claimed_by"];
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Partial<ClaimHolder>;
  // `owner` is the only field worth anything to a reader; a payload without
  // it is treated as no claim rather than as a claim by nobody.
  if (typeof c.owner !== "string" || c.owner === "") return null;
  return {
    session_id: typeof c.session_id === "string" ? c.session_id : "",
    owner: c.owner,
    expires_at: typeof c.expires_at === "string" ? c.expires_at : "",
  };
}

/**
 * "In use by agent:solubility-screening", for a tile banner.
 *
 * The owner string is device-supplied and free-form (an email, an agent id,
 * `ac-organic-lab-dashboard`), so it is rendered verbatim rather than parsed.
 */
export function claimLabel(holder: ClaimHolder): string {
  return `In use by ${holder.owner}`;
}

/**
 * Why a control is disabled, for a `title=` tooltip. Includes the expiry so
 * an operator can tell a live workflow from a claim about to lapse.
 */
export function claimTitle(holder: ClaimHolder): string {
  const expiry = formatExpiry(holder.expires_at);
  return expiry
    ? `${claimLabel(holder)}. The claim lapses at ${expiry} unless refreshed.`
    : `${claimLabel(holder)}.`;
}

function formatExpiry(iso: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
