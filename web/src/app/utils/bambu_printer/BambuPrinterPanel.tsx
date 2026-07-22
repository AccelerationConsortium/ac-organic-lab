import { EquipmentGrid } from "@/components/EquipmentGrid";
import type { EquipmentSnapshot } from "@/types/api";

export function BambuPrinterPanel({ printers }: { printers: EquipmentSnapshot[] }) {
  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
          Bambu Printers
        </h1>
        <p className="text-sm text-ink-subtle dark:text-slate-400">
          Monitoring only · live local MQTT telemetry from the Bambu Gateway. Printer
          controls remain in Bambu&apos;s cloud interfaces.
        </p>
      </header>
      <EquipmentGrid snapshots={printers} />
    </section>
  );
}
