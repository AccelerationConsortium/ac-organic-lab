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
