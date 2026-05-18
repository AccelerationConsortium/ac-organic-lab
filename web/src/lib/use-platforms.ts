"use client";

import { useQuery } from "@tanstack/react-query";
import { getPlatforms } from "./api";

export function usePlatforms() {
  return useQuery({
    queryKey: ["platforms"],
    queryFn: getPlatforms,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}
