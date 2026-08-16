import type { EquipmentSnapshot } from "@/types/api";
import { StatusDots } from "@/components/StatusDots";
import { STATE_META, effectiveState } from "@/lib/state-meta";

/**
 * The lab's host machines — the servers and device PCs the equipment services
 * run on. This is a hand-maintained inventory (hosts are not equipment, so
 * they have no registry entry); keep it in sync with DEVICE_PC_SETUP.md §7.
 *
 * `opsId` marks a host that runs the `sdl-lab-hostops` agent and names its
 * equipment.yaml id; those rows show the agent's live status (the hostops
 * entries stay registered and polled — they just no longer sit on the
 * Overview's Services card).
 */
type LabHost = {
  id: string;
  name: string;
  group: "Servers" | "Device PCs";
  hostname: string;
  os: string;
  runs: string;
  opsId?: string;
};

const LAB_HOSTS: LabHost[] = [
  {
    id: "gaia",
    name: "Central Server (gaia)",
    group: "Servers",
    hostname: "sdl2-server-gaia",
    os: "Linux",
    runs: "Dashboard (web + API), auth edge, camera/plug gateway + go2rtc, Uptime Kuma, PyPoe, Hermes, AnaliticaDB, Bitácora",
  },
  {
    id: "cytation-pc",
    name: "Cytation PC",
    group: "Device PCs",
    hostname: "sdl2-pc-03-cytation",
    os: "Windows",
    runs: "xArm, PlateLoc, both OT-2 gateways, shaker, Cytation 5, BioStack",
    opsId: "hostops_cytation_pc",
  },
  {
    id: "uplc-pc",
    name: "UPLC PC",
    group: "Device PCs",
    hostname: "sdl2-pc-06-uplc",
    os: "Windows",
    runs: "UPLC-MS sidecar, OT-2 complexation USB bridge",
    opsId: "hostops_uplc_pc",
  },
];

const GROUPS: LabHost["group"][] = ["Servers", "Device PCs"];

function OpsStatus({ snapshot }: { snapshot: EquipmentSnapshot | undefined }) {
  if (!snapshot) {
    return (
      <span className="text-xs text-ink-muted dark:text-slate-500">
        host-ops: no data
      </span>
    );
  }
  const state = effectiveState(snapshot);
  return (
    <span className="flex items-center gap-1.5">
      <span className="rounded-full border border-violet-300 bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-700 dark:border-violet-700 dark:bg-violet-900/30 dark:text-violet-300">
        host-ops
      </span>
      <span className="text-xs text-ink-muted dark:text-slate-400">
        {STATE_META[state].label}
      </span>
      <StatusDots snapshot={snapshot} />
    </span>
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
          The machines the lab&apos;s services run on. Hosts marked{" "}
          <span className="font-medium">host-ops</span> run the whitelisted
          remote-ops agent and show its live status.
        </p>
      </header>
      {GROUPS.map((group) => (
        <div key={group} className="flex flex-col gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted dark:text-slate-500">
            {group}
          </h2>
          <ul className="flex flex-col gap-2">
            {LAB_HOSTS.filter((h) => h.group === group).map((host) => (
              <li
                key={host.id}
                className="flex flex-col gap-1 rounded-md border border-slate-200 bg-white/60 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/40"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-medium text-ink dark:text-slate-100">
                      {host.name}
                    </span>
                    <span className="font-mono text-[11px] text-ink-muted dark:text-slate-500">
                      {host.hostname}
                    </span>
                    <span className="text-[11px] text-ink-muted dark:text-slate-500">
                      {host.os}
                    </span>
                  </div>
                  {host.opsId ? (
                    <OpsStatus snapshot={byId.get(host.opsId)} />
                  ) : (
                    <span className="text-xs text-ink-muted dark:text-slate-500">
                      no ops agent
                    </span>
                  )}
                </div>
                <p className="text-xs text-ink-subtle dark:text-slate-400">
                  {host.runs}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
