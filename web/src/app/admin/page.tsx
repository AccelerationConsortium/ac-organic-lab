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
// Layout: a strict two-column ("double panel") grid of equal tiles, paired by
// topic — every row is two tiles, none spans the grid:
//
//   Overview (headline numbers)  |  Roster health (reload / approvals / expiry)
//   Accounts                     |  Automation & API keys
//   Live sessions                |  Live claims
//   Sign-in activity             |  Control actions
//
// To fit the wider tables into half-width tiles, related fields share one
// cell (name over email, role + grants as chips, status over expiry, device
// over action, outcome over duration). Tile bodies are fixed-height and scroll
// independently under a sticky table header, like the platform pages.
// ---------------------------------------------------------------------------

interface AdminGrant {
  scope: "global" | "platform" | "equipment";
  id?: string;
  role: string;
}

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
  grants: AdminGrant[];
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
  /** Wall-clock of the device interaction (claim → action → release), seconds.
   *  Null on rows written before 2026-07-24 and on refusals that never
   *  reached the device. */
  duration_s: number | null;
  /** Provenance of the click (X-Control-Origin). "assistant" when authorized
   *  from the lab assistant's confirm card; null for direct tile clicks. */
  origin: string | null;
}

interface ControlActionsResponse {
  /** Newest-first window (the `limit` we asked for). */
  actions: ControlAction[];
  /** Lifetime row count — the headline figure; not capped by `limit`. */
  total: number;
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

const SHORT_STAMP: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

/** ac_auth timestamps are epoch seconds (REAL). Full form, for tooltips. */
function fmtEpoch(t: number | null | undefined): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString();
}

/** Compact "Aug 23, 14:15" form for table cells; pair with a full `title`. */
function fmtEpochShort(t: number | null | undefined): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString(undefined, SHORT_STAMP);
}

function parseIso(ts: string): Date {
  return new Date(ts.endsWith("Z") || ts.includes("+") ? ts : ts + "Z");
}

