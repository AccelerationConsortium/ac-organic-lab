/**
 * Fetch helpers for the /api/history/* and /api/ingest/* endpoints.
 *
 * All functions hit the dashboard API (same origin) so no CORS issues.
 * They are intentionally thin — just fetch + throw on error.
 */

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = typeof body?.detail === "string" ? body.detail : undefined;
    } catch {
      detail = undefined;
    }
    throw new Error(detail ?? `${res.status} ${res.statusText} from ${path}`);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UptimeEvent {
  ts: string;
  event: "up" | "down" | "unreachable" | "recovered";
  consecutive_failures: number;
}

export interface DeviceUptimeSummary {
  device_id: string;
  days: number;
  uptime_pct: number | null;
  last_event: Pick<UptimeEvent, "ts" | "event"> | null;
  /** % of window spent in each equipment state, e.g. {ready: 45.2, unreachable: 54.8} */
  state_pcts: Record<string, number>;
}

export interface AllUptimeResponse {
  devices: Record<string, DeviceUptimeSummary>;
  days: number;
}

export interface UptimeDetailResponse {
  device_id: string;
  days: number;
  uptime_pct: number;
  events: UptimeEvent[];
}

export interface EquipmentEvent {
  ts: string;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  message: string | null;
  payload: Record<string, unknown> | null;
}

export interface EquipmentEventsResponse {
  device_id: string;
  events: EquipmentEvent[];
}

export interface SensorPoint {
  ts: string;
  value: number;
  unit: string;
}

export interface SensorHistoryResponse {
  sensor_id: string;
  metric: string;
  since_hours: number;
  readings: SensorPoint[];
}

export interface LatestSensorEntry {
  sensor_id: string;
  metric: string;
  value: number;
  unit: string;
  ts: string;
}

export interface LatestSensorsResponse {
  readings: LatestSensorEntry[];
}

export interface RunRecord {
  id: string;
  started_at: string;
  finished_at: string | null;
  device_id: string;
  config_name: string | null;
  plate_id: string | null;
  compound_id: string | null;
  target_mg: number | null;
  n_wells: number;
  n_converged: number;
  status: "in_progress" | "complete" | "failed" | "aborted";
}

export interface RunsResponse {
  runs: RunRecord[];
}

export interface WellResult {
  id: number;
  run_id: string;
  ts: string;
  well: string;
  target_mg: number;
  actual_mg: number | null;
  converged: number;
  iterations: number | null;
  duration_s: number | null;
}

export interface WellResultsResponse {
  run_id: string;
  wells: WellResult[];
}

// ---------------------------------------------------------------------------
// Fetch functions
// ---------------------------------------------------------------------------

export async function getAllUptime(days = 7): Promise<AllUptimeResponse> {
  return fetchJson<AllUptimeResponse>(`/api/history/uptime?days=${days}`);
}

export async function getDeviceUptime(
  deviceId: string,
  days = 7,
): Promise<UptimeDetailResponse> {
  return fetchJson<UptimeDetailResponse>(
    `/api/history/uptime/${encodeURIComponent(deviceId)}?days=${days}`,
  );
}

export async function getEquipmentEvents(
  deviceId: string,
  limit = 50,
): Promise<EquipmentEventsResponse> {
  return fetchJson<EquipmentEventsResponse>(
    `/api/history/events/${encodeURIComponent(deviceId)}?limit=${limit}`,
  );
}

export async function getSensorHistory(
  sensorId: string,
  metric: string,
  sinceHours = 24,
): Promise<SensorHistoryResponse> {
  return fetchJson<SensorHistoryResponse>(
    `/api/history/sensors/${encodeURIComponent(sensorId)}/${encodeURIComponent(metric)}?since_hours=${sinceHours}`,
  );
}

export async function getLatestSensors(): Promise<LatestSensorsResponse> {
  return fetchJson<LatestSensorsResponse>("/api/history/sensors/latest");
}

export async function getRuns(limit = 20, deviceId?: string): Promise<RunsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (deviceId) params.set("device_id", deviceId);
  return fetchJson<RunsResponse>(`/api/history/runs?${params}`);
}

export async function getWellResults(runId: string): Promise<WellResultsResponse> {
  return fetchJson<WellResultsResponse>(
    `/api/history/runs/${encodeURIComponent(runId)}/wells`,
  );
}
