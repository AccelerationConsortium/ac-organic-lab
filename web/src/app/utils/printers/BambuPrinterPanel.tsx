import { EquipmentGrid } from "@/components/EquipmentGrid";
import type { EquipmentSnapshot } from "@/types/api";

/**
 * The Bambu gateway's own submission page.
 *
 * Absolute and tailnet-addressed on purpose. `equipment.yaml` reaches this
 * service at `127.0.0.1:8012` because the aggregator runs on the same host as
 * the gateway — but loopback in a visitor's browser means the visitor's own
 * machine, so the registry's `base_url` is useless as a link. A raw tailnet IP
 * rather than the MagicDNS name so the link resolves for any tailnet member
 * regardless of their DNS settings.
 */
const SUBMISSION_UI_URL = "http://100.64.254.6:8012/ui";

export function BambuPrinterPanel({ printers }: { printers: EquipmentSnapshot[] }) {
  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
            Bambu Printers
          </h1>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Monitoring only · live local MQTT telemetry from the Bambu Gateway. Printer
            controls remain in Bambu&apos;s cloud interfaces.
          </p>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Print jobs are submitted through the gateway&apos;s own page, which validates
            a model against the target machine and queues it. Queued jobs are never sent
            to a printer — dispatch is not implemented.
          </p>
        </div>
        <a
          href={SUBMISSION_UI_URL}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-900 hover:bg-emerald-100 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-200 dark:hover:bg-emerald-900/30"
        >
          Submit a print <span aria-hidden="true">↗</span>
        </a>
      </header>
      <EquipmentGrid snapshots={printers} />
    </section>
  );
}
