import type { EquipmentSnapshot } from "@/types/api";
import { EquipmentStatusCard } from "@/components/EquipmentStatusCard";

/**
 * The lab's host machines — the servers and device PCs the equipment services
 * run on, rendered in the same tile style as the Bambu printer panel below.
 * This is a hand-maintained inventory (hosts are not equipment, so they have
 * no registry entry); keep it in sync with DEVICE_PC_SETUP.md §7.
 *
 * `opsId` marks a host running the `sdl-lab-hostops` agent and names its
 * equipment.yaml id; those hosts render the agent's live equipment tile (the
 * same card the Services grid used to show — the entries stay registered and
 * polled, they just live here now). Hosts without ops get a static card in
 * matching chrome.
 */
type LabHost = {
  id: string;
  name: string;
  kind: "server" | "device PC";
  hostname: string;
  os: string;
  runs: string;
  opsId?: string;
};

const LAB_HOSTS: LabHost[] = [
  {
    id: "gaia",
    name: "Central Server (gaia)",
    kind: "server",
    hostname: "sdl2-server-gaia",
    os: "Linux",
    runs: "Dashboard (web + API), auth edge, camera/plug gateway + go2rtc, Uptime Kuma, PyPoe, Hermes, AnaliticaDB, Bitácora",
  },
  {
    id: "cytation-pc",
    name: "Cytation PC Ops",
    kind: "device PC",
    hostname: "sdl2-pc-03-cytation",
    os: "Windows",
    runs: "xArm, PlateLoc, both OT-2 gateways, shaker, Cytation 5, BioStack",
    opsId: "hostops_cytation_pc",
  },
  {
    id: "uplc-pc",
    name: "UPLC PC Ops",
    kind: "device PC",
    hostname: "sdl2-pc-06-uplc",
    os: "Windows",
    runs: "UPLC-MS sidecar, OT-2 complexation USB bridge",
    opsId: "hostops_uplc_pc",
  },
];

// Mirrors TileShell's card chrome so static host cards sit flush with the
// live equipment tiles around them.
const TILE_CARD =
  "flex h-full flex-col gap-2 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900";

function StaticHostCard({ host, note }: { host: LabHost; note: string }) {
  return (
    <article className={TILE_CARD}>
      <header className="flex min-w-0 flex-col gap-0.5">
        <h3 className="truncate text-sm font-semibold text-ink dark:text-slate-100">
          {host.name}
        </h3>
        <p className="truncate text-xs text-ink-subtle dark:text-slate-500">
          <span className="uppercase">{host.kind}</span> ·{" "}
          <span className="font-mono">{host.hostname}</span> · {host.os}
        </p>
      </header>
      <p className="text-xs text-ink-subtle dark:text-slate-400">{host.runs}</p>
      <p className="mt-auto text-[10px] text-ink-subtle dark:text-slate-500">{note}</p>
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
        <p className="text-sm text-ink-subtle dark:text-slate-400">
          The machines the lab&apos;s services run on. Hosts running the
          host-ops agent show its live tile; hover a tile for what runs there.
        </p>
      </header>
      {/* Same grid geometry as EquipmentGrid; every host card is a 2×1 tile. */}
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:[grid-template-columns:repeat(4,minmax(0,262px))]"
        style={{ gridAutoRows: "minmax(220px, auto)" }}
      >
        {LAB_HOSTS.map((host) => {
          const snapshot = host.opsId ? byId.get(host.opsId) : undefined;
          return (
            <div
              key={host.id}
              className="h-full"
              style={{ gridColumn: "span 2", gridRow: "span 1" }}
              title={host.runs}
            >
              {snapshot ? (
                <EquipmentStatusCard snapshot={snapshot} />
              ) : (
                <StaticHostCard
                  host={host}
                  note={host.opsId ? "host-ops agent — no data yet" : "no ops agent"}
                />
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
