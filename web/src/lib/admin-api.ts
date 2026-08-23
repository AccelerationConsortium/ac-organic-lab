import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Shared plumbing for the admin console and the "Accounts & Activities"
// headline tile (which the Overview page also mounts for admins): the
// response shapes of the ac_auth sidecar's /admin/* proxies and the
// dashboard's control-action audit feed, one react-query hook, and the
// formatters both surfaces use. Server-side access is enforced by the Next
// middleware (admin session for /api/admin/*) and re-checked by the sidecar;
// `enabled` below only avoids firing requests that would 401.
// ---------------------------------------------------------------------------

export interface AdminState {
  roster: {
    users: number;
    automation: number;
    projects: number;
    active_accounts: number;
  };
  roster_loaded_at: number | null;
  last_reload: { ts: number; applied: boolean; errors: string[] } | null;
  pending_automation: string[];
  expiring_soon: { email: string; expires_at: number }[];
}

export interface SessionRow {
  email: string;
  created_at: number;
  expires_at: number;
}

export interface ControlAction {
  ts: string;
  device_id: string;
  message: string | null;
  action: string | null;
  method: string | null;
  status_code: number | null;
  outcome: string | null;
  owner: string | null;
  /** Wall-clock of the device interaction (claim → action → release), seconds.
   *  Null on rows written before 2026-07-24 and on refusals that never
   *  reached the device. */
  duration_s: number | null;
  /** Provenance of the click (X-Control-Origin). "assistant" when authorized
   *  from the lab assistant's confirm card; null for direct tile clicks. */
  origin: string | null;
}

export interface ControlActionsResponse {
  /** Newest-first window (the `limit` we asked for). */
  actions: ControlAction[];
  /** Lifetime row count — the headline figure; not capped by `limit`. */
  total: number;
}

/** `details.claimed_by` on a v1.1+ device's live /status. */
export interface ClaimedBy {
  session_id: string;
  owner: string;
  expires_at: string;
}

export async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) {
    const body = (await r.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `${r.status} from ${path}`);
  }
  return (await r.json()) as T;
}

/** Polling read of an admin/history endpoint; `enabled` gates on the admin
 *  session so a non-admin viewer never fires requests that would 401. */
export function useAdminQuery<T>(path: string, refetchMs: number, enabled: boolean) {
  return useQuery({
    queryKey: ["admin", path],
    queryFn: () => getJson<T>(path),
    refetchInterval: refetchMs,
    enabled,
  });
}

// ---- formatting --------------------------------------------------------------

const SHORT_STAMP: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

/** ac_auth timestamps are epoch seconds (REAL). Full form, for tooltips. */
export function fmtEpoch(t: number | null | undefined): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString();
}

/** Compact "Aug 23, 14:15" form for table cells; pair with a full `title`. */
export function fmtEpochShort(t: number | null | undefined): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString(undefined, SHORT_STAMP);
}

function parseIso(ts: string): Date {
  return new Date(ts.endsWith("Z") || ts.includes("+") ? ts : ts + "Z");
}

/** lab.db timestamps are ISO-8601 UTC strings. Full form, for tooltips. */
export function fmtIso(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = parseIso(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export function fmtIsoShort(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = parseIso(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, SHORT_STAMP);
}

/** "2 d 4 h" / "3 h 12 m" / "45 m" / "30 s" — coarse on purpose (headline use). */
export function fmtDuration(seconds: number): string {
  const s = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return h > 0 ? `${d} d ${h} h` : `${d} d`;
  if (h > 0) return m > 0 ? `${h} h ${m} m` : `${h} h`;
  if (m > 0) return `${m} m`;
  return `${Math.floor(s)} s`;
}