/** lab.db timestamps are ISO-8601 UTC strings. Full form, for tooltips. */
function fmtIso(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = parseIso(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function fmtIsoShort(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = parseIso(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, SHORT_STAMP);
}

/** "2 d 4 h" / "3 h 12 m" / "45 m" / "30 s" — coarse on purpose (headline use). */
function fmtDuration(seconds: number): string {
  const s = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return h > 0 ? `${d} d ${h} h` : `${d} d`;
  if (h > 0) return m > 0 ? `${h} h ${m} m` : `${h} h`;
  if (m > 0) return `${m} m`;
  return `${Math.floor(s)} s`;
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
  controls,
  children,
}: {
  title: string;
  sub?: string;
  /** Optional header widgets (filter dropdowns etc.). */
  controls?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950/40">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink dark:text-slate-100">{title}</h2>
          {sub && <p className="mt-0.5 text-xs text-ink-subtle dark:text-slate-400">{sub}</p>}
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
        <tr className="text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-400">
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

/** Section caption inside a tile that stacks two tables. */
function Caption({ children }: { children: React.ReactNode }) {
  return (
    <p className="border-t border-slate-100 px-4 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-ink-subtle first:border-t-0 dark:border-slate-800 dark:text-slate-400">
      {children}
    </p>
  );
}

function Empty({ message }: { message: string }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-ink-muted dark:text-slate-300">{message}</p>
  );
}

function ErrorNote({ error }: { error: unknown }) {
  return (
    <p className="px-4 py-6 text-center text-sm text-rose-600 dark:text-rose-400">
      {error instanceof Error ? error.message : "Failed to load."}
    </p>
  );
}

/** Two-line cell: a primary line over a muted secondary line. */
function Stacked({
  primary,
  secondary,
  mono,
}: {
  primary: React.ReactNode;
  secondary?: React.ReactNode;
  /** Render the secondary line in monospace (ids, actions). */
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="truncate">{primary}</div>
      {secondary != null && secondary !== "" && (
        <div
          className={`truncate text-xs text-ink-subtle dark:text-slate-400 ${mono ? "font-mono" : ""}`}
        >
          {secondary}
        </div>
      )}
    </div>
  );
}

// One chip per grant. Platform scope reads `hte · platform` (one grant, every
// device in the section); equipment scope is just the device id. A flat
// role (admin/operator) is an implicit global grant, shown as its own chip so
// the column never reads empty for an account that can in fact reach devices.
function GrantChips({ role, grants }: { role: string; grants: AdminGrant[] }) {
  const chip = (key: string, label: string, cls: string, title: string) => (
    <span
      key={key}
      className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
      title={title}
    >
      {label}
    </span>
  );
  const chips: React.ReactNode[] = [];
  if (role !== "none") {
    chips.push(
      chip(
        "flat",
        `all · ${role}`,
        "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
        `flat role "${role}" — implicit global grant on every equipment`,
      ),
    );
  }
  for (const g of grants) {
    const label =
      g.scope === "platform" ? `${g.id} · platform` : g.scope === "global" ? `all · ${g.role}` : g.id ?? "?";
    const cls =
      g.scope === "platform"
        ? "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
        : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
    chips.push(chip(`${g.scope}:${g.id ?? "*"}`, label, cls, `${g.scope} grant · role ${g.role}`));
  }
  if (chips.length === 0) {
    return (
      <span className="text-xs text-ink-subtle dark:text-slate-400" title="Sign-in only: public reads, no device control">
        login only
      </span>
    );
  }
  return <div className="flex max-w-xs flex-wrap gap-1">{chips}</div>;
}

/** The "agent" marker on machine principals listed among users. */
function AgentChip() {
  return (
    <span
      className="ml-1.5 whitespace-nowrap rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
      title="Machine principal: exists so devices can resolve this claim-owner string to a role. Cannot sign in."
    >
      agent
    </span>
  );
}

/**
 * One cell of the Overview KPI row: sentence-case label, a semibold value in
 * the page sans (proportional figures — these are not a column), and a short
 * muted detail line that says what the number is over.
 */
function Stat({
  label,
  value,
  detail,
  title,
}: {
  label: string;
  value: string;
  detail?: string;
  title?: string;
}) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="truncate text-xs text-ink-subtle dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 truncate text-2xl font-semibold leading-tight text-ink dark:text-slate-100">
        {value}
      </dd>
      {detail && (
        <dd className="mt-0.5 text-[11px] leading-snug text-ink-muted dark:text-slate-300">{detail}</dd>
      )}
    </div>
  );
}

