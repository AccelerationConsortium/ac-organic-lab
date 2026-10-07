import type { EquipmentSnapshot } from "@/types/api";

/**
 * Reading a plate-weigher envelope (weigh-every-plate: one balance, one plate
 * lift, an optional lid — STATUS_SPEC v1.2, `kind: other`).
 *
 * The contract has no `plate_weigher` kind yet (WEIGHER_DOSER_SPLIT phase 4),
 * so the tile is selected by the envelope's shape rather than by id: a
 * `balance` component plus `details.balance.driver` and `details.lift.driver`.
 * Every unit built from that package — the Flex Pi 5 servo lift, the bench
 * Tic lift, a bare OT-2 slot balance — then gets this tile without an
 * EquipmentGrid edit per device.
 */

export type BalanceCondition = "underload" | "overload" | null;

export interface PlateWeigherState {
  /** Last mass taken through the service (never a live poll). */
  massG: number | null;
  stable: boolean | null;
  /** Balance reports `Low` / `High` instead of a number (nothing on the pan,
   *  or more than it can weigh). The mass is null in that case. */
  condition: BalanceCondition;
  balanceConnected: boolean;
  balanceState: string | null;
  balanceMessage: string | null;
  liftState: string | null;
  liftMessage: string | null;
  /** `details.lift_pose` — the named pose the lift last reached, if known. */
  liftPose: string | null;
  poses: string[];
  lidFitted: boolean;
  lidState: string | null;
}

type Details = Record<string, unknown>;

function rec(value: unknown): Details | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Details) : null;
}

export function isPlateWeigher(snapshot: {
  kind?: string;
  status?: { components?: Record<string, unknown> | null; details?: Details | null } | null;
}): boolean {
  const details = snapshot.status?.details ?? null;
  const balance = rec(details?.["balance"]);
  const lift = rec(details?.["lift"]);
  return (
    snapshot.kind === "other" &&
    Boolean(snapshot.status?.components?.["balance"]) &&
    typeof balance?.["driver"] === "string" &&
    typeof lift?.["driver"] === "string"
  );
}

export function parsePlateWeigher(snapshot: EquipmentSnapshot): PlateWeigherState {
  const status = snapshot.status;
  const components = status.components ?? {};
  const details = (status.details ?? {}) as Details;
  const balance = rec(details["balance"]) ?? {};
  const lift = rec(details["lift"]) ?? {};
  const lid = rec(details["lid"]);
  const balanceComponent = components["balance"];
  const liftComponent = components["lift"];
  const lidComponent = components["lid"];

  const metricMass = status.metrics?.["mass_g"]?.value;
  const detailMass = balance["last_mass_g"];
  const massG =
    typeof metricMass === "number" ? metricMass : typeof detailMass === "number" ? detailMass : null;
  const conditionRaw = balance["condition"];
  const condition: BalanceCondition =
    conditionRaw === "underload" || conditionRaw === "overload" ? conditionRaw : null;
  const stableRaw = balance["last_stable"];
  const posesRaw = rec(lift["poses_deg"]) ?? rec(lift["poses_steps"]) ?? rec(lift["poses"]);

  return {
    massG,
    stable: typeof stableRaw === "boolean" ? stableRaw : null,
    condition,
    balanceConnected: balanceComponent?.connected === true,
    balanceState: typeof balanceComponent?.state === "string" ? balanceComponent.state : null,
    balanceMessage: balanceComponent?.message ?? null,
    liftState: typeof liftComponent?.state === "string" ? liftComponent.state : null,
    liftMessage: liftComponent?.message ?? null,
    liftPose: typeof details["lift_pose"] === "string" ? (details["lift_pose"] as string) : null,
    poses: posesRaw ? Object.keys(posesRaw) : [],
    lidFitted: Boolean(lidComponent) && lidComponent?.state !== "absent",
    lidState: typeof lidComponent?.state === "string" ? lidComponent.state : lid ? "unknown" : null,
  };
}

/** The OT-2 platebalance panel's fixed-width mass box: 4 decimals in 9 ch. */
export function formatMassG(state: Pick<PlateWeigherState, "massG" | "condition">): string {
  if (state.condition === "underload") return "     Low";
  if (state.condition === "overload") return "    High";
  if (state.massG === null) return "       —";
  return state.massG.toFixed(4).padStart(8, " ");
}

export function stabilityLabel(state: PlateWeigherState): string {
  if (state.condition === "underload") return "Low — nothing on the pan";
  if (state.condition === "overload") return "High — over capacity";
  if (state.massG === null) return "No reading";
  if (state.stable === null) return "Reading";
  return state.stable ? "Stable" : "Unstable";
}
