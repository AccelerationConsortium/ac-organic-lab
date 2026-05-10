"use client";

import { useQuery } from "@tanstack/react-query";
import { getEquipmentList, getEquipmentStatus } from "./api";

export function useEquipmentList(refetchIntervalMs = 2500) {
  return useQuery({
    queryKey: ["equipment"],
    queryFn: getEquipmentList,
    refetchInterval: refetchIntervalMs,
    staleTime: 1500,
  });
}

export function useEquipmentStatus(id: string, refetchIntervalMs = 2500) {
  return useQuery({
    queryKey: ["equipment", id],
    queryFn: () => getEquipmentStatus(id),
    refetchInterval: refetchIntervalMs,
    staleTime: 1500,
  });
}
