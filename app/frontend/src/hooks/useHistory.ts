import { useQuery } from '@tanstack/react-query'
import { historyApi } from '../services/historyApi'

export const useAllUptime     = (days = 30)                           => useQuery({ queryKey: ['uptime', days],           queryFn: () => historyApi.allUptime(days),                 refetchInterval: 60_000 })
export const useDeviceUptime  = (id: string, days = 30)              => useQuery({ queryKey: ['uptime', id, days],        queryFn: () => historyApi.deviceUptime(id, days),          enabled: !!id })
export const useDeviceEvents  = (id: string)                         => useQuery({ queryKey: ['events', id],              queryFn: () => historyApi.deviceEvents(id),                enabled: !!id })
export const useSensorHistory = (sid: string, metric: string, hours = 24) =>
  useQuery({ queryKey: ['sensor', sid, metric, hours], queryFn: () => historyApi.sensorHistory(sid, metric, hours), refetchInterval: 60_000 })
export const useLatestSensors = ()                                   => useQuery({ queryKey: ['sensors-latest'],          queryFn: historyApi.latestSensors,                         refetchInterval: 30_000 })
export const useRuns          = (limit = 20)                         => useQuery({ queryKey: ['runs', limit],             queryFn: () => historyApi.runs(limit),                     refetchInterval: 30_000 })
