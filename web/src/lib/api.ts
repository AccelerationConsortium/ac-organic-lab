import type {
  AggregatorHealth,
  ControlAck,
  EquipmentList,
  EquipmentSnapshot,
  MediaListing,
  PlatformsConfig,
  PlugSwitchRequest,
  PresetGotoRequest,
  PresetSaveRequest,
  PrivacyRequest,
  PtzContinuousRequest,
  PtzNudgeRequest,
  RecordingCancelRequest,
  RecordingCancelResponse,
  RecordingStartRequest,
  RecordingStartResponse,
  RecordingStopRequest,
  RecordingStopResponse,
  RollingStartRequest,
  RollingStopResponse,
  SnapshotRequest,
  SnapshotResponse,
  StreamingRequest,
} from "@/types/api";

/**
 * Error thrown by {@link fetchJson} on a non-2xx response. Preserves the
 * HTTP status code and the parsed JSON body so callers can branch on the
 * code (e.g. 412 vs 423) and surface the device's structured detail
 * (claimed_by, retry_after_s, actual_c / setpoint_c / tolerance_c, ...).
 *
 * The legacy `Error.message` is still set to the device's `detail` string
 * when present, so naive callers that only show `e.message` keep working.
 */
export class ApiError extends Error {
  /** HTTP status code from the failed response. */
  readonly status: number;
  /** Raw parsed JSON body if the response was JSON; otherwise `null`. */
  readonly body: unknown;
  /** The path that failed, for diagnostics. */
  readonly path: string;
  /** `Retry-After` header value in seconds if the server sent one. */
  readonly retryAfterS: number | null;

  constructor(
    status: number,
    statusText: string,
    body: unknown,
    path: string,
    retryAfterS: number | null,
  ) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : undefined;
    super(detail ?? `${status} ${statusText} from ${path}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.path = path;
    this.retryAfterS = retryAfterS;
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const retryAfterHeader = response.headers.get("Retry-After");
    const retryAfterS =
      retryAfterHeader && /^\d+(\.\d+)?$/.test(retryAfterHeader)
        ? parseFloat(retryAfterHeader)
        : null;
    throw new ApiError(response.status, response.statusText, body, path, retryAfterS);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function getHealth(): Promise<AggregatorHealth> {
  return fetchJson<AggregatorHealth>("/api/health");
}

export async function getEquipmentList(): Promise<EquipmentList> {
  return fetchJson<EquipmentList>("/api/equipment");
}

export async function getPlatforms(): Promise<PlatformsConfig> {
  return fetchJson<PlatformsConfig>("/api/platforms");
}

export async function getEquipmentStatus(id: string): Promise<EquipmentSnapshot> {
  return fetchJson<EquipmentSnapshot>(`/api/equipment/${encodeURIComponent(id)}/status`);
}

// -- Control passthrough (cameras + plugs) ---------------------------------

function controlUrl(equipmentId: string, action: string): string {
  return `/api/equipment/${encodeURIComponent(equipmentId)}/control/${action}`;
}

async function controlPost<TBody extends object, TResp = ControlAck>(
  equipmentId: string,
  action: string,
  body: TBody,
): Promise<TResp> {
  return fetchJson<TResp>(controlUrl(equipmentId, action), {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Authorize an assistant-proposed control action (UI_DESIGN §5). Reuses the
 * exact operator control passthrough — identity, per-equipment authorization,
 * the claim/act/release dance, and the audit row all apply — and stamps
 * `X-Control-Origin: assistant` so the audit trail separates assistant-
 * originated actions from tile clicks. `action` is the proposal's
 * `passthrough_action` (e.g. `graph/move_to`); `args` its validated body.
 * The concrete response is preserved because plate-reader reads and imaging
 * return measurements/metadata rather than the generic `{ok: true}` ack.
 */
export async function authorizeAssistantAction(
  equipmentId: string,
  action: string,
  args: Record<string, unknown>,
  opts: {
    /** `<plan_id>#<step index>` when this call is one step of an approved
     *  assistant plan (UI_DESIGN §5 Step 1i). Stamps `X-Control-Origin:
     *  assistant-plan` + `X-Control-Plan` so the audit row joins back to the
     *  `assistant_plan_approved` row that reviewed it. */
    plan?: string;
  } = {},
): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Control-Origin": opts.plan ? "assistant-plan" : "assistant",
  };
  if (opts.plan) headers["X-Control-Plan"] = opts.plan;
  return fetchJson<Record<string, unknown>>(controlUrl(equipmentId, action), {
    method: "POST",
    body: JSON.stringify(args ?? {}),
    headers,
  });
}

