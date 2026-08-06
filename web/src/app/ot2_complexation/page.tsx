"use client";

import { EmbeddedDevicePanel } from "@/components/EmbeddedDevicePanel";

/** Alias of /equipment/ot2_complexation/control — the complexation OT-2. */
export default function Ot2ComplexationPage() {
  return (
    <EmbeddedDevicePanel
      equipmentId="ot2_complexation"
      title="Opentrons OT-2 (Complexation) — operator panel"
    />
  );
}
