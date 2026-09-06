import type {
  EquipmentSnapshot,
  LabHostGroup,
  LabHostMachine,
  LabHostService,
  LabHostServiceRole,
  LabHostsResponse,
} from "@/types/api";
import { AuthGatedLink } from "@/components/AuthGatedLink";
import { StalenessIndicator } from "@/components/StalenessIndicator";
import { STATE_META, effectiveState } from "@/lib/state-meta";

/**
 * The lab's host machines — the servers and device PCs the equipment services
 * run on. Nothing here is hand-maintained anymore: `GET /api/hosts`
 * (api/app/hosts.py) derives each machine's services — name, **port, domain**
 * — from `equipment.yaml` base_urls, joined onto the SSH console whitelist
 * (the server-side host inventory). Registering a new service in the registry
 * puts its chip on the tile with no frontend change.
 *
 * What a machine runs is rendered as **capability chips**, color-coded by the
 * server-classified role (legend in the panel header):
 *
 *   - sky      `service`   — a web-service process (`kind: other`)
 *   - violet   `ops`       — the sdl-lab-hostops agent surface (whitelisted
 *                            MCP: status / logs / restarts, never a shell)
 *   - emerald  `equipment` — an equipment REST service fronting hardware
 *
 * Chip tooltips carry the config detail (full base_url, adapter, spec
 * version) plus the live state from the equipment poll — hover for it.
 *
 * A host with an ops entry additionally renders a **host-ops panel** from the
 * agent's live `/status.details`: its service-control backend, the NSSM
 * service whitelist (restartable subset marked ↻), and the loopback ports it
 * probes.
 *
 * The second block, **"Other device hosts"**, is the Pis that carry one
 * instrument each, plus any registry hostname no whitelisted machine claims.
 * The split is presentation, not capability: a device host on the whitelist
 * carries its console id and gets the same SSH terminal link. Those tiles lead
 * with the machine's name and put the **address** in the chip
 * (`100.64.254.100:5000`), because on a single-instrument host the service
 * name is already the title.
 *
 * The SSH terminal link is admin-only, matching the gate in
 * `web/src/middleware.ts`; a non-admin sees no link at all. Host ids come
 * from the same whitelist the ssh route (`/utils/computers/ssh/<id>`) looks
 * up server-side, so the two can no longer drift.
 */

// Chip tints per role. Same palette families the pill system uses
// (lib/pill.ts) so the chips read as part of one design system; lighter fills
// because these are informational, not interactive.
const CAP_STYLE: Record<LabHostServiceRole, string> = {
  service:
    "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-200",
  ops: "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-800 dark:bg-violet-900/30 dark:text-violet-200",
  equipment:
    "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200",
};

const CAP_LEGEND: { kind: LabHostServiceRole; label: string }[] = [
  { kind: "service", label: "web service" },
  { kind: "ops", label: "lab-ops agent" },
  { kind: "equipment", label: "equipment service" },
];

const HOSTOPS_TITLE =
  "sdl-lab-hostops — whitelisted host-ops MCP (bearer token): service status · logs · restart (whitelisted subset) · serial-port enumeration · local /status probes. Never a shell.";

const ROLE_ORDER: Record<LabHostServiceRole, number> = { service: 0, ops: 1, equipment: 2 };

/** ":8010" from the config's explicit port, or the edge path ("/hermes/")
 *  when the base_url names no port. */
function addressSuffix(service: LabHostService): string {
  if (service.port != null) return `:${service.port}`;
  if (service.path && service.path !== "/") return service.path;
  return "";
}

function chipLabel(service: LabHostService): string {
  const suffix = addressSuffix(service);
  const name = service.role === "ops" ? "hostops" : service.name;
  return suffix ? `${name} ${suffix}` : name;
}

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

/** "100.64.254.100:5000" — the address as the registry spells it. An FQDN is
 *  shortened to its first label so the chip stays readable; an IP literal is
 *  left whole, since every part of it carries meaning. Used on device-host
 *  tiles, whose title already carries the service's name. */
function addressChipLabel(service: LabHostService): string {
  const raw = service.host;
  const host = IPV4.test(raw) || raw.includes(":") ? raw : raw.split(".")[0];
  return `${host}${addressSuffix(service)}`;
}

function chipTitle(service: LabHostService, snapshot: EquipmentSnapshot | undefined): string {
  const lines = [
    service.role === "ops" ? HOSTOPS_TITLE : `${service.kind} · spec v${service.protocol}`,
    service.base_url,
  ];
  if (snapshot) {
    lines.push(`live: ${STATE_META[effectiveState(snapshot)].label}`);
  } else if (service.adapter === "mock") {
    lines.push("link-out tile — not health-polled");
  }
  return lines.join("\n");
}

