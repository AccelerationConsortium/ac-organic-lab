import type { EquipmentSnapshot } from "@/types/api";
import { AuthGatedLink } from "@/components/AuthGatedLink";
import { StalenessIndicator } from "@/components/StalenessIndicator";
import { STATE_META, effectiveState } from "@/lib/state-meta";

/**
 * The lab's host machines — the servers and device PCs the equipment services
 * run on, rendered in the same tile chrome as the printer grid. This is a
 * hand-maintained inventory (hosts are not equipment, so they have no
 * registry entry); keep it in sync with DEVICE_PC_SETUP.md §7.
 *
 * What a machine offers is rendered as **capability chips**, color-coded by
 * kind (legend in the panel header):
 *
 *   - sky      `service`   — a lab service process listening on the machine
 *   - violet   `ops`       — the sdl-lab-hostops agent surface (whitelisted
 *                            MCP: status / logs / restarts, never a shell)
 *   - emerald  `equipment` — hardware this machine's services front
 *
 * (An amber `bridge` kind existed briefly for the UPLC PC's OT-2 USB
 * portproxy; the bridge was retired 2026-08-27 — see ROADMAP → the campus
 * Wi-Fi outage entry — and the kind went with it.)
 *
 * Chip `title` tooltips carry the detail the old prose rows held (ports,
 * caveats, what the ops API actually offers) — hover for it.
 *
 * `opsId` marks a host running the `sdl-lab-hostops` agent and names its
 * equipment.yaml id; those tiles carry the agent's live status pill (the
 * hostops entries stay registered and polled — they just render here instead
 * of on the Services card).
 *
 * Each `id` below must match an entry in `api/app/ssh_console.py::SSH_HOSTS`
 * — that id is the `/utils/computers/ssh/<id>` route the "SSH terminal" link
 * opens, and the server looks the ssh target up by it. The link is
 * admin-only, matching the gate in `web/src/middleware.ts`; a non-admin sees
 * no link at all.
 */

type CapKind = "service" | "ops" | "equipment";

type Capability = {
  label: string;
  kind: CapKind;
  /** Hover detail (port, caveat, what the surface offers). */
  title?: string;
};

type LabHost = {
  id: string;
  name: string;
  kind: string; // subtitle label, e.g. "Windows PC"
  hostname: string;
  /** What the machine offers, rendered as color-coded chips in this order. */
  capabilities: Capability[];
  opsId?: string;
};

// Chip tints per capability kind. Same palette families the pill system uses
// (lib/pill.ts) so the chips read as part of one design system; lighter fills
// because these are informational, not interactive.
const CAP_STYLE: Record<CapKind, string> = {
  service:
    "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-200",
  ops: "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-800 dark:bg-violet-900/30 dark:text-violet-200",
  equipment:
    "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200",
};

const CAP_LEGEND: { kind: CapKind; label: string }[] = [
  { kind: "service", label: "service" },
  { kind: "ops", label: "lab-ops agent" },
  { kind: "equipment", label: "controls equipment" },
];

const HOSTOPS_TITLE =
  "sdl-lab-hostops — whitelisted host-ops MCP (bearer token, :8060): service status · logs · restart (whitelisted subset) · serial-port enumeration · local /status probes. Never a shell.";

const LAB_HOSTS: LabHost[] = [
  {
    id: "gaia",
    name: "Central Server (gaia)",
    kind: "Linux server",
    hostname: "sdl2-server-gaia",
    capabilities: [
      { label: "dashboard web :8000", kind: "service" },
      { label: "dashboard API :8001", kind: "service" },
      { label: "auth edge", kind: "service", title: "Caddy single edge + ac_auth sidecar (:8009)" },
      { label: "kasa-tapo gateway", kind: "service", title: "Camera/plug gateway (:8002) + go2rtc streams" },
      { label: "Uptime Kuma", kind: "service" },
      { label: "PyPoe", kind: "service" },
      { label: "Hermes", kind: "service" },
      { label: "AnaliticaDB", kind: "service", title: "ELN + LIMS record layer (:8010)" },
      { label: "Bitácora", kind: "service", title: "Agentic ELN (:3001)" },
      { label: "Tapo cameras", kind: "equipment", title: "Via the kasa-tapo gateway" },
      { label: "Kasa plugs / strips", kind: "equipment", title: "Via the kasa-tapo gateway" },
    ],
  },
  {
    id: "cytation-pc",
    name: "Cytation PC",
    kind: "Windows PC",
    hostname: "sdl2-pc-03-cytation",
    capabilities: [
      { label: "xarm :8000", kind: "service" },
      { label: "plateloc :8010", kind: "service" },
      { label: "ot2-gateway-hte :8020", kind: "service" },
      { label: "ot2-gateway-complexation :8021", kind: "service" },
      { label: "torry-pines-shaker :8030", kind: "service" },
      { label: "cytation :8040", kind: "service" },
      { label: "biostack4 :8050", kind: "service" },
      { label: "hostops :8060", kind: "ops", title: HOSTOPS_TITLE },
      { label: "xArm5", kind: "equipment", title: "Arm, gripper, linear track" },
      { label: "PlateLoc sealer", kind: "equipment" },
      { label: "OT-2 (HTE)", kind: "equipment" },
      { label: "OT-2 (complexation)", kind: "equipment" },
      { label: "Torrey Pines shaker", kind: "equipment" },
      { label: "Cytation 5 reader", kind: "equipment" },
      { label: "BioStack stacker", kind: "equipment" },
    ],
    opsId: "hostops_cytation_pc",
  },
  {
    id: "uplc-pc",
    name: "UPLC PC",
    kind: "Windows PC",
    hostname: "sdl2-pc-06-uplc",
    capabilities: [
      { label: "hplc-ms-status :8010", kind: "service", title: "UPLC-MS sidecar — owns the run queue; production runs from branch fix_server_vial" },
      { label: "hostops :8060", kind: "ops", title: HOSTOPS_TITLE },
      { label: "Agilent UPLC-MS", kind: "equipment", title: "Via OpenLab CDS" },
    ],
    opsId: "hostops_uplc_pc",
  },
];

