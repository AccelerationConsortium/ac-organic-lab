"use client";

import Link from "next/link";
import { useMemo } from "react";

import {
  fmtDuration,
  useAdminQuery,
  type AdminState,
  type ClaimedBy,
  type ControlActionsResponse,
  type SessionRow,
} from "@/lib/admin-api";
import { useEquipmentList } from "@/lib/use-equipment";
import { useUserAuth } from "@/lib/user-auth";
import { AdminTile, ErrorNote, Stat } from "./AdminTile";

/**
 * "Accounts & Activities" — the admin headline numbers as one KPI tile:
 * active accounts, projects, live sessions, devices claimed, lifetime control
 * actions, and signed-in time. Leads the admin console, and the Overview page
 * mounts it at the top for admins (`wide`, with a GO → link into /admin).
 *
 * Renders nothing for a non-admin viewer: the roster/session figures come from
 * the sidecar's admin-only endpoints, and the Admin tab is hidden for them too.
 */
export function AccountsActivitiesTile({
  wide = false,
  adminLink = false,
}: {
  /** Lay the six stats out in one row on wide screens (Overview banner). */
  wide?: boolean;
  /** Show a GO → link to the admin console in the header. */
  adminLink?: boolean;
}) {
  const { authenticated, identity } = useUserAuth();
  const isAdmin = authenticated && identity?.role === "admin";

  const state = useAdminQuery<AdminState>("/api/admin/state", 60_000, isAdmin);
  const sessions = useAdminQuery<{ sessions: SessionRow[] }>(
    "/api/admin/sessions",
    30_000,
    isAdmin,
  );
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

  return (
    <AdminTile
      title="Accounts & Activities"
      sub="Headline numbers — the roster, who is signed in, what is claimed, how much has been done."
      frame={false}
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
        <dl
          className={`grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 ${wide ? "lg:grid-cols-6" : ""}`}
        >
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
            value={equipment.data ? String(claimed) : "…"}
            detail={equipment.data ? `live · of ${equipment.data.equipment.length} polled` : undefined}
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
    </AdminTile>
  );
}