function CapChip({
  service,
  snapshot,
  address = false,
}: {
  service: LabHostService;
  snapshot: EquipmentSnapshot | undefined;
  /** Label the chip with the address rather than the service name. */
  address?: boolean;
}) {
  return (
    <span
      title={chipTitle(service, snapshot)}
      data-kind={service.role}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${CAP_STYLE[service.role]}`}
    >
      {address ? addressChipLabel(service) : chipLabel(service)}
    </span>
  );
}

function sortedServices(services: LabHostService[]): LabHostService[] {
  // Stable: registry order within each role band.
  return [...services].sort((a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role]);
}

// ---------------------------------------------------------------------------
// Host-ops detail — rendered from the agent's live /status.details
// (backend, services_whitelist, restartable, probe_ports).
// ---------------------------------------------------------------------------

type OpsDetails = {
  backend: string | null;
  whitelist: string[];
  restartable: Set<string>;
  probePorts: number[];
};

function readOpsDetails(snapshot: EquipmentSnapshot | undefined): OpsDetails | null {
  const details = snapshot?.status?.details as Record<string, unknown> | undefined;
  if (!details || !Array.isArray(details.services_whitelist)) return null;
  const strings = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : [];
  return {
    backend: typeof details.backend === "string" ? details.backend : null,
    whitelist: strings(details.services_whitelist),
    restartable: new Set(strings(details.restartable)),
    probePorts: Array.isArray(details.probe_ports)
      ? details.probe_ports.filter((x): x is number => typeof x === "number")
      : [],
  };
}

function OpsPanel({
  service,
  snapshot,
}: {
  service: LabHostService;
  snapshot: EquipmentSnapshot | undefined;
}) {
  const details = readOpsDetails(snapshot);
  return (
    <div className="rounded-lg border border-violet-200 bg-violet-50/60 px-2.5 py-2 text-[11px] dark:border-violet-900/60 dark:bg-violet-900/15">
      <p className="font-medium text-violet-900 dark:text-violet-200" title={HOSTOPS_TITLE}>
        Host ops — sdl-lab-hostops{details?.backend ? ` · ${details.backend}` : ""}{" "}
        <span className="font-mono font-normal text-violet-700 dark:text-violet-300">
          {addressSuffix(service)}
        </span>
      </p>
      {details ? (
        <>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {details.whitelist.map((svc) => {
              const restartable = details.restartable.has(svc);
              return (
                <span
                  key={svc}
                  title={
                    restartable
                      ? "Whitelisted for status, logs and restart through the ops surface"
                      : "Whitelisted for status and logs (restart not offered)"
                  }
                  className="inline-flex items-center gap-1 rounded border border-violet-200 bg-surface-raised px-1.5 py-0.5 font-mono text-violet-900 dark:border-violet-800 dark:bg-slate-900 dark:text-violet-200"
                >
                  {svc}
                  {restartable && <span aria-label="restartable">↻</span>}
                </span>
              );
            })}
          </div>
          <p className="mt-1.5 text-violet-800/90 dark:text-violet-300/90">
            ↻ restartable via the ops surface
            {details.probePorts.length > 0 && (
              <>
                {" · probes "}
                <span className="font-mono">
                  {details.probePorts.map((p) => `:${p}`).join(" ")}
                </span>
              </>
            )}
          </p>
        </>
      ) : (
        <p className="mt-1 text-violet-800/90 dark:text-violet-300/90">
          No live details — the ops agent has not answered a poll yet.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiles
// ---------------------------------------------------------------------------

// Mirrors TileShell's card chrome so these tiles sit flush with the live
// equipment tiles in the printer grid below.
const TILE_CARD =
  "flex h-full flex-col gap-2 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900";

function HostTile({
  host,
  snapshotById,
}: {
  host: LabHostMachine;
  snapshotById: Map<string, EquipmentSnapshot>;
}) {
  const ops = host.services.find((s) => s.role === "ops");
  const opsSnapshot = ops ? snapshotById.get(ops.id) : undefined;
  return (
    <article className={TILE_CARD}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="truncate text-sm font-semibold text-ink dark:text-slate-100">
            {host.label}
          </h3>
          <p className="truncate text-xs text-ink-subtle dark:text-slate-400">
            <span className="uppercase">{host.kind}</span> ·{" "}
            <span className="font-mono">{host.hostname}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {ops &&
            (opsSnapshot ? (
              // STATE_META, not StatusPill: effectiveState can return the
              // presentation-only "unreachable", which lib/format's maps lack.
              <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATE_META[effectiveState(opsSnapshot)].badge}`}
              >
                {STATE_META[effectiveState(opsSnapshot)].label}
              </span>
            ) : (
              <span className="text-[10px] text-ink-subtle dark:text-slate-400">
                no data
              </span>
            ))}
        </div>
      </header>
      <div className="flex flex-wrap content-start gap-1">
        {sortedServices(host.services)
          .filter((s) => s.role !== "ops")
          .map((service) => (
            <CapChip key={service.id} service={service} snapshot={snapshotById.get(service.id)} />
          ))}
      </div>
      {ops && <OpsPanel service={ops} snapshot={opsSnapshot} />}
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
      {opsSnapshot && (
        <div className="mt-auto flex items-center justify-end gap-2 text-[10px] text-ink-subtle dark:text-slate-400">
          {typeof opsSnapshot.latency_ms === "number" && <span>{opsSnapshot.latency_ms} ms</span>}
          <StalenessIndicator fetchedAt={opsSnapshot.fetched_at} />
        </div>
      )}
    </article>
  );
}

