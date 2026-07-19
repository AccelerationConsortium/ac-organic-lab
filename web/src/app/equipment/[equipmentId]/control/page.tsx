"use client";

import { Ot2ControlPanel } from "@/components/Ot2ControlPanel";

/**
 * Generic full-page equipment control view.
 *
 * Today this renders the dedicated OT-2 interface (deck, declared vs observed
 * state, modules, tips, claim); other kinds get a graceful notice from the
 * panel itself. `/ot2_hte` and `/ot2_complexation` are fixed-id aliases of
 * this route. All runtime data comes from the central equipment API; all
 * writes ride the existing audited `/api/equipment/{id}/control/*`
 * passthrough (claims + auth enforced server-side).
 */
export default function EquipmentControlPage({
  params,
}: {
  params: { equipmentId: string };
}) {
  return <Ot2ControlPanel equipmentId={params.equipmentId} />;
}