/** One step's outcome, as the bubble reports it back after running a plan. */
export interface AssistantPlanStepResult {
  index: number;
  outcome: "ok" | "failed" | "skipped";
  status_code?: number | null;
  message?: string | null;
}

/** Record the operator's approval of an assistant plan — the hash of exactly
 *  the steps the card rendered. 409 if the plan changed, 410 if it expired. */
export async function approveAssistantPlan(
  planId: string,
  stepHash: string,
): Promise<{ plan_id: string; step_hash: string; approved: boolean; expires_in_s: number }> {
  return fetchJson(`/api/assistant/plans/${encodeURIComponent(planId)}/approve`, {
    method: "POST",
    body: JSON.stringify({ step_hash: stepHash }),
    headers: { "Content-Type": "application/json" },
  });
}

/** Report how an approved plan ended (best-effort audit; never blocks the UI). */
export async function finishAssistantPlan(
  planId: string,
  body: {
    status: "executed" | "failed" | "aborted";
    results: AssistantPlanStepResult[];
    halt_reason?: string | null;
  },
): Promise<{ ok: boolean }> {
  return fetchJson(`/api/assistant/plans/${encodeURIComponent(planId)}/finish`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

async function controlDelete<TResp = ControlAck>(
  equipmentId: string,
  action: string,
): Promise<TResp> {
  return fetchJson<TResp>(controlUrl(equipmentId, action), {
    method: "DELETE",
  });
}

async function controlGet<TResp>(
  equipmentId: string,
  action: string,
): Promise<TResp> {
  return fetchJson<TResp>(controlUrl(equipmentId, action), {
    method: "GET",
  });
}

// PTZ - the route accepts either nudge or continuous bodies; callers can
// pass whichever shape they want.
export async function postPtz(
  equipmentId: string,
  body: PtzNudgeRequest | PtzContinuousRequest,
): Promise<ControlAck> {
  return controlPost(equipmentId, "ptz", body);
}

export async function savePreset(
  equipmentId: string,
  body: PresetSaveRequest,
): Promise<ControlAck> {
  return controlPost(equipmentId, "preset/save", body);
}

export async function gotoPreset(
  equipmentId: string,
  body: PresetGotoRequest,
): Promise<ControlAck> {
  return controlPost(equipmentId, "preset/goto", body);
}

export async function deletePreset(
  equipmentId: string,
  presetId: string,
): Promise<ControlAck> {
  return controlDelete(equipmentId, `preset/${encodeURIComponent(presetId)}`);
}

export async function setPrivacy(
  equipmentId: string,
  body: PrivacyRequest,
): Promise<ControlAck> {
  return controlPost(equipmentId, "privacy", body);
}

export async function setStreaming(
  equipmentId: string,
  body: StreamingRequest,
): Promise<ControlAck> {
  return controlPost(equipmentId, "streaming", body);
}

// -- Snapshot + recording --------------------------------------------------
//
// All four endpoints are typed POSTs that return action-specific bodies
// (rather than the generic `ControlAck`), so we use the generic
// `controlPost` form to override the default response type.

export async function takeSnapshot(
  equipmentId: string,
  body: SnapshotRequest = {},
): Promise<SnapshotResponse> {
  return controlPost<SnapshotRequest, SnapshotResponse>(
    equipmentId, "snapshot", body,
  );
}

export async function startRecording(
  equipmentId: string,
  body: RecordingStartRequest = {},
): Promise<RecordingStartResponse> {
  return controlPost<RecordingStartRequest, RecordingStartResponse>(
    equipmentId, "recording/start", body,
  );
}

export async function stopRecording(
  equipmentId: string,
  body: RecordingStopRequest = {},
): Promise<RecordingStopResponse> {
  return controlPost<RecordingStopRequest, RecordingStopResponse>(
    equipmentId, "recording/stop", body,
  );
}

export async function cancelRecording(
  equipmentId: string,
  body: RecordingCancelRequest = {},
): Promise<RecordingCancelResponse> {
  return controlPost<RecordingCancelRequest, RecordingCancelResponse>(
    equipmentId, "recording/cancel", body,
  );
}

// -- Plug / power-strip control -------------------------------------------

/** Toggle, turn on, or turn off a single outlet (or the whole strip if outlet is omitted). */
export async function postPlugSwitch(
  equipmentId: string,
  action: "on" | "off" | "toggle",
  outlet?: number,
): Promise<ControlAck> {
  const body: PlugSwitchRequest = { outlet: outlet ?? null };
  return controlPost<PlugSwitchRequest>(equipmentId, action, body);
}

// -- Press (Waters Filtration / filter_every_well) control -----------------
//
// Endpoint shapes mirror skills/.../skill_catalog/press.py. The device is
// at protocol v1.1 with claim semantics, but the dashboard's control
// passthrough handles the X-Claim-Token side internally (or omits it; the
// device accepts both per its README).

export async function postPressInit(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

export async function postPressStop(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "stop", {});
}

export async function postPressUp(
  equipmentId: string,
  hold_time: number = 0.5,
): Promise<ControlAck> {
  return controlPost(equipmentId, "press/up", { hold_time });
}

export async function postPressDown(
  equipmentId: string,
  hold_time: number = 0.5,
): Promise<ControlAck> {
  return controlPost(equipmentId, "press/down", { hold_time });
}

export async function postPlateIn(
  equipmentId: string,
  smooth: boolean = true,
): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/in", { smooth });
}

export async function postPlateOut(
  equipmentId: string,
  smooth: boolean = true,
): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/out", { smooth });
}

