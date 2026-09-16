"use client";

import type { EquipmentSnapshot, MetricValue } from "@/types/api";
import { FetchErrorBand } from "@/components/FetchErrorBand";
import { stateClass } from "@/lib/format";
import { PrinterTileFrame } from "./PrinterTileFrame";

function formatMetric(metric?: MetricValue): string {
  if (!metric) return "—";
  const value = typeof metric.value === "number" && !Number.isInteger(metric.value)
    ? metric.value.toFixed(2) : String(metric.value);
  return metric.unit ? `${value} ${metric.unit}` : value;
}

const checkLabels: Record<string, string> = {
  printer_ethernet: "Printer", ethernet_link: "Ethernet", wifi_link: "Wi-Fi",
  internet_sharing: "Sharing", dhcp: "DHCP", printer_service: "Printer service",
};

/** Connection-only observations must never imply that a printer is ready to print. */
export function PrinterConnectionTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const unreachable = !!snapshot.fetch_error || status.equipment_status === "unknown";
  const healthy = !snapshot.fetch_error && status.equipment_status === "ready";
  const label = unreachable ? "Unreachable" : healthy ? "Connected" : "Connection degraded";
  const components = Object.values(status.components ?? {});
  const connected = components.filter(component => component.connected).length;
  const metrics = snapshot.fetch_error ? {} : status.metrics ?? {};

  return (
    <PrinterTileFrame
      name={snapshot.name}
      subtitle={<>
        <span className="whitespace-nowrap">{formatMetric(metrics.printer_ip)}</span>
        <span aria-hidden="true">·</span>
        <span className="whitespace-nowrap">{metrics.tcp_connect ? "TCP" : "Ping"} <span>{formatMetric(metrics.tcp_connect ?? metrics.ping)}</span></span>
      </>}
      fetchedAt={snapshot.fetched_at}
      staleAfterSeconds={90}
      badge={(
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${stateClass(unreachable ? "unknown" : healthy ? "ready" : "degraded")}`}>
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
          {label}
        </span>
      )}
    >
      {!snapshot.fetch_error && <section aria-label={`${snapshot.name} network checks`}>
        <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-ink-subtle dark:text-slate-400">
          <h3 className="font-medium">Network checks</h3>
          <span>{connected} of {components.length} passing</span>
        </div>
        <ul className="flex flex-wrap gap-1.5">
          {Object.entries(status.components ?? {}).map(([key, component]) => (
            <li key={key} title={component.message ?? undefined} className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs ring-1 ring-inset ${stateClass(component.connected ? "ready" : "degraded")}`}>
              <span aria-hidden="true">{component.connected ? "✓" : "!"}</span>
              <span>{checkLabels[key] ?? key.replace(/_/g, " ")}</span>
              <span className="sr-only">: {component.state}</span>
            </li>
          ))}
        </ul>
      </section>}
      {snapshot.fetch_error ? <FetchErrorBand error={snapshot.fetch_error} /> : !healthy && (
        <p className="text-xs text-ink-subtle dark:text-slate-400">{status.message}</p>
      )}
      <p className="text-xs text-ink-subtle dark:text-slate-400">
        Print progress and cloud connectivity are not monitored.
      </p>
    </PrinterTileFrame>
  );
}