// Mirrors TileShell's card chrome so these tiles sit flush with the live
// equipment tiles in the printer grid below.
const TILE_CARD =
  "flex h-full flex-col gap-2 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900";

function CapChip({ cap }: { cap: Capability }) {
  return (
    <span
      title={cap.title}
      data-kind={cap.kind}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${CAP_STYLE[cap.kind]}`}
    >
      {cap.label}
    </span>
  );
}

function HostTile({
  host,
  snapshot,
}: {
  host: LabHost;
  snapshot: EquipmentSnapshot | undefined;
}) {
  return (
    <article className={TILE_CARD}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="truncate text-sm font-semibold text-ink dark:text-slate-100">
            {host.name}
          </h3>
          <p className="truncate text-xs text-ink-subtle dark:text-slate-400">
            <span className="uppercase">{host.kind}</span> ·{" "}
            <span className="font-mono">{host.hostname}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {host.opsId &&
            (snapshot ? (
              // STATE_META, not StatusPill: effectiveState can return the
              // presentation-only "unreachable", which lib/format's maps lack.
              <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATE_META[effectiveState(snapshot)].badge}`}
              >
                {STATE_META[effectiveState(snapshot)].label}
              </span>
            ) : (
              <span className="text-[10px] text-ink-subtle dark:text-slate-400">
                no data
              </span>
            ))}
        </div>
      </header>
      <div className="flex flex-wrap content-start gap-1">
        {host.capabilities.map((cap) => (
          <CapChip key={cap.label} cap={cap} />
        ))}
      </div>
      <AuthGatedLink
        href={`/utils/computers/ssh/${host.id}`}
        external
        adminOnly
        hideUnauthorized
        title={`Open an SSH terminal on ${host.hostname} (admins only, audited)`}
        className="w-fit rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-ink transition-colors hover:border-slate-400 hover:bg-surface-subtle dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500 dark:hover:bg-slate-800"
      >
        SSH terminal ↗
      </AuthGatedLink>
      {snapshot && (
        <div className="mt-auto flex items-center justify-end gap-2 text-[10px] text-ink-subtle dark:text-slate-400">
          {typeof snapshot.latency_ms === "number" && <span>{snapshot.latency_ms} ms</span>}
          <StalenessIndicator fetchedAt={snapshot.fetched_at} />
        </div>
      )}
    </article>
  );
}

export function HostsPanel({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  const byId = new Map(snapshots.map((s) => [s.id, s]));
  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1.5">
        <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
          Computers and Servers
        </h1>
        <p className="text-sm text-ink-subtle dark:text-slate-300">
          The machines the lab&apos;s services run on. Hosts running the
          sdl-lab-hostops agent carry its live status pill. Admins get an
          in-browser SSH terminal per machine; every session is audited.
        </p>
        <div
          className="flex flex-wrap items-center gap-1.5"
          role="list"
          aria-label="Capability color legend"
        >
          {CAP_LEGEND.map(({ kind, label }) => (
            <span
              key={kind}
              role="listitem"
              className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${CAP_STYLE[kind]}`}
            >
              {label}
            </span>
          ))}
        </div>
      </header>
      {/* Same grid geometry as EquipmentGrid — four equal stretchy columns
          filling the content container, so a 2-wide host tile is exactly half
          the container: the same width and left/right alignment as an
          Overview platform card. (Columns were previously capped at 262px,
          which left the grid ~120px short of the container's right edge.)
          Every host tile is 2 wide. */}
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
        style={{ gridAutoRows: "minmax(220px, auto)" }}
      >
        {LAB_HOSTS.map((host) => (
          <div
            key={host.id}
            className="h-full"
            style={{ gridColumn: "span 2", gridRow: "span 1" }}
          >
            <HostTile
              host={host}
              snapshot={host.opsId ? byId.get(host.opsId) : undefined}
            />
          </div>
        ))}
      </div>
    </section>
  );
}
