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
 * Every entry is a device-served page opened in a new tab, not a dashboard
 * route: each carries the shared /auth/banner.js bar, so the popped-out panel
 * still sits under the SDL2 identity.
 *
 * `gibbie_flex` is deliberately absent. It was mapped to a framed dashboard
 * page (/utils/flex_control) that iframed /flex/gibbie/ui/, but no such panel
 * has ever existed: the Gibbie PC runs only `gibbie-server` on :8070 — a
 * read-only STATUS_SPEC gateway with no /control/* and no UI — plus the
 * balance and host-ops services. Nothing listens on :8071, and the edge block
 * that would proxy it was never installed. Verified on the PC 2026-09-20:
 * `netstat` shows 8070 alone in the 80xx range, and `sc query` lists no Flex
 * service. So the tile offers no "Control interface" rather than a link that
 * 404s. Restore an entry here when a Flex operator panel actually ships.
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
  dobot_mg400: "/mg400/web/",
});

/** The device's own panel path, or `null` when it hosts no panel. */
export function devicePanelPath(equipmentId: string): string | null {
  return DEVICE_PANEL_PATHS[equipmentId] ?? null;
}
