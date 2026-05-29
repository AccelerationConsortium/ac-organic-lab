import { useQuery } from '@tanstack/react-query'
import type { EquipmentList, PlatformsConfig } from '../types/api'
import { apiClient } from '../services/api'

export function useEquipmentList() {
  return useQuery<EquipmentList>({
    queryKey: ['equipment'],
    queryFn: () => apiClient.get<EquipmentList>('/api/equipment'),
    refetchInterval: 10_000,
  })
}

export function usePlatforms() {
  return useQuery<PlatformsConfig>({
    queryKey: ['platforms'],
    queryFn: () => apiClient.get<PlatformsConfig>('/api/platforms'),
    staleTime: 60_000,
  })
}
