import { apiClient } from './api'

export interface UptimeLastEvent { ts: string; event: string }
export interface UptimeResponse {
  device_id: string
  days: number
  uptime_pct: number
  last_event: UptimeLastEvent | null
  state_pcts: Record<string, number>
}
export interface SensorPoint { ts: string; value: number; unit: string }
export interface SensorHistoryResponse { sensor_id: string; metric: string; since_hours: number; readings: SensorPoint[] }
export interface RunRecord { id: string; started_at: string; finished_at?: string; device_id: string; status: string; n_wells: number; n_converged: number }
export interface EquipmentEvent { id: number; ts: string; device_id: string; event_type: string; from_state?: string; to_state?: string; message?: string }

type AllUptimeRaw = { devices: Record<string, UptimeResponse>; days: number }

async function allUptimeFetch(days: number): Promise<{ devices: UptimeResponse[] }> {
  const raw = await apiClient.get<AllUptimeRaw>(`/api/history/uptime?days=${days}`)
  return { devices: Object.values(raw.devices) }
}

export const historyApi = {
  allUptime:     (days = 30)                           => allUptimeFetch(days),
  deviceUptime:  (id: string, days = 30)               => apiClient.get<UptimeResponse>(`/api/history/uptime/${id}?days=${days}`),
  deviceEvents:  (id: string, limit = 50)              => apiClient.get<{ events: EquipmentEvent[] }>(`/api/history/events/${id}?limit=${limit}`),
  sensorHistory: (sid: string, metric: string, hours = 24) =>
    apiClient.get<SensorHistoryResponse>(`/api/history/sensors/${sid}/${metric}?hours=${hours}`),
  latestSensors: ()                                    => apiClient.get<{ readings: { sensor_id: string; metric: string; value: number; unit: string; ts: string }[] }>('/api/history/sensors/latest'),
  runs:          (limit = 20)                          => apiClient.get<{ runs: RunRecord[] }>(`/api/history/runs?limit=${limit}`),
}