// -- Plate sealer (Agilent PlateLoc) control -------------------------------
//
// Endpoint shapes mirror skills/.../skill_catalog/plate_sealer.py. Argument
// ranges (20..235 °C, 0.5..12.0 s) are enforced server-side; the tile
// validates on the client too for UX.

export async function postSealerStartup(
  equipmentId: string,
  profile?: string | null,
): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", { profile: profile ?? null });
}

export async function postSealerShutdown(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "shutdown", {});
}

export async function postSealerSealStart(
  equipmentId: string,
  opts: { temperature_c?: number | null; seconds?: number | null } = {},
): Promise<ControlAck> {
  return controlPost(equipmentId, "seal/start", {
    temperature_c: opts.temperature_c ?? null,
    seconds: opts.seconds ?? null,
  });
}

export async function postSealerSealStop(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "seal/stop", {});
}

export async function postSealerSetTemperature(
  equipmentId: string,
  temperature_c: number,
): Promise<ControlAck> {
  return controlPost(equipmentId, "seal/temperature", { temperature_c });
}

export async function postSealerSetTime(
  equipmentId: string,
  seconds: number,
): Promise<ControlAck> {
  return controlPost(equipmentId, "seal/time", { seconds });
}

export async function postSealerStageIn(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "stage/in", {});
}

export async function postSealerStageOut(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "stage/out", {});
}

// -- Generic lifecycle (kinds without a dedicated tile) --------------------
//
// Exactly the standard STATUS_SPEC startup verb, offered by the generic
// EquipmentStatusCard when the device itself advertises it. Deliberately
// NOT `connect`: the xArm gates claims behind an operator-only /connect
// that must never be one click away (ROADMAP → xarm `do_not_call_connect`).

