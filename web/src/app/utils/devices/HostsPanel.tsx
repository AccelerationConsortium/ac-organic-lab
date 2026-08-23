import type { EquipmentSnapshot } from "@/types/api";
import { StalenessIndicator } from "@/components/StalenessIndicator";
import { STATE_META, effectiveState } from "@/lib/state-meta";

/**
 * The lab's host machines — the servers and device PCs the equipment services
 * run on, rendered in the same tile chrome as the printer grid below. This is
 * a hand-maintained inventory (hosts are not equipment, so they have no
 * registry entry); keep it in sync with DEVICE_PC_SETUP.md §7.
 *
 * `opsId` marks a host running the `sdl-lab-hostops` agent and names its
 * equipment.yaml id; those tiles carry the agent's live status pill (the
 * hostops entries stay registered and polled — they just render here instead
 * of on the Services card).
 */
type LabHost = {
  id: string;
  name: string;
  kind: string; // subtitle label, e.g. "Windows PC"
  hostname: string;
  /** Lab services / servers running on the machine. */
  services: string;
  /** Equipment this machine controls (what the services front). */
  controls: string;
  /** What the lab-ops agent on this host offers; null = no agent. */
  opsApi: string | null;
  opsId?: string;
};

const HOSTOPS_API =
  "Service status · logs · restart (whitelisted) · serial-port enumeration · local /status probes — bearer-token MCP, port 8060";

const LAB_HOSTS: LabHost[] = [
  {
    id: "gaia",
    name: "Central Server (gaia)",
    kind: "Linux server",
    hostname: "sdl2-server-gaia",
    services:
      "Dashboard web + API, auth edge (Caddy + ac_auth), kasa-tapo gateway + go2rtc, Uptime Kuma, PyPoe, Hermes, AnaliticaDB, Bitácora",
    controls: "Tapo cameras and Kasa plugs / power strips (via the kasa-tapo gateway)",
    opsApi: null,
  },
  {
    id: "cytation-pc",
    name: "Cytation PC",
    kind: "Windows PC",
    hostname: "sdl2-pc-03-cytation",
    services:
      "xarm :8000 · plateloc :8010 · ot2-gateway-hte :8020 · ot2-gateway-complexation :8021 · torry-pines-shaker :8030 · cytation :8040 · biostack4 :8050 · sdl-lab-hostops :8060",
    controls:
      "xArm5 (arm, gripper, track), PlateLoc sealer, both OT-2 robots, Torrey Pines shaker, Cytation 5 reader, BioStack stacker",
    opsApi: HOSTOPS_API,
    opsId: "hostops_cytation_pc",
  },
  {
    id: "uplc-pc",
    name: "UPLC PC",
    kind: "Windows PC",
    hostname: "sdl2-pc-06-uplc",
    services: "hplc-ms-status :8010 (UPLC-MS sidecar) · sdl-lab-hostops :8060",
    controls:
      "Agilent UPLC-MS (via OpenLab CDS) · network bridge to the OT-2 complexation robot (USB portproxy :31950)",
    opsApi: HOSTOPS_API,
    opsId: "hostops_uplc_pc",
  },
];

// Mirrors TileShell's card chrome so these tiles sit flush with the live
// equipment tiles in the printer grid below.
const TILE_CARD =
  "flex h-full flex-col gap-2 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900";

function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-xs">
      <span className="font-medium uppercase text-ink-muted dark:text-slate-400">
        {label}
      </span>{" "}
      <span className="text-ink-subtle dark:text-slate-300">{value}</span>
    </div>
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
      <div className="flex flex-col gap-1.5">
        <FactRow label="Services" value={host.services} />
        <FactRow label="Controls" value={host.controls} />
        <FactRow label="Lab-ops API" value={host.opsApi ?? "none — no ops agent on this host"} />
      </div>
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
      <header>
        <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
          PCs &amp; Servers
        </h1>
        <p className="text-sm text-ink-subtle dark:text-slate-300">
          The machines the lab&apos;s services run on. Hosts running the
          sdl-lab-hostops agent carry its live status pill.
        </p>
      </header>
      {/* Same grid geometry as EquipmentGrid; every host tile is 2 wide. */}
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:[grid-template-columns:repeat(4,minmax(0,262px))]"
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
