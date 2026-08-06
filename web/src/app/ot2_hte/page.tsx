"use client";

import { EmbeddedDevicePanel } from "@/components/EmbeddedDevicePanel";

/** Alias of /equipment/ot2_hte/control — the HTE bench OT-2. */
export default function Ot2HtePage() {
  return <EmbeddedDevicePanel equipmentId="ot2_hte" title="Opentrons OT-2 (HTE) — operator panel" />;
}
