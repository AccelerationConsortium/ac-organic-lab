/**
 * Device-hosted operator panels, path-routed onto this origin by the edge.
 *
 * Some devices ship their own full operator UI (the OT-2 gateway's SPA, the
 * xArm's panel). Rather than maintain a second implementation of each in this
 * repo, the dashboard links straight at the device's own panel — the tile's
 * "Control interface" opens it in a new tab. These paths mirror the route
 * blocks in `deploy/Caddyfile.single-edge`; they are edge paths on *this*
 * origin, not device URLs, so the session cookie and the injected
 * `X-Auth-User` identity carry through without a second login.
 *
 * Kept here rather than in `equipment.yaml` because the mapping describes the
 * *edge's* routing table, not the device — the registry's `base_url` is the
 * device port the aggregator polls directly, deliberately un-proxied. Move
 * this into the registry only if the two ever need to agree automatically.
 */

export const DEVICE_PANEL_PATHS: Readonly<Record<string, string>> = Object.freeze({
  ot2_hte: "/ot2/hte/ui/",
  ot2_complexation: "/ot2/complexation/ui/",
  xarm_translocation: "/xarm5/web/",
});

/** The device's own panel path, or `null` when it hosts no panel. */
export function devicePanelPath(equipmentId: string): string | null {
  return DEVICE_PANEL_PATHS[equipmentId] ?? null;
}
