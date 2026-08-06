"use client";

import { EmbeddedDevicePanel } from "@/components/EmbeddedDevicePanel";

/**
 * Generic full-page equipment control view.
 *
 * Frames the device's own operator panel (see `EmbeddedDevicePanel`) rather
 * than reimplementing its controls here. `/ot2_hte` and `/ot2_complexation`
 * are fixed-id aliases of this route. Equipment that hosts no panel gets a
 * notice pointing back at its tile.
 */
export default function EquipmentControlPage({
  params,
}: {
  params: { equipmentId: string };
}) {
  return <EmbeddedDevicePanel equipmentId={params.equipmentId} />;
}