export async function postGenericStartup(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

// -- Shaker (Torrey Pines SC20 / torry-pines-shaker-server) ---------------
//
// Endpoint shapes mirror skills/.../skill_catalog/shaker.py. The device is
// STATUS_SPEC v1.1; per-request claim/release is handled by the dashboard's
// control passthrough.

export async function postShakerStartup(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

export async function postShakerShutdown(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "shutdown", {});
}

export async function postShakerShakeStart(
  equipmentId: string,
  body: {
    speed_level: number;
    temperature_c: number;
    duration_s: number;
    wait_for_temperature?: boolean;
  },
): Promise<ControlAck> {
  return controlPost(equipmentId, "shake/start", body);
}

export async function postShakerShakeStop(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "shake/stop", {});
}

export async function postShakerSetTemperature(
  equipmentId: string,
  temperature_c: number,
): Promise<ControlAck> {
  return controlPost(equipmentId, "shake/set_temperature", { temperature_c });
}

export async function postShakerSetSpeed(
  equipmentId: string,
  speed_level: number,
): Promise<ControlAck> {
  return controlPost(equipmentId, "shake/set_speed", { speed_level });
}

// The OT-2's write surface (lifecycle, lights, pause, deck declaration) lives
// in the gateway's own panel, framed at /ot2/{hte,complexation}/ui/ — see
// `lib/device-panels.ts`. `getDeckLayout` below stays: the tile reads deck
// state, it just no longer writes it.

// -- HPLC (Agilent UPLC-MS sidecar) ------------------------------------------
//
// Lifecycle + halt only. `standby` parks the instrument in low-flow standby
// (a true power-down is a deliberate manual procedure, not an API action);
// `abort` halts the current acquisition. run.submit / queue verbs need typed
// arg shapes and stay out of the tile.

export async function postHplcStartup(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

export async function postHplcStandby(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "standby", {});
}

export async function postHplcAbort(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "abort", {});
}

// -- Plate reader (BioTek Cytation 5 / agilent-cytation-server) -------------
//
// Lifecycle only for now (the ON toggle on the tile). The read/imaging verbs
// need typed arg shapes and land with the protocol-execution work.

export async function postPlateReaderStartup(
  equipmentId: string,
): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

export async function postPlateReaderShutdown(
  equipmentId: string,
): Promise<ControlAck> {
  return controlPost(equipmentId, "shutdown", {});
}

// -- Sash (fume hood) control ----------------------------------------------
//
// As of STATUS_SPEC v1.1 the actuator exposes `/control/sash/{move,stop}`;
// the dashboard's generic /api/equipment/{id}/control/{action} passthrough
// handles claim acquisition. Response is the full EquipmentStatus envelope.

export async function postSashMove(
  equipmentId: string,
  position: number,
): Promise<unknown> {
  return controlPost(equipmentId, "sash/move", { position });
}

export async function postSashStop(equipmentId: string): Promise<unknown> {
  return controlPost(equipmentId, "sash/stop", {});
}

// -- Robot arm (xArm) safety-floor actions ---------------------------------
//
// connect/disconnect (lifecycle) + move-stop/clear-errors live at the device
// ROOT (siblings of /status, outside /control/*) and are claim-exempt, so
// they ride the dashboard's /device/* proxy (api/app/control.py) — auth +
// audit, no claim dance. The device now aliases /control/stop and
// /control/clear_errors (2026-08-13), but do NOT fold those back into
// controlPost: the passthrough's claim→action→release dance would let a
// workflow's held claim block an operator's stop, which the safety floor
// must never allow. devicePost is the deliberate shape, not a workaround.

