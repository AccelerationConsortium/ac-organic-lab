"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useEquipmentList } from "@/lib/use-equipment";
import { useUserAuth } from "@/lib/user-auth";

// ---------------------------------------------------------------------------
// The admin console. Server-side access is enforced twice (Next middleware
// requires an admin session for /admin and /api/admin/*; the ac_auth sidecar
// re-checks admin on every /admin/* endpoint) — the client-side guard below
// is UX only. Read-only by design: roster edits stay in roster.yaml
// (edit → validate → commit → SIGHUP), never in a web form.
//
// Layout mirrors the platform pages: a grid of fixed-height tiles whose
// bodies scroll independently under a sticky table header.
// ---------------------------------------------------------------------------

interface AdminAccount {
  email: string;
  name: string;
  role: string;
  status: string;
  lab_account: string;
  notes: string;
  expires_at: number | null;
  is_expired: boolean;
  disabled_reason: string;
  last_login_at: number | null;
  active_sessions: number;
}

interface AdminAutomation {
  email: string;
  name: string;
  approved: boolean;
  platform: string | null;
  expires_at: number | null;
  is_expired: boolean;
  notes: string;
  api_keys: number;
}

interface AuthEventRow {
  ts: number;
  email: string;
  event: string;
  detail: string;
  ip: string;
  user_agent: string;
}

interface SessionRow {
  email: string;
  created_at: number;
  expires_at: number;
}

interface ApiKeyRow {
  id: number;
  email: string;
  label: string;
  created_at: number;
  expires_at: number | null;
  revoked: boolean;
  last_used_at: number | null;
}

interface AdminState {
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

interface ControlAction {
  ts: string;
  device_id: string;
  message: string | null;
  action: string | null;
  method: string | null;
  status_code: number | null;
  outcome: string | null;
  owner: string | null;
}

interface ClaimedBy {
  session_id: string;
  owner: string;
  expires_at: string;
}

// ---------------------------------------------------------------------------
// Data hooks
// ---------------------------------------------------------------------------

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) {
    const body = (await r.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `${r.status} from ${path}`);
  }
  return (await r.json()) as T;
}

function useAdmin<T>(path: string, refetchMs: number, enabled: boolean) {
  return useQuery({
    queryKey: ["admin", path],
    queryFn: () => getJson<T>(path),
    refetchInterval: refetchMs,
    enabled,
  });
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/** ac_auth timestamps are epoch seconds (REAL). */
function fmtEpoch(t: number | null | undefined): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString();
}

/** lab.db timestamps are ISO-8601 UTC strings. */
function fmtIso(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts.endsWith("Z") || ts.includes("+") ? ts : ts + "Z");
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

const EVENT_BADGE: Record<string, string> = {
  login_success: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  code_requested: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  logout: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  login_failed: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  login_rejected: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  roster_reload_applied: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
  roster_reload_rejected: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
};

// ---------------------------------------------------------------------------
// Tile primitives (mirrors the equipment-tile look: header row + scroll body)
// ---------------------------------------------------------------------------

