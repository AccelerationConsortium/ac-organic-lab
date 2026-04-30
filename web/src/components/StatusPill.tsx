import type { EquipmentState } from "@/types/api";
import { stateClass, stateLabel } from "@/lib/format";

export function StatusPill({ state }: { state: EquipmentState }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${stateClass(state)}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      {stateLabel(state)}
    </span>
  );
}
