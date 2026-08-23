"use client";

import Link from "next/link";
import { useMemo } from "react";

import {
  fmtDuration,
  useAdminQuery,
  type AdminState,
  type ClaimedBy,
  type ControlActionsResponse,
  type SessionsResponse,
} from "@/lib/admin-api";
import { useEquipmentList } from "@/lib/use-equipment";
import { useUserAuth } from "@/lib/user-auth";
import { AdminTile, ErrorNote, Stat } from "./AdminTile";

/**
 * "Accounts & Activities" — the admin headline numbers as one half-width,
 * double-column KPI tile: two columns of stats, each cell a stacked pair, so
 * the eight figures read as
 *
 *   Active accounts   | Equipment
 *   Live sessions     | Equipment claimed
 *   Projects          | Control actions
 *   Session time      | Total session time
 *
 * It leads the admin console (paired with Roster health, same footprint as
 * every other panel) and the Overview page mounts the same tile as the first
 * card of its masonry with a GO → link into /admin.
 *
 * Renders nothing for a non-admin viewer: the roster/session figures come from
 * the sidecar's admin-only endpoints, and the Admin tab is hidden for them too.
 */
export function AccountsActivitiesTile({
  adminLink = false,
  className,
}: {
  /** Show a GO → link to the admin console in the header. */
  adminLink?: boolean;
  /** Extra classes on the card. */
  className?: string;
}) {
  const { authenticated, identity } = useUserAuth();
  const isAdmin = authenticated && identity?.role === "admin";

  const state = useAdminQuery<AdminState>("/api/admin/state", 60_000, isAdmin);
  const sessions = useAdminQuery<SessionsResponse>("/api/admin/sessions", 30_000, isAdmin);
  // Only the lifetime `total` is needed here; the audit table on the admin
  // page fetches its own window.
  const controlActions = useAdminQuery<ControlActionsResponse>(
    "/api/history/control-actions?limit=1",
    30_000,
    isAdmin,
  );
  const equipment = useEquipmentList();

  const claimed = useMemo(() => {
    const items = equipment.data?.equipment ?? [];
    return items.filter(
      (e) => (e.status?.details as { claimed_by?: ClaimedBy } | undefined)?.claimed_by != null,
    ).length;
  }, [equipment.data]);

  if (!isAdmin) return null;

  const s = state.data;
  const rosterTotal = s ? s.roster.users + s.roster.automation : null;

  // Signed-in time summed over the *live* sessions (now − created_at). Ended
  // sessions leave no duration record — logout deletes the row and expiry
  // purges it — so a lifetime figure would be a guess; this one is exact for
  // what is on screen. `now` is per render; the 30 s refetch re-renders.
  const nowS = Date.now() / 1000;
  const liveSessions = sessions.data?.sessions ?? [];
  const sessionSeconds = liveSessions.reduce((acc, r) => acc + Math.max(0, nowS - r.created_at), 0);
  const sessionAccounts = new Set(liveSessions.map((r) => r.email)).size;

  const totalTime = sessions.data?.total_time_s;

  // Four stacked pairs laid out two-across ("double column"): pair 1 | pair 2
  // on the first band, pair 3 | pair 4 below. Each pair is its own flex stack
  // so the above/below relation survives every breakpoint.
  const pairs: [React.ReactNode, React.ReactNode][] = [
    [
      <Stat
        key="accounts"
        label="Active accounts"
        value={s ? String(s.roster.active_accounts) : "…"}
        detail={s ? `of ${rosterTotal} on the roster` : undefined}
        title="Roster principals that are enabled, unexpired and (for automation) approved"
      />,
      <Stat
        key="sessions"
        label="Live sessions"
        value={sessions.data ? String(liveSessions.length) : "…"}
        detail={
          sessions.data
            ? `${sessionAccounts} ${sessionAccounts === 1 ? "account" : "accounts"} signed in`
            : undefined
        }
        title="Unexpired session cookies (~12 h TTL)"
      />,
    ],
    [
      <Stat
        key="equipment"
        label="Equipment"
        value={equipment.data ? String(equipment.data.equipment.length) : "…"}
        detail="registered & polled"
        title="Entries in equipment.yaml the aggregator polls"
      />,
      <Stat
        key="claimed"
        label="Equipment claimed"
        value={equipment.data ? String(claimed) : "…"}
        detail="live, holding a claim"
        title="Devices whose live /status carries details.claimed_by"
      />,
    ],
    [
      <Stat
        key="projects"
        label="Projects"
        value={s ? String(s.roster.projects) : "…"}
        detail="declared in roster.yaml"
      />,
      <Stat
        key="session-time"
        label="Session time"
        value={sessions.data ? fmtDuration(sessionSeconds) : "…"}
        detail="live sessions, summed"
        title="Σ (now − signed in) across unexpired sessions"
      />,
    ],
    [
      <Stat
        key="control"
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
      />,
      <Stat
        key="total-time"
        label="Total session time"
        value={sessions.data ? (totalTime != null ? fmtDuration(totalTime) : "—") : "…"}
        detail="all time, all accounts"
        title="Signed-in time reconstructed from the auth_events log — union of session windows per account (concurrent sessions counted once, logins assumed to run their TTL unless logged out). '—' until the auth sidecar serves total_time_s."
      />,
    ],
  ];

  return (
    <AdminTile
      title="Accounts & Activities"
      sub="Headline numbers — the roster, who is signed in, what is claimed, how much has been done."
      frame={false}
      className={className}
      controls={
        adminLink ? (
          <Link
            href="/admin"
            className="text-xs font-medium text-orange-600 hover:underline dark:text-orange-400"
          >
            GO →
          </Link>
        ) : undefined
      }
    >
      {state.error ? (
        <ErrorNote error={state.error} />
      ) : (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-5">
          {pairs.map((pair, i) => (
            <div key={i} className="flex flex-col gap-4">
              {pair}
            </div>
          ))}
        </dl>
      )}
    </AdminTile>
  );
}
