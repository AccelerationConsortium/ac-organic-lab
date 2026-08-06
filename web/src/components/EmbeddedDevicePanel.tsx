"use client";

import { devicePanelPath } from "@/lib/device-panels";

/**
 * Frames a device's own operator panel inside the dashboard.
 *
 * The alternative — reimplementing each device's controls here — means two
 * React trees per device kept in sync by hand, which had already drifted for
 * the OT-2. The device panel is authoritative: it reads `/status` directly
 * (seconds, not the aggregator's poll) and holds a real heartbeated claim
 * rather than the per-request claim the passthrough takes.
 *
 * Framed rather than linked with `next/link` for the same reason as
 * `/utils/xarm_control` and `/workflows`: a client-side transition would
 * resolve the edge path against this app's route manifest and 404.
 *
 * Access is enforced at the edge — these paths sit behind `forward_auth`
 * against `ac_auth`, and each device trusts the injected identity (see
 * `deploy/Caddyfile.single-edge`), so the panel picks up the signed-in user
 * without a second login and stamps them into `details.claimed_by.owner`.
 *
 * Trade-off worth knowing: writes made inside the panel go straight to the
 * device and so are **not** in `equipment_events` the way the dashboard's
 * `/control/*` passthrough writes are (ARCHITECTURE decision #1). Closing that
 * means having the device post its own `control_action` rows to
 * `/api/ingest/events`, the way the xArm's events exporter does.
 */
export function EmbeddedDevicePanel({
  equipmentId,
  title,
}: {
  equipmentId: string;
  title?: string;
}) {
  const path = devicePanelPath(equipmentId);

  if (!path) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white px-4 py-6 text-sm text-ink-subtle dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        <p>
          <span className="font-mono">{equipmentId}</span> does not host its own operator panel.
        </p>
        <p className="mt-2">
          Its controls live on the equipment tile — open the dashboard section that lists it.
        </p>
      </div>
    );
  }

  return (
    // Full-bleed, like /utils/xarm_control: the panel is a whole second UI
    // (deck, controls, claim) and the app's max-w-7xl column crops it.
    <div className="relative left-1/2 w-screen max-w-[100vw] -translate-x-1/2 px-4 sm:px-6 lg:px-8">
      {/* No border/radius/shadow: the panel draws its own tiles, so a frame
          around it reads as a second, redundant card edge. */}
      <iframe
        src={path}
        title={title ?? `${equipmentId} — operator panel`}
        className="h-[calc(100vh-180px)] min-h-[720px] w-full border-0 bg-transparent"
      />
    </div>
  );
}
