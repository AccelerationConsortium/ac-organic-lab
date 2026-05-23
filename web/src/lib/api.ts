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

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = typeof body?.detail === "string" ? body.detail : undefined;
    } catch {
      detail = undefined;
    }
    throw new Error(detail ?? `${response.status} ${response.statusText} from ${path}`);
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

// -- Sash (legacy fume hood) control ---------------------------------------

export interface SashCommandResponse {
  equipment_status?: string;
  sash_position?: number | null;
  target_position?: number | null;
  sash_state?: string;
  is_moving?: boolean;
  message?: string;
  error?: string;
}

export async function postSashMove(
  equipmentId: string,
  position: number,
): Promise<SashCommandResponse> {
  return fetchJson<SashCommandResponse>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/sash/move`,
    {
      method: "POST",
      body: JSON.stringify({ position }),
      headers: { "Content-Type": "application/json" },
    },
  );
}

export async function postSashStop(
  equipmentId: string,
): Promise<SashCommandResponse> {
  return fetchJson<SashCommandResponse>(
    `/api/equipment/${encodeURIComponent(equipmentId)}/sash/stop`,
    {
      method: "POST",
      body: "{}",
      headers: { "Content-Type": "application/json" },
    },
  );
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