function devicePost<TResp = ControlAck>(
  equipmentId: string,
  action: string,
): Promise<TResp> {
  return fetchJson<TResp>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/device/${action}`,
    { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } },
  );
}

/** Connect the controller (INIT): requires_init → ready. */
export async function postArmConnect(equipmentId: string): Promise<ControlAck> {
  return devicePost(equipmentId, "connect");
}

/** Disconnect the controller (OFF): ready → requires_init. */
export async function postArmDisconnect(equipmentId: string): Promise<ControlAck> {
  return devicePost(equipmentId, "disconnect");
}

/** Halt current motion; stays connected. */
export async function postArmStop(equipmentId: string): Promise<ControlAck> {
  return devicePost(equipmentId, "move/stop");
}

/** Clear a fault so motion can resume. */
export async function postArmClear(equipmentId: string): Promise<ControlAck> {
  return devicePost(equipmentId, "clear/errors");
}

// -- Plate stacker (Agilent BioStack 4) control ----------------------------
//
// Endpoint shapes mirror skills/.../skill_catalog/plate_stacker.py. The
// device is STATUS_SPEC v1.1 with hard X-Claim-Token enforcement; the
// dashboard's generic /control/{action} passthrough does the per-request
// claim acquire / release. All six actions take an empty body, and the
// skill name is the control action segment verbatim (no dots).

export async function postStackerStartup(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

export async function postStackerShutdown(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "shutdown", {});
}

export async function postStackerHome(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "home", {});
}

export async function postStackerStagePlate(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "stage_plate", {});
}

export async function postStackerPresentPlate(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "present_plate", {});
}

export async function postStackerHandoff(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "handoff", {});
}

// -- Solid doser (dose_every_well) control ---------------------------------
//
// Endpoint shapes mirror skills/.../skill_catalog/solid_doser.py. The device
// is STATUS_SPEC v1.1 with hard X-Claim-Token enforcement on /control/*; the
// dashboard's generic /control/{action} passthrough does the per-request
// claim acquire / release.

export async function postDoserStartup(
  equipmentId: string,
  config_name: string = "with_cnc_solid_doser",
): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", { config_name });
}

export async function postDoserShutdown(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "shutdown", {});
}

export async function postDoserHome(equipmentId: string): Promise<ControlAck> {
  // Device endpoint is /control/home; the passthrough prepends /control/,
  // so the action segment is just "home" (not "control/home", which would
  // resolve to the non-existent /control/control/home).
  return controlPost(equipmentId, "home", {});
}

export async function postDoserTare(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "tare", {});
}

export interface DoserBalanceReading {
  mass_g: number;
  mass_mg: number;
}

// Read-only; the device doesn't gate this behind a claim (see
// GET /control/read-balance), so it's cheap to call on demand from a
// "Weigh" button rather than poll continuously (the reading fluctuates
// with air currents/vibration, so a live-updating number is more noise
// than signal — the operator asks for a fresh read when they want one).
export async function getDoserBalanceReading(
  equipmentId: string,
): Promise<DoserBalanceReading> {
  return controlGet(equipmentId, "read-balance");
}

// Full sequences (open+lower+close, or open+raise). Not currently surfaced
// on the dashboard tile, which exposes the granular lid/lift moves below
// instead so an operator can drive each axis independently; kept here as
// the typed wrapper for the device's full-sequence endpoints.
export async function postDoserPlateLoad(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/load", {});
}

export async function postDoserPlateUnload(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/unload", {});
}

// Granular single-axis moves for manual plate placement/removal.
export async function postDoserOpenLid(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "lid/open", {});
}

export async function postDoserCloseLid(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "lid/close", {});
}

export async function postDoserRaisePlate(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/raise", {});
}

export async function postDoserLowerPlate(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/lower", {});
}

export async function postDoserDoseWell(
  equipmentId: string,
  well: string,
  target_mg: number,
  verify: boolean = true,
  use_pid: boolean = false,
): Promise<ControlAck> {
  return controlPost(equipmentId, "dose/well", { well, target_mg, verify, use_pid });
}

export interface DoseResult {
  well: string;
  position: [number, number];
  target_mg: number;
  initial_mg?: number | null;
  final_mg?: number | null;
  actual_mg?: number | null;
  error_mg?: number | null;
}

export interface DoseMultipleResponse {
  results: Record<string, DoseResult>;
  total_wells: number;
  successful_wells: number;
}

// well_targets maps well name -> target mass in mg (e.g. an operator's
// multi-select in the well grid, all doses to the same target).
export async function postDoserDoseMultiple(
  equipmentId: string,
  well_targets: Record<string, number>,
  verify: boolean = true,
  use_pid: boolean = false,
): Promise<DoseMultipleResponse> {
  return controlPost<
    { well_targets: Record<string, number>; verify: boolean; use_pid: boolean },
    DoseMultipleResponse
  >(equipmentId, "dose/multiple", { well_targets, verify, use_pid });
}

export async function postDoserDoseAll(
  equipmentId: string,
  target_mg: number,
  verify: boolean = true,
  use_pid: boolean = false,
): Promise<ControlAck> {
  return controlPost(equipmentId, "dose/all", { target_mg, verify, use_pid });
}

// -- Plate labware (definitions + current-plate status) -------------------
//
// Sibling namespace to /control/* on the device (`/plate/definitions`,
// `/plate/status`), proxied by the dashboard's generic plate passthrough
// (api/app/control.py `plate_get`). Both are read-only and not
// claim-gated on the device.

export interface PlateDefinitionInfo {
  key: string;
  name: string;
  rows: number;
  columns: number;
  total_wells: number;
  well_spacing_mm: number;
  well_diameter_mm: number;
  well_depth_mm: number;
  well_volume_ul: number;
  plate_type: string;
}

export async function getDoserPlateDefinitions(
  equipmentId: string,
): Promise<PlateDefinitionInfo[]> {
  return fetchJson<PlateDefinitionInfo[]>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/plate/definitions`,
  );
}

