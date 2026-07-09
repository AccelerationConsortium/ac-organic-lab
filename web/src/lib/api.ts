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

// -- Liquid handler (OT-2) control -----------------------------------------
//
// The OT-2 gateway exposes `/control/lights` as a claim-gated convenience
// control (the dashboard's generic passthrough handles claim acquire /
// release per request). Like every control write it requires a signed-in
// session; the tile disables the toggle when logged out.

export async function postSetLights(
  equipmentId: string,
  on: boolean,
): Promise<unknown> {
  return controlPost(equipmentId, "lights", { on });
}

// Lifecycle: `/control/startup` initialises/homes the robot; `/control/shutdown`
// powers it down (the tile's ON toggle pairs these). `/control/pause` pauses a
// running protocol — the closest thing the OT-2 has to a motion halt, wired to
// the tile's STOP. All are claim-gated and require a signed-in session (unlike
// lights, they are NOT a convenience-class bypass in the middleware).
export async function postOt2Startup(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "startup", {});
}

export async function postOt2Shutdown(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "shutdown", {});
}

export async function postOt2Pause(equipmentId: string): Promise<ControlAck> {
  return controlPost(equipmentId, "pause", {});
}

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
// they can't go through controlPost. They ride the dashboard's /device/*
// proxy (api/app/control.py) — auth + audit, no claim dance. TODO: fold back
// into controlPost once the device exposes /control/* aliases.

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
