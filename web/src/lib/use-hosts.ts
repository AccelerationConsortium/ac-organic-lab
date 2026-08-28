"use client";

import { useQuery } from "@tanstack/react-query";
import { getLabHosts } from "./api";

/** Host inventory (`/api/hosts`) — derived from equipment.yaml + the SSH
 *  whitelist, so it only changes on an API restart. Config-style caching,
 *  like usePlatforms. */
export function useLabHosts() {
  return useQuery({
    queryKey: ["lab-hosts"],
    queryFn: getLabHosts,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}
