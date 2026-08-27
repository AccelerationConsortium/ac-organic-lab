"use client";

import Link from "next/link";
import { useMemo } from "react";

import {
  fmtDuration,
  useAdminQuery,
  type ClaimedBy,
  type ControlActionsResponse,
  type OverviewSessions,
  type OverviewState,
} from "@/lib/admin-api";
import { useEquipmentList } from "@/lib/use-equipment";
import { useUserAuth } from "@/lib/user-auth";
import { AdminTile, ErrorNote, Stat } from "./AdminTile";

/**
 * "Accounts & Activities" — the headline numbers in two lines of four stats
 * (four columns, each a stacked pair, so the vertical pairing holds):
 *
 *   Active accounts | Equipment         | Projects        | Control actions
 *   Live sessions   | Equipment claimed | Current session | Total session
 *
 * It leads the admin console (paired with Roster health, same footprint as
 * every other panel) and the Overview page mounts the same tile as the first
 * card of its masonry with a GO → link into /admin for admins.
 *
 * Names the tile after the aggregate data source it uses: the roster/session
 * figures come from sidecar /overview/* endpoints, which any signed-in user
 * may read (they carry counts only — no account listing). The fuller /admin/*
 * endpoints stay admin-only and back the admin console. Renders nothing for a
 * signed-out viewer (the figures require a session).
 */
export function AccountsActivitiesTile({
  adminLink = false,
  className,
}: {
  /** Show a GO → link to the admin console in the header (admins only). */
  adminLink?: boolean;
  /** Extra classes on the card. */
  className?: string;
}) {
  const { authenticated, identity } = useUserAuth();
  const isAdmin = authenticated && identity?.role === "admin";

  const state = useAdminQuery<OverviewState>("/api/overview/state", 60_000, authenticated);
  const sessions = useAdminQuery<OverviewSessions>(
    "/api/overview/sessions",
    30_000,
    authenticated,
  );
  // Only the lifetime `total` is needed here; the audit table on the admin
  // page fetches its own window.
  const controlActions = useAdminQuery<ControlActionsResponse>(
    "/api/history/control-actions?limit=1",
    30_000,
    authenticated,
  );
  const equipment = useEquipmentList();

  const claimed = useMemo(() => {
    const items = equipment.data?.equipment ?? [];
    return items.filter(
      (e) => (e.status?.details as { claimed_by?: ClaimedBy } | undefined)?.claimed_by != null,
    ).length;
  }, [equipment.data]);

  if (!authenticated) return null;

  const s = state.data;
  const rosterTotal = s ? s.roster.users + s.roster.automation : null;

  // Signed-in time summed over the *live* sessions (now − created_at). Ended
  // sessions leave no duration record — logout deletes the row and expiry
  // purges it — so a lifetime figure would be a guess; this one is exact for
  // what is on screen. The sidecar computes it against a single `now`.
  const live = sessions.data?.live;
  const liveCount = live?.count ?? 0;
  const sessionAccounts = live?.accounts;
  const sessionSeconds = live?.seconds;

  const totalTime = sessions.data?.total_time_s;

  // Four stacked pairs laid out four-across — two lines of four stats. Each
  // pair is its own flex stack so the above/below relation survives every
  // breakpoint (narrow screens fall back to two pairs per band).
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
        value={sessions.data ? String(liveCount) : "…"}
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
        label="Current session"
        value={sessions.data ? fmtDuration(sessionSeconds ?? 0) : "…"}
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
        detail="all time, dashboard"
        title="Lifetime count of control_action audit rows in lab.db"
      />,
      <Stat
        key="total-time"
        label="Total session"
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
        <dl className="grid grid-cols-2 gap-x-5 gap-y-5 sm:grid-cols-4">
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