/** One row of the Roster health list: label on the left, content on the right. */
function HealthRow({
  label,
  tone,
  children,
}: {
  label: string;
  tone?: "ok" | "warn" | "bad";
  children: React.ReactNode;
}) {
  const dot =
    tone === "bad"
      ? "bg-rose-500"
      : tone === "warn"
        ? "bg-amber-500"
        : tone === "ok"
          ? "bg-emerald-500"
          : "bg-slate-300 dark:bg-slate-600";
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-x-3 px-4 py-2.5 text-sm">
      <dt className="flex items-start gap-2 text-xs text-ink-subtle dark:text-slate-400">
        <span className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden />
        <span className="leading-5">{label}</span>
      </dt>
      <dd className="min-w-0 text-ink dark:text-slate-200">{children}</dd>
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
  const controlActions = useAdmin<ControlActionsResponse>(
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

  // Headline "session time": signed-in time summed over the *live* sessions
  // (now − created_at). Ended sessions leave no duration record — logout
  // deletes the row and expiry purges it — so a lifetime figure would be a
  // guess; this one is exact for what is on screen. `now` is taken per render;
  // the sessions query refetches every 30 s, which re-renders.
  const nowS = Date.now() / 1000;
  const liveSessions = sessions.data?.sessions ?? [];
  const sessionSeconds = liveSessions.reduce(
    (acc, r) => acc + Math.max(0, nowS - r.created_at),
    0,
  );
  const sessionAccounts = new Set(liveSessions.map((r) => r.email)).size;

  if (loading) {
    return <div className="mt-6 h-24 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;
  }
  if (!isAdmin) {
    // The middleware redirects non-admins server-side; this covers client-side
    // navigations and sessions that expire while the page is open.
    return (
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
        <p className="text-sm font-medium text-ink-muted dark:text-slate-300">
          Admin console — sign in with an admin account to view this page.
        </p>
      </div>
    );
  }

  const s = state.data;
  const lastReload = s?.last_reload;
  const rosterTotal = s ? s.roster.users + s.roster.automation : null;

  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-2">
      {/* ================================================================== */}
      {/* Row 1 — Overview | Roster health                                     */}
      {/* ================================================================== */}

      <Tile
        title="Overview"
        sub="Headline numbers — the roster, who is signed in, what is claimed, how much has been done."
      >
        {state.error ? (
          <ErrorNote error={state.error} />
        ) : (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-5 px-4 py-4 sm:grid-cols-3">
            <Stat
              label="Active accounts"
              value={s ? String(s.roster.active_accounts) : "…"}
              detail={s ? `of ${rosterTotal} on the roster` : undefined}
              title="Roster principals that are enabled, unexpired and (for automation) approved"
            />
            <Stat
              label="Projects"
              value={s ? String(s.roster.projects) : "…"}
              detail="declared in roster.yaml"
            />
            <Stat
              label="Live sessions"
              value={sessions.data ? String(liveSessions.length) : "…"}
              detail={
                sessions.data
                  ? `${sessionAccounts} ${sessionAccounts === 1 ? "account" : "accounts"} signed in`
                  : undefined
              }
              title="Unexpired session cookies (~12 h TTL)"
            />
            <Stat
              label="Devices claimed"
              value={equipment.data ? String(claims.length) : "…"}
              detail={
                equipment.data ? `live · of ${equipment.data.equipment.length} polled` : undefined
              }
              title="Devices whose live /status carries details.claimed_by"
            />
            <Stat
              label="Control actions"
              value={
                controlActions.data
                  ? controlActions.data.total.toLocaleString()
                  : controlActions.error
                    ? "—"
                    : "…"
              }
              detail="all time, via the dashboard"
              title="Lifetime count of control_action audit rows in lab.db"
            />
            <Stat
              label="Session time"
              value={sessions.data ? fmtDuration(sessionSeconds) : "…"}
              detail="live sessions, summed"
              title="Σ (now − signed in) across unexpired sessions; ended sessions keep no duration record"
            />
          </dl>
        )}
      </Tile>

      <Tile
        title="Roster health"
        sub="roster.yaml as loaded by the auth sidecar — reloads, approvals waiting on an admin, accounts about to lapse."
      >
        {state.error ? (
          <ErrorNote error={state.error} />
        ) : !s ? (
          <Empty message="Loading…" />
        ) : (
          <dl className="divide-y divide-slate-100 dark:divide-slate-800">
            <HealthRow label="Roster loaded" tone="ok">
              <span title={fmtEpoch(s.roster_loaded_at)}>{fmtEpochShort(s.roster_loaded_at)}</span>
              <span className="ml-2 text-xs text-ink-subtle dark:text-slate-400">
                {s.roster.users} users · {s.roster.automation} automation · {s.roster.projects}{" "}
                projects
              </span>
            </HealthRow>

            <HealthRow
              label="Last reload"
              tone={lastReload == null ? undefined : lastReload.applied ? "ok" : "bad"}
            >
              {lastReload == null ? (
                <span className="text-ink-muted dark:text-slate-300">none since start</span>
              ) : lastReload.applied ? (
                <span>
                  applied{" "}
                  <span className="text-xs text-ink-subtle dark:text-slate-400" title={fmtEpoch(lastReload.ts)}>
                    {fmtEpochShort(lastReload.ts)}
                  </span>
                </span>
              ) : (
                <div className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-rose-800 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
                  <p className="font-medium">
                    REJECTED {fmtEpochShort(lastReload.ts)} — the previous allow-list is still in
                    effect.
                  </p>
                  <ul className="mt-1 list-inside list-disc text-xs">
                    {lastReload.errors.map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}
            </HealthRow>

            <HealthRow
              label="Pending automation"
              tone={s.pending_automation.length > 0 ? "warn" : "ok"}
            >
              {s.pending_automation.length === 0 ? (
                <span className="text-ink-muted dark:text-slate-300">none awaiting approval</span>
              ) : (
                <ul className="space-y-0.5 text-amber-700 dark:text-amber-400">
                  {s.pending_automation.map((e) => (
                    <li key={e} className="truncate font-medium">
                      {e}
                    </li>
                  ))}
                </ul>
              )}
            </HealthRow>

            <HealthRow
              label="Expiring ≤ 30 days"
              tone={s.expiring_soon.length > 0 ? "warn" : "ok"}
            >
              {s.expiring_soon.length === 0 ? (
                <span className="text-ink-muted dark:text-slate-300">nothing lapsing soon</span>
              ) : (
                <ul className="space-y-0.5">
                  {s.expiring_soon.map((e) => (
                    <li key={e.email} className="flex justify-between gap-3">
                      <span className="truncate text-amber-700 dark:text-amber-400">{e.email}</span>
                      <span
                        className="shrink-0 text-xs text-ink-subtle dark:text-slate-400"
                        title={fmtEpoch(e.expires_at)}
                      >
                        {fmtEpochShort(e.expires_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </HealthRow>
          </dl>
        )}
      </Tile>

      {/* ================================================================== */}
      {/* Row 2 — Accounts | Automation & API keys                            */}
      {/* ================================================================== */}

      <Tile
        title="Accounts"
        sub="The full roster.yaml allow-list, incl. disabled/expired. Access = what each account may use. Edits go through roster.yaml, not this page."
      >
        {accounts.error ? (
          <ErrorNote error={accounts.error} />
        ) : !accounts.data ? (
          <Empty message="Loading…" />
        ) : accounts.data.users.length === 0 ? (
          <Empty message="No human accounts on the roster." />
        ) : (
          <Table head={["Account", "Access", "Status", "Last login"]}>
            {accounts.data.users.map((u) => {
              const inactive = u.status !== "active" || u.is_expired;
              const isAgent = u.email.startsWith("agent:");
              return (
                <tr key={u.email} className={inactive ? "opacity-50" : ""}>
                  <td className="max-w-[14rem] px-4 py-2">
                    <Stacked
                      primary={
                        <span className="font-medium text-ink dark:text-slate-100">
                          {u.name || u.email}
                          {isAgent && <AgentChip />}
                        </span>
                      }
                      secondary={
                        u.name
                          ? `${u.email}${u.lab_account ? ` · ${u.lab_account}` : ""}`
                          : u.lab_account || undefined
                      }
                    />
                  </td>
                  <td className="px-4 py-2">
                    <GrantChips role={u.role} grants={u.grants ?? []} />
                  </td>
                  <td className="px-4 py-2">
                    <Stacked
                      primary={
                        <>
                          {u.is_expired ? "expired" : u.status}
                          {u.disabled_reason && (
                            <span className="ml-1 text-xs text-ink-subtle dark:text-slate-400">
                              ({u.disabled_reason})
                            </span>
                          )}
                        </>
                      }
                      secondary={
                        u.expires_at != null ? (
                          <span title={fmtEpoch(u.expires_at)}>
                            {u.is_expired ? "expired" : "expires"} {fmtEpochShort(u.expires_at)}
                          </span>
                        ) : undefined
                      }
                    />
                  </td>
                  <td className="whitespace-nowrap px-4 py-2">
                    <Stacked
                      primary={
                        <span title={fmtEpoch(u.last_login_at)}>{fmtEpochShort(u.last_login_at)}</span>
                      }
                      secondary={
                        u.active_sessions
                          ? `${u.active_sessions} live ${u.active_sessions === 1 ? "session" : "sessions"}`
                          : undefined
                      }
                    />
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Tile>

      <Tile
        title="Automation & API keys"
        sub="Machine principals and how they authenticate (X-Api-Key; humans use sessions). last_used_at separates dead keys from load-bearing ones."
      >
        {accounts.error || apiKeys.error ? (
          <ErrorNote error={accounts.error ?? apiKeys.error} />
        ) : !accounts.data || !apiKeys.data ? (
          <Empty message="Loading…" />
        ) : (
          <>
            <Caption>Automation accounts</Caption>
            {accounts.data.automation.length === 0 ? (
              <Empty message="No automation accounts on the roster." />
            ) : (
              <Table head={["Account", "Approved", "Platform", "Keys", "Expires"]}>
                {accounts.data.automation.map((a) => (
                  <tr key={a.email} className={a.is_expired ? "opacity-50" : ""}>
                    <td className="max-w-[14rem] px-4 py-2">
                      <Stacked
                        primary={
                          <span className="font-medium text-ink dark:text-slate-100">
                            {a.name || a.email}
                          </span>
                        }
                        secondary={a.name ? a.email : undefined}
                      />
                    </td>
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
                    <td className="px-4 py-2 tabular-nums">{a.api_keys}</td>
                    <td className="whitespace-nowrap px-4 py-2" title={fmtEpoch(a.expires_at)}>
                      {fmtEpochShort(a.expires_at)}
                    </td>
                  </tr>
                ))}
              </Table>
            )}

            <Caption>API keys</Caption>
            {apiKeys.data.keys.length === 0 ? (
              <Empty message="No API keys issued." />
            ) : (
              <Table head={["Account", "Label", "Last used", "Expires", "Status"]}>
                {apiKeys.data.keys.map((k) => (
                  <tr key={k.id} className={k.revoked ? "opacity-50" : ""}>
                    <td className="max-w-[14rem] truncate px-4 py-2" title={k.email}>
                      {k.email}
                    </td>
                    <td className="px-4 py-2">{k.label || `#${k.id}`}</td>
                    <td className="whitespace-nowrap px-4 py-2" title={fmtEpoch(k.last_used_at)}>
                      {fmtEpochShort(k.last_used_at)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2" title={fmtEpoch(k.expires_at)}>
                      {fmtEpochShort(k.expires_at)}
                    </td>
                    <td className="px-4 py-2">{k.revoked ? "revoked" : "active"}</td>
                  </tr>
                ))}
              </Table>
            )}
          </>
        )}
      </Tile>

      {/* ================================================================== */}
      {/* Row 3 — Live sessions | Live claims                                  */}
      {/* ================================================================== */}

      <Tile title="Live sessions" sub="Unexpired session cookies (~12 h TTL).">
        {sessions.error ? (
          <ErrorNote error={sessions.error} />
        ) : !sessions.data ? (
          <Empty message="Loading…" />
        ) : liveSessions.length === 0 ? (
          <Empty message="Nobody is signed in." />
        ) : (
          <Table head={["Account", "Signed in", "Elapsed", "Expires"]}>
            {liveSessions.map((r, i) => (
              <tr key={`${r.email}-${i}`}>
                <td className="max-w-[14rem] truncate px-4 py-2" title={r.email}>
                  {r.email}
                </td>
                <td className="whitespace-nowrap px-4 py-2" title={fmtEpoch(r.created_at)}>
                  {fmtEpochShort(r.created_at)}
                </td>
                <td className="whitespace-nowrap px-4 py-2 tabular-nums text-ink-muted dark:text-slate-300">
                  {fmtDuration(nowS - r.created_at)}
                </td>
                <td className="whitespace-nowrap px-4 py-2" title={fmtEpoch(r.expires_at)}>
                  {fmtEpochShort(r.expires_at)}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Tile>

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
                <td className="px-4 py-2">
                  <Stacked
                    primary={
                      <span className="font-medium text-ink dark:text-slate-100">{c.name}</span>
                    }
                    secondary={c.id}
                    mono
                  />
                </td>
                <td className="px-4 py-2">{c.claimed.owner}</td>
                <td className="px-4 py-2 font-mono text-xs" title={c.claimed.session_id}>
                  {c.claimed.session_id.slice(0, 12)}…
                </td>
                <td className="whitespace-nowrap px-4 py-2" title={fmtIso(c.claimed.expires_at)}>
                  {fmtIsoShort(c.claimed.expires_at)}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Tile>

      {/* ================================================================== */}
      {/* Row 4 — Sign-in activity | Control actions                          */}
      {/* ================================================================== */}

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
                <td className="whitespace-nowrap px-4 py-2 text-xs" title={fmtEpoch(e.ts)}>
                  {fmtEpochShort(e.ts)}
                </td>
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
                <td className="max-w-[12rem] truncate px-4 py-2 text-xs" title={e.email || undefined}>
                  {e.email || "—"}
                </td>
                <td className="px-4 py-2 font-mono text-xs" title={e.user_agent}>
                  {e.ip || "—"}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Tile>

      <Tile
        title="Control actions"
        sub={
          controlActions.data
            ? `Dashboard-mediated device writes — who, what, how the device answered. Latest ${controlActions.data.actions.length} of ${controlActions.data.total.toLocaleString()}.`
            : "Dashboard-mediated device writes — who, what, how the device answered."
        }
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
          <Table head={["Time", "Device · action", "Operator", "Outcome"]}>
            {filteredActions.map((a, i) => {
              const ok = a.outcome === "ok" || (a.status_code != null && a.status_code < 300);
              return (
                <tr key={`${a.ts}-${i}`}>
                  <td className="whitespace-nowrap px-4 py-2 text-xs" title={fmtIso(a.ts)}>
                    {fmtIsoShort(a.ts)}
                  </td>
                  <td className="max-w-[14rem] px-4 py-2">
                    <Stacked
                      primary={
                        <span className="text-xs font-medium text-ink dark:text-slate-100">
                          {a.device_id}
                        </span>
                      }
                      secondary={`${a.method ? `${a.method} ` : ""}${a.action ?? a.message ?? "—"}`}
                      mono
                    />
                  </td>
                  <td className="max-w-[12rem] px-4 py-2 text-xs">
                    <Stacked
                      primary={<span title={a.owner ?? undefined}>{a.owner ?? "—"}</span>}
                      secondary={
                        a.origin === "assistant" || a.origin === "assistant-plan" ? (
                          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium text-purple-800 dark:bg-purple-950/50 dark:text-purple-200">
                            {a.origin}
                          </span>
                        ) : (
                          "tile"
                        )
                      }
                    />
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-xs">
                    <Stacked
                      primary={
                        <span
                          className={
                            ok
                              ? "text-emerald-600 dark:text-emerald-400"
                              : "text-rose-600 dark:text-rose-400"
                          }
                        >
                          {a.outcome ?? "—"}
                          {a.status_code != null ? ` (${a.status_code})` : ""}
                        </span>
                      }
                      secondary={
                        a.duration_s != null ? (
                          <span
                            className="tabular-nums"
                            title="Wall-clock of the device interaction (claim → action → release) as seen from the dashboard"
                          >
                            {a.duration_s.toFixed(1)} s
                          </span>
                        ) : undefined
                      }
                    />
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Tile>
    </div>
  );
}
