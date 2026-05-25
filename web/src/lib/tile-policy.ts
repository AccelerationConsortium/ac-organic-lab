/**
 * Single source of truth for "what is gated by the dashboard lock?"
 *
 * Policy (operator-facing, documented in EQUIPMENT_INTEGRATION.md §6b):
 *
 *   The lock chip gates destructive controls only. Convenience controls
 *   (camera PTZ / presets / snapshots, lights, anything that cannot
 *   damage hardware or interrupt an experiment) are intentionally
 *   left open even when the password gate is enabled.
 *
 * Two helpers:
 *
 *   - kindHasDestructiveControls(kind) — true when a tile of this kind
 *     either has, or will have, destructive controls that should sit
 *     behind the lock. Drives whether EquipmentStatusCard shows a lock
 *     chip in its header. Kind-specific tiles (FumeHoodTile,
 *     PowerStripTile, etc.) make their own decision based on what they
 *     actually render.
 *
 *   - outletIsSafe(label) — heuristic for "this outlet drives lighting,
 *     not equipment; do not lock it". Matches `light` or `lamp` as
 *     whole-word case-insensitive in the outlet's gateway-supplied
 *     label. False positives (e.g. "Lighthouse fan") are possible -
 *     replace this with an explicit per-outlet flag in
 *     kasa_tapo_services/devices.yaml once the substring heuristic
 *     becomes a problem.
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

// Kinds explicitly outside the lock policy:
//   - camera: PTZ + presets + snapshots are convenience controls
//   - smart_plug / power_strip: per-outlet decision (see outletIsSafe)
//   - environmental_sensor: read-only
//   - other: unknown shape; default to safe so we don't fake-promise a
//     lock that nothing honors.
//
// The set is implicit (the complement of DESTRUCTIVE_KINDS), but
// listing them here is the natural place to revisit when a new kind
// joins the enum.

export function kindHasDestructiveControls(kind: Kind | string | undefined): boolean {
  if (!kind) return false;
  return DESTRUCTIVE_KINDS.has(kind as Kind);
}

// Kinds whose entire control surface is convenience-only — they bypass
// the dashboard password gate even when CONTROL_PASSWORD is set. Cameras
// (PTZ, presets, snapshots, privacy/streaming toggles, recording) cannot
// damage hardware or interrupt an experiment, and environmental sensors
// have no controls at all. Power strips and smart plugs are NOT in this
// set: a single power_strip can mix light outlets (safe) and hotplate
// outlets (destructive), and the URL alone doesn't disambiguate, so the
// middleware keeps them gated and the per-outlet decision happens in
// PowerStripTile via outletIsSafe().
const UNGATED_KINDS: ReadonlySet<Kind> = new Set<Kind>([
  "camera",
  "environmental_sensor",
]);

export function kindBypassesControlGate(kind: Kind | string | undefined | null): boolean {
  if (!kind) return false;
  return UNGATED_KINDS.has(kind as Kind);
}

const LIGHT_LABEL_RE = /\b(?:light|lamp)\b/i;

export function outletIsSafe(label: string | null | undefined): boolean {
  if (!label) return false;
  return LIGHT_LABEL_RE.test(label);
}
