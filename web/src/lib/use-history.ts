"use client";

import { useQuery } from "@tanstack/react-query";
import {
  getAllUptime,
  getDeviceUptime,
  getEquipmentEvents,
  getSensorHistory,
  getLatestSensors,
  getRuns,
  getWellResults,
} from "./history-api";

// History data refreshes every 30 s — not every 2.5 s like live status.
const REFETCH_MS = 30_000;

export function useAllUptime(days = 7) {
  return useQuery({
    queryKey: ["history", "uptime", "all", days],
    queryFn: () => getAllUptime(days),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
  });
}

export function useDeviceUptime(deviceId: string, days = 7) {
  return useQuery({
    queryKey: ["history", "uptime", deviceId, days],
    queryFn: () => getDeviceUptime(deviceId, days),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
    enabled: !!deviceId,
  });
}

export function useEquipmentEvents(deviceId: string, limit = 50) {
  return useQuery({
    queryKey: ["history", "events", deviceId, limit],
    queryFn: () => getEquipmentEvents(deviceId, limit),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
    enabled: !!deviceId,
  });
}

export function useSensorHistory(
  sensorId: string,
  metric: string,
  sinceHours: number,
  enabled = true,
) {
  return useQuery({
    queryKey: ["history", "sensors", sensorId, metric, sinceHours],
    queryFn: () => getSensorHistory(sensorId, metric, sinceHours),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
    enabled: enabled && !!sensorId && !!metric,
  });
}

export function useLatestSensors() {
  return useQuery({
    queryKey: ["history", "sensors", "latest"],
    queryFn: getLatestSensors,
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
  });
}

export function useRuns(limit = 20, deviceId?: string) {
  return useQuery({
    queryKey: ["history", "runs", limit, deviceId],
    queryFn: () => getRuns(limit, deviceId),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
  });
}

export function useWellResults(runId: string, enabled = true) {
  return useQuery({
    queryKey: ["history", "wells", runId],
    queryFn: () => getWellResults(runId),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS / 2,
    enabled: enabled && !!runId,
  });
}