function OtherHostTile({
  group,
  snapshotById,
}: {
  group: LabHostGroup;
  snapshotById: Map<string, EquipmentSnapshot>;
}) {
  // A whitelisted device host leads with its name and shows the address in the
  // chip; an unclaimed hostname has no name to lead with, so it keeps the old
  // shape (hostname as title, service name in the chip).
  const named = Boolean(group.label);
  return (
    <article className={TILE_CARD}>
      <header className="flex min-w-0 flex-col gap-0.5">
        <h3
          className={`truncate text-sm font-semibold text-ink dark:text-slate-100 ${named ? "" : "font-mono"}`}
        >
          {group.label ?? group.hostname}
        </h3>
        <p className="truncate text-xs text-ink-subtle dark:text-slate-400">
          <span className="uppercase">{group.kind ?? "device host"}</span> ·{" "}
          <span className="font-mono">{named ? group.hostname : "from equipment.yaml"}</span>
        </p>
      </header>
      <div className="flex flex-wrap content-start gap-1">
        {sortedServices(group.services).map((service) => (
          <CapChip
            key={service.id}
            service={service}
            snapshot={snapshotById.get(service.id)}
            address={named}
          />
        ))}
      </div>
      {group.id && (
        <AuthGatedLink
          href={`/utils/computers/ssh/${group.id}`}
          external
          adminOnly
          hideUnauthorized
          title={`Open an SSH terminal on ${group.hostname} (admins only, audited)`}
          className="mt-auto w-fit rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-ink transition-colors hover:border-slate-400 hover:bg-surface-subtle dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500 dark:hover:bg-slate-800"
        >
          SSH terminal ↗
        </AuthGatedLink>
      )}
    </article>
  );
}

export function HostsPanel({
  hosts,
  snapshots,
}: {
  hosts: LabHostsResponse;
  snapshots: EquipmentSnapshot[];
}) {
  const byId = new Map(snapshots.map((s) => [s.id, s]));
  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1.5">
        <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
          Computers and Servers
        </h1>
        <p className="text-sm text-ink-subtle dark:text-slate-300">
          The machines the lab&apos;s services run on — every port and domain
          below comes from <span className="font-mono">equipment.yaml</span>.
          Hosts running the sdl-lab-hostops agent carry its live status pill
          and whitelist. Admins get an in-browser SSH terminal per machine;
          every session is audited.
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
          Overview platform card. Every host tile is 2 wide. */}
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
        style={{ gridAutoRows: "minmax(220px, auto)" }}
      >
        {hosts.hosts.map((host) => (
          <div
            key={host.id}
            className="h-full"
            style={{ gridColumn: "span 2", gridRow: "span 1" }}
          >
            <HostTile host={host} snapshotById={byId} />
          </div>
        ))}
      </div>
      {hosts.other_hosts.length > 0 && (
        <>
          <header className="mt-2 flex flex-col gap-0.5">
            <h2 className="text-sm font-semibold text-ink dark:text-slate-100">
              Other device hosts
            </h2>
            <p className="text-xs text-ink-subtle dark:text-slate-400">
              The Pis that carry one instrument each, plus any host the registry
              reaches that is not one of the machines above. Most have a
              terminal too.
            </p>
          </header>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {hosts.other_hosts.map((group) => (
              <div key={group.hostname} className="h-full" style={{ gridColumn: "span 2" }}>
                <OtherHostTile group={group} snapshotById={byId} />
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