function Tile({
  title,
  sub,
  wide,
  controls,
  children,
}: {
  title: string;
  sub?: string;
  /** Span both columns of the lg grid (banner tile). */
  wide?: boolean;
  /** Optional header widgets (filter dropdowns etc.). */
  controls?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950/40 ${
        wide ? "lg:col-span-2" : ""
      }`}
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink dark:text-slate-100">{title}</h2>
          {sub && <p className="mt-0.5 text-xs text-ink-subtle dark:text-slate-500">{sub}</p>}
        </div>
        {controls && <div className="flex shrink-0 items-center gap-2">{controls}</div>}
      </header>
      {/* Fixed-height scroll body — the tile keeps its footprint, content scrolls. */}
      <div className="max-h-80 overflow-y-auto overflow-x-auto">{children}</div>
    </section>
  );
}

function Select({
  value,
  onChange,
  options,
  allLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  allLabel: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="max-w-48 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-ink dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
    >
      <option value="">{allLabel}</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <table className="w-full text-left text-sm">
      {/* Sticky under the tile header while the body scrolls. Needs a solid bg. */}
      <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
        <tr className="text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-500">
          {head.map((h) => (
            <th key={h} className="px-4 py-2 font-medium">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{children}</tbody>
    </table>
  );
}

function Empty({ message }: { message: string }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-ink-muted dark:text-slate-400">{message}</p>
  );
}

function ErrorNote({ error }: { error: unknown }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-rose-600 dark:text-rose-400">
      {error instanceof Error ? error.message : "Failed to load."}
    </p>
  );
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: "warn" | "bad" }) {
  const toneCls =
    tone === "bad"
      ? "text-rose-600 dark:text-rose-400"
      : tone === "warn"
        ? "text-amber-600 dark:text-amber-400"
        : "text-ink dark:text-slate-100";
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-950/40">
      <p className="text-xs text-ink-subtle dark:text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${toneCls}`}>{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const { loading, authenticated, identity } = useUserAuth();
  const isAdmin = authenticated && identity?.role === "admin";

  const [eventEmail, setEventEmail] = useState("");
  const [actionOwner, setActionOwner] = useState("");
  const [actionDevice, setActionDevice] = useState("");

  const accounts = useAdmin<{ users: AdminAccount[]; automation: AdminAutomation[] }>(
    "/api/admin/accounts",
    60_000,
    isAdmin,
  );
  const state = useAdmin<AdminState>("/api/admin/state", 60_000, isAdmin);
  const sessions = useAdmin<{ sessions: SessionRow[] }>("/api/admin/sessions", 30_000, isAdmin);
  const apiKeys = useAdmin<{ keys: ApiKeyRow[] }>("/api/admin/api-keys", 60_000, isAdmin);
  const events = useAdmin<{ events: AuthEventRow[] }>(
    `/api/admin/auth-events?limit=200${eventEmail ? `&email=${encodeURIComponent(eventEmail)}` : ""}`,
    30_000,
    isAdmin,
  );
  const controlActions = useAdmin<{ actions: ControlAction[] }>(
    "/api/history/control-actions?limit=200",
    30_000,
    isAdmin,
  );
  const equipment = useEquipmentList();

  const claims = useMemo(() => {
    const items = equipment.data?.equipment ?? [];
    return items.flatMap((e) => {
      const claimed = (e.status?.details as { claimed_by?: ClaimedBy } | undefined)
        ?.claimed_by;
      return claimed ? [{ id: e.id, name: e.name, claimed }] : [];
    });
  }, [equipment.data]);

  const emails = useMemo(
    () => (accounts.data?.users ?? []).map((u) => u.email).sort(),
    [accounts.data],
  );

  // Control-action filters are client-side over the fetched window (the API
  // filter is by device only; owner + device both narrow the loaded rows).
  const actionOwners = useMemo(
    () =>
      Array.from(
        new Set((controlActions.data?.actions ?? []).flatMap((a) => (a.owner ? [a.owner] : []))),
      ).sort(),
    [controlActions.data],
  );
  const actionDevices = useMemo(
    () =>
      Array.from(new Set((controlActions.data?.actions ?? []).map((a) => a.device_id))).sort(),
    [controlActions.data],
  );
  const filteredActions = useMemo(
    () =>
      (controlActions.data?.actions ?? []).filter(
        (a) =>
          (!actionOwner || a.owner === actionOwner) &&
          (!actionDevice || a.device_id === actionDevice),
      ),
    [controlActions.data, actionOwner, actionDevice],
  );

  if (loading) {
    return <div className="mt-6 h-24 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;
  }
  if (!isAdmin) {
    // The middleware redirects non-admins server-side; this covers client-side
    // navigations and sessions that expire while the page is open.
    return (
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
        <p className="text-sm font-medium text-ink-muted dark:text-slate-400">
          Admin console — sign in with an admin account to view this page.
        </p>
      </div>
    );
  }

  const s = state.data;
  const lastReload = s?.last_reload;

  return (
    <div className="mt-6 space-y-6">
      {/* ---- summary strip ------------------------------------------------ */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard
          label="Active accounts"
          value={s ? `${s.roster.active_accounts} / ${s.roster.users + s.roster.automation}` : "…"}
        />
        <StatCard label="Projects" value={s ? String(s.roster.projects) : "…"} />
        <StatCard
          label="Live sessions"
          value={sessions.data ? String(sessions.data.sessions.length) : "…"}
        />
        <StatCard
          label="Devices claimed"
          value={equipment.data ? String(claims.length) : "…"}
        />
        <StatCard
          label="Pending automation"
          value={s ? String(s.pending_automation.length) : "…"}
          tone={s && s.pending_automation.length > 0 ? "warn" : undefined}
        />
        <StatCard
          label="Roster reload"
          value={
            lastReload == null
              ? "none since start"
              : lastReload.applied
                ? "applied"
                : "REJECTED"
          }
          tone={lastReload && !lastReload.applied ? "bad" : undefined}
        />
      </div>

      {lastReload && !lastReload.applied && (
        <div className="rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
          <p className="font-medium">
            Last roster reload was rejected ({fmtEpoch(lastReload.ts)}) — the previous
            allow-list is still in effect.
          </p>
          <ul className="mt-1 list-inside list-disc text-xs">
            {lastReload.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      {s && s.expiring_soon.length > 0 && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          <span className="font-medium">Expiring within 30 days: </span>
          {s.expiring_soon.map((e) => `${e.email} (${fmtEpoch(e.expires_at)})`).join(", ")}
        </div>
      )}

      {/* ---- tile grid ----------------------------------------------------- */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* ---- sign-in activity ------------------------------------------- */}
        <Tile
          title="Sign-in activity"
          sub="auth_events audit log — codes, logins, failures, logouts."
          controls={
            <Select
              value={eventEmail}
              onChange={setEventEmail}
              options={emails}
              allLabel="All accounts"
            />
          }
        >
          {events.error ? (
            <ErrorNote error={events.error} />
          ) : !events.data ? (
            <Empty message="Loading…" />
          ) : events.data.events.length === 0 ? (
            <Empty message="No events recorded yet." />
          ) : (
            <Table head={["Time", "Event", "Account", "From"]}>
              {events.data.events.map((e, i) => (
                <tr key={`${e.ts}-${i}`}>
                  <td className="whitespace-nowrap px-4 py-2 text-xs">{fmtEpoch(e.ts)}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${
                        EVENT_BADGE[e.event] ??
                        "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                      }`}
                      title={e.detail || undefined}
                    >
                      {e.event}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs">{e.email || "—"}</td>
                  <td className="px-4 py-2 font-mono text-xs" title={e.user_agent}>
                    {e.ip || "—"}
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Tile>

        {/* ---- control-action audit ---------------------------------------- */}
        <Tile
          title="Control actions"
          sub="Dashboard-mediated device writes — who did what, how the device answered."
          controls={
            <>
              <Select
                value={actionOwner}
                onChange={setActionOwner}
                options={actionOwners}
                allLabel="All operators"
              />
              <Select
                value={actionDevice}
                onChange={setActionDevice}
                options={actionDevices}
                allLabel="All devices"
              />
            </>
          }
        >
          {controlActions.error ? (
            <ErrorNote error={controlActions.error} />
          ) : !controlActions.data ? (
            <Empty message="Loading…" />
          ) : filteredActions.length === 0 ? (
            <Empty message="No operator control writes recorded." />
          ) : (
            <Table head={["Time", "Device", "Action", "Operator", "Outcome"]}>
              {filteredActions.map((a, i) => (
                <tr key={`${a.ts}-${i}`}>
                  <td className="whitespace-nowrap px-4 py-2 text-xs">{fmtIso(a.ts)}</td>
                  <td className="px-4 py-2 text-xs font-medium text-ink dark:text-slate-100">
                    {a.device_id}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {a.method ? `${a.method} ` : ""}
                    {a.action ?? a.message ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs">{a.owner ?? "—"}</td>
                  <td className="px-4 py-2 text-xs">
                    <span
                      className={
                        a.outcome === "ok" || (a.status_code != null && a.status_code < 300)
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400"
                      }
                    >
                      {a.outcome ?? "—"}
                      {a.status_code != null ? ` (${a.status_code})` : ""}
                    </span>
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Tile>

        {/* ---- live claims -------------------------------------------------- */}
        <Tile
          title="Live claims"
          sub="Devices under a cooperative claim (details.claimed_by, live poll)."
        >
          {claims.length === 0 ? (
            <Empty message="No device is currently claimed." />
          ) : (
            <Table head={["Device", "Held by", "Session", "Claim expires"]}>
              {claims.map((c) => (
                <tr key={c.id}>
                  <td className="px-4 py-2 font-medium text-ink dark:text-slate-100">{c.name}</td>
                  <td className="px-4 py-2">{c.claimed.owner}</td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {c.claimed.session_id.slice(0, 12)}…
                  </td>
                  <td className="px-4 py-2">{fmtIso(c.claimed.expires_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Tile>

        {/* ---- live sessions ------------------------------------------------ */}
        <Tile title="Live sessions" sub="Unexpired session cookies (~12 h TTL).">
          {sessions.error ? (
            <ErrorNote error={sessions.error} />
          ) : !sessions.data ? (
            <Empty message="Loading…" />
          ) : sessions.data.sessions.length === 0 ? (
            <Empty message="Nobody is signed in." />
          ) : (
            <Table head={["Account", "Signed in", "Expires"]}>
              {sessions.data.sessions.map((r, i) => (
                <tr key={`${r.email}-${i}`}>
                  <td className="px-4 py-2">{r.email}</td>
                  <td className="px-4 py-2">{fmtEpoch(r.created_at)}</td>
                  <td className="px-4 py-2">{fmtEpoch(r.expires_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Tile>

        {/* ---- accounts (banner tile) --------------------------------------- */}
        <Tile
          wide
          title="Accounts"
          sub="The full roster.yaml allow-list (including disabled/expired). Edits go through roster.yaml, not this page."
        >
          {accounts.error ? (
            <ErrorNote error={accounts.error} />
          ) : !accounts.data ? (
            <Empty message="Loading…" />
          ) : (
            <>
              <Table head={["Name", "Email", "Role", "Status", "Group", "Last login", "Sessions", "Expires"]}>
                {accounts.data.users.map((u) => (
                  <tr
                    key={u.email}
                    className={u.status !== "active" || u.is_expired ? "opacity-50" : ""}
                  >
                    <td className="px-4 py-2 font-medium text-ink dark:text-slate-100">
                      {u.name || "—"}
                    </td>
                    <td className="px-4 py-2">{u.email}</td>
                    <td className="px-4 py-2">{u.role}</td>
                    <td className="px-4 py-2">
                      {u.is_expired ? "expired" : u.status}
                      {u.disabled_reason && (
                        <span className="ml-1 text-xs text-ink-subtle dark:text-slate-500">
                          ({u.disabled_reason})
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2">{u.lab_account || "—"}</td>
                    <td className="px-4 py-2">{fmtEpoch(u.last_login_at)}</td>
                    <td className="px-4 py-2">{u.active_sessions || "—"}</td>
                    <td className="px-4 py-2">{fmtEpoch(u.expires_at)}</td>
                  </tr>
                ))}
              </Table>
              {accounts.data.automation.length > 0 && (
                <>
                  <p className="border-t border-slate-100 px-4 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-ink-subtle dark:border-slate-800 dark:text-slate-500">
                    Automation (machine principals)
                  </p>
                  <Table head={["Email", "Name", "Approved", "Platform", "Active keys", "Expires"]}>
                    {accounts.data.automation.map((a) => (
                      <tr key={a.email} className={a.is_expired ? "opacity-50" : ""}>
                        <td className="px-4 py-2">{a.email}</td>
                        <td className="px-4 py-2">{a.name || "—"}</td>
                        <td className="px-4 py-2">
                          {a.approved ? (
                            "yes"
                          ) : (
                            <span className="font-medium text-amber-600 dark:text-amber-400">
                              pending
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2">{a.platform ?? "—"}</td>
                        <td className="px-4 py-2">{a.api_keys}</td>
                        <td className="px-4 py-2">{fmtEpoch(a.expires_at)}</td>
                      </tr>
                    ))}
                  </Table>
                </>
              )}
            </>
          )}
        </Tile>

        {/* ---- API keys ------------------------------------------------------ */}
        <Tile
          title="API keys"
          sub="Machine-principal keys; last_used_at separates dead keys from load-bearing ones."
        >
          {apiKeys.error ? (
            <ErrorNote error={apiKeys.error} />
          ) : !apiKeys.data ? (
            <Empty message="Loading…" />
          ) : apiKeys.data.keys.length === 0 ? (
            <Empty message="No API keys issued." />
          ) : (
            <Table head={["Account", "Label", "Last used", "Expires", "Status"]}>
              {apiKeys.data.keys.map((k) => (
                <tr key={k.id} className={k.revoked ? "opacity-50" : ""}>
                  <td className="px-4 py-2">{k.email}</td>
                  <td className="px-4 py-2">{k.label || `#${k.id}`}</td>
                  <td className="px-4 py-2">{fmtEpoch(k.last_used_at)}</td>
                  <td className="px-4 py-2">{fmtEpoch(k.expires_at)}</td>
                  <td className="px-4 py-2">{k.revoked ? "revoked" : "active"}</td>
                </tr>
              ))}
            </Table>
          )}
        </Tile>
      </div>
    </div>
  );
}