export interface WellInfo {
  name: string;
  row: number;
  column: number;
  position_x: number;
  position_y: number;
  current_mass_mg: number;
  target_mass_mg?: number | null;
  dosed: boolean;
}

export interface PlateStatusInfo {
  name: string;
  rows: number;
  columns: number;
  total_wells: number;
  dosed_wells: number;
  undosed_wells: number;
  origin: [number, number];
  wells: WellInfo[];
}

// Only meaningful once a plate has been set (`postDoserSetPlate`) — the
// device 500s with "No plate currently set" otherwise (surfaced as an
// ApiError), which callers should treat as "no plate set yet" rather than
// a real fault.
export async function getDoserPlateStatus(
  equipmentId: string,
): Promise<PlateStatusInfo> {
  return fetchJson<PlateStatusInfo>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/plate/status`,
  );
}

export async function postDoserSetPlate(
  equipmentId: string,
  definition: string,
  origin_x: number = 0.0,
  origin_y: number = 0.0,
): Promise<ControlAck> {
  return controlPost(equipmentId, "plate/set", { definition, origin_x, origin_y });
}

export async function startRolling(
  equipmentId: string,
  body: RollingStartRequest = {},
): Promise<ControlAck> {
  return controlPost<RollingStartRequest, ControlAck>(equipmentId, "rolling/start", body);
}

export async function stopRolling(
  equipmentId: string,
): Promise<RollingStopResponse> {
  return controlPost<Record<string, never>, RollingStopResponse>(equipmentId, "rolling/stop", {});
}

// `/media` and `/media/<kind>/<lens>/<file>` aren't `/control/...`
// routes - they live under the camera namespace directly. The dashboard
// API has a passthrough router that forwards the same path shape onto
// the gateway, so the browser only ever talks to the dashboard origin.
export async function getCameraMedia(
  equipmentId: string,
): Promise<MediaListing> {
  return fetchJson<MediaListing>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/media`,
  );
}

/**
 * Translate a gateway-side relative URL into a dashboard-proxied absolute path.
 *
 * The gateway emits `url = "/cameras/<id>/media/<kind>/<lens>/<name>"`. The
 * dashboard's passthrough router exposes the same structure under
 * `/api/equipment/<id>/media/<kind>/<lens>/<name>`. This helper converts
 * the former to the latter so callers can drop the value straight into
 * an `<a href>` or `<img src>` without worrying about hostnames.
 */
export function mediaUrlForBrowser(
  equipmentId: string,
  gatewayUrl: string,
): string {
  // Pull off everything after `/media/` from the gateway URL.
  const idx = gatewayUrl.indexOf("/media/");
  if (idx === -1) return gatewayUrl;
  const tail = gatewayUrl.slice(idx + "/media/".length);
  return `/api/equipment/${encodeURIComponent(equipmentId)}/media/${tail}`;
}

