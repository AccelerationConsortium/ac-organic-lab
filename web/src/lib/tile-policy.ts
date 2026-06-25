/**
 * Single source of truth for "what is gated by the dashboard lock?"
 *
 * The dashboard is view-only until a user signs in; the server-side gate
 * (middleware.ts → /auth/verify) requires a session for *every* control
 * write. This helper only shapes the *client-side* UX on top of that:
 *
 *   - kindHasDestructiveControls(kind) — true when a tile of this kind
 *     either has, or will have, destructive controls. Drives whether
 *     EquipmentStatusCard shows a "Sign in" chip in its header when signed
 *     out. Kind-specific tiles (FumeHoodTile, PowerStripTile, etc.) make
 *     their own decision based on what they actually render — e.g.
 *     PowerStripTile adds its own auto-relocking cover over the outlets.
 */

import type { EquipmentSnapshot } from "@/types/api";

type Kind = NonNullable<EquipmentSnapshot["kind"]>;

// Kinds that operate hardware on a command. The lock chip appears on
// tiles of these kinds even before the kind-specific control buttons
// are wired up — the chip is the visible promise that controls, when
// they arrive, will be gated.
const DESTRUCTIVE_KINDS: ReadonlySet<Kind> = new Set<Kind>([
  "solid_doser",
  "liquid_handler",
  "press",
  "fume_hood",
  "robot_arm",
  "hplc",
  "plate_reader",
  "plate_sealer",
  "plate_stacker",
  "shaker",
]);

// Kinds outside the destructive-lock policy (no lock chip): cameras and
// environmental sensors. Camera controls (PTZ, presets, snapshots,
// privacy/streaming toggles, recording) cannot damage hardware or interrupt
// an experiment, and environmental sensors have no controls at all — these
// are convenience controls, gated only on sign-in (no per-tile lock chip).
// The set is the implicit complement of DESTRUCTIVE_KINDS; this comment is
// the natural place to revisit when a new kind joins the enum.

export function kindHasDestructiveControls(kind: Kind | string | undefined): boolean {
  if (!kind) return false;
  return DESTRUCTIVE_KINDS.has(kind as Kind);
}