// ---------------------------------------------------------------------------
// Deck layout (shared, server-persisted). Stopgap until a device publishes its
// own deck state on /status; see api/app/deck.py.
// ---------------------------------------------------------------------------

export interface DeckLayout {
  /** Slot number (as string, "1".."12") -> labware key ("96-well" | "24-well"). */
  slots: Record<string, string>;
}

export async function getDeckLayout(equipmentId: string): Promise<DeckLayout> {
  return fetchJson<DeckLayout>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/deck`,
  );
}

export async function putDeckLayout(
  equipmentId: string,
  slots: Record<string, string>,
): Promise<DeckLayout> {
  return fetchJson<DeckLayout>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/deck`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slots }),
    },
  );
}

// -- Device-published deck state (retires the deck_layouts.json stopgap) ------
//
// A migrated OT-2 gateway publishes its own normalized deck on
// `status.details.snapshot.deck` (see DeviceDeck below), and accepts the
// operator-declared layout via `POST /control/deck/declare` (a claim-gated
// control action, so it routes through the dashboard passthrough and is
// audited like any other control write). The tile prefers this over the
// legacy get/putDeckLayout store and falls back to it for un-migrated devices.

/** One slot of the gateway's normalized deck (details.snapshot.deck.slots). */
/** One well of the orchestrator-tracked plate (deck slot `labware.wells`). */
export interface WellSample {
  well: string;
  sample_id?: string | null;
  volume_ul?: number | null;
  notes?: string | null;
}

export interface DeviceDeckSlot {
  labware: {
    kind: string;
    load_name: string;
    display_name?: string | null;
    is_tiprack?: boolean;
    rows?: number | null;
    columns?: number | null;
    plate_id?: string | null;
    /** Tracked plate samples the deck folds onto this slot. */
    wells?: WellSample[] | null;
    /** The setup recipe's name for this labware — the key `details.tip_racks`
     *  and `/control/*` use. Stamped per slot by the gateway so it survives
     *  run/REPL precedence, unlike `display_name`. */
    nickname?: string | null;
  } | null;
  module: { module_name: string; status?: string | null; serial_number?: string | null } | null;
  slot_state: "empty" | "declared" | "occupied" | "in_use" | "mismatch";
  source: "run" | "repl" | "declared" | "empty";
  declared?: { kind: string; load_name: string } | null;
}

export interface DeviceDeck {
  source: "run" | "repl" | "declared" | "empty";
  slots: Record<string, DeviceDeckSlot>;
  timestamp?: string;
}

/** One attached hardware module from `details.robot.modules` — live telemetry
 *  straight off the robot (refreshes ~5 s whenever the module is powered,
 *  independent of any run). Distinct from the deck's declared module, which is
 *  operator intent; the tile pairs the two by serial or module family. */
export interface RobotModule {
  model: string; // e.g. "temperatureModuleV2"
  type: string; // e.g. "temperatureModuleType"
  serial?: string | null;
  id?: string | null;
  status?: string | null; // e.g. "idle" | "heating" | "cooling" | "holding at target"
  current_temperature?: number | null;
  target_temperature?: number | null;
}

// Declaring the layout (`POST`/`DELETE /control/deck/declare`) is done from
// the gateway's own panel — see the note above `getDeckLayout`.

// -- Central custom-labware store (/api/labware) -----------------------------
//
// Two merged sources served by api/app/labware.py: repo-committed
// (<repo>/labware/*.json, PR-reviewed) and uploaded (data/labware/).
// Reads are public; POST/DELETE are session-gated at the middleware — any
// signed-in role may write, as of 2026-08-18 (previously admin-only).

export interface LabwareSummary {
  load_name: string;
  display_name: string;
  display_category: string;
  is_tiprack: boolean;
  rows: number;
  columns: number;
  well_count: number;
  well_volume_ul?: number | null;
  version?: number | null;
  namespace?: string | null;
  vendor?: string | null;
  product_numbers: string[];
  product_links: string[];
  source: "repo" | "uploaded" | "standard";
  /** ac_auth principal (X-Auth-User) who first saved this upload. Null for
   *  repo / standard / legacy raw uploads that have not been re-saved. */
  created_by?: string | null;
  created_at?: string | null;
  /** ac_auth principal who last overwrote this upload. */
  updated_by?: string | null;
  updated_at?: string | null;
}

export async function getLabwareList(): Promise<{ definitions: LabwareSummary[] }> {
  return fetchJson<{ definitions: LabwareSummary[] }>("/api/labware");
}

/** Every official Opentrons definition (latest schema-2 version each),
 *  served from the opentrons-shared-data package on the dashboard host. */
export async function getStandardLabwareList(): Promise<{ definitions: LabwareSummary[] }> {
  return fetchJson<{ definitions: LabwareSummary[] }>("/api/labware/standard");
}

export async function getStandardLabwareDefinition(
  loadName: string,
): Promise<{ source: string; definition: Record<string, unknown> }> {
  return fetchJson(`/api/labware/standard/${encodeURIComponent(loadName)}`);
}

export async function getLabwareDefinition(
  loadName: string,
): Promise<{ source: string; definition: Record<string, unknown> }> {
  return fetchJson(`/api/labware/${encodeURIComponent(loadName)}`);
}

export async function postLabware(
  definition: Record<string, unknown>,
): Promise<LabwareSummary> {
  return fetchJson<LabwareSummary>("/api/labware", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ definition }),
  });
}

export async function deleteLabware(loadName: string): Promise<void> {
  return fetchJson<void>(`/api/labware/${encodeURIComponent(loadName)}`, {
    method: "DELETE",
  });
}


// -- Plate custody (docs/PLATE_TRACKING.md D5–D8) -------------------------------
//
// Where every plate is, per the record layer's custody ledger — a read-through
// (the dashboard keeps no copy), and the human front door for a bench-top move,
// which writes the same `move` row the run executor writes.

export interface CustodyPlate {
  hid: string;
  container_id: string;
  container_type: string | null;
  model: string | null;
  status: string | null;
  location_id: string | null;
  /** Registry name (e.g. `ot2_hte/slot_2`), or null when never placed. */
  location: string | null;
  equipment_id: string | null;
  project_id: string | null;
}

export interface CustodyAction {
  action_id: string;
  action_type: string;
  to_location_id: string | null;
  source_container_id: string | null;
  target_container_id: string | null;
  performed_by: string;
  performed_at: string;
  step_id: string | null;
  plan_id: string | null;
  params: Record<string, unknown>;
}

export interface CustodyMoveRequest {
  hid: string;
  to: string;
  note?: string;
  performed_by?: string;
}

export interface CustodyMoveResponse {
  recorded: boolean;
  hid: string;
  to: string;
  action_id?: string;
}

export async function getCustodyPlates(): Promise<{ plates: CustodyPlate[] }> {
  return fetchJson<{ plates: CustodyPlate[] }>("/api/custody/plates");
}

export async function getCustodyPlate(
  hid: string,
): Promise<CustodyPlate & { history: CustodyAction[] }> {
  return fetchJson<CustodyPlate & { history: CustodyAction[] }>(
    `/api/custody/plates/${encodeURIComponent(hid)}`,
  );
}

export async function postCustodyMove(body: CustodyMoveRequest): Promise<CustodyMoveResponse> {
  return fetchJson<CustodyMoveResponse>("/api/custody/move", {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

export interface LocationEntry {
  name: string;
  type: string;
  equipment: string | null;
  capacity: number | null;
  label: string | null;
  active: boolean;
  aliases: Record<string, string | string[]>;
  notes: string | null;
}

/** The registry of places a container can be (`locations.yaml`). */
export async function getLocations(): Promise<{ locations: LocationEntry[] }> {
  return fetchJson<{ locations: LocationEntry[] }>("/api/locations");
}
