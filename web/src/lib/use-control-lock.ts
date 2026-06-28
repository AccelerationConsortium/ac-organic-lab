"use client";

import { useCallback } from "react";
import { useUserAuth } from "@/lib/user-auth";

export interface UseControlLockResult {
  /** True when controls should be disabled — i.e. the user is not signed in. */
  locked: boolean;
  /** Vestigial (no auto-relock countdown anymore); always 0. */
  countdown: number;
  /** When signed out, nudge the login bar into view; otherwise a no-op. */
  unlock: () => Promise<void>;
  /** No-op, kept for call-site compatibility. */
  lock: () => void;
  /** Alias of unlock(). */
  toggle: () => Promise<void>;
}

/**
 * Control-gate state for tiles.
 *
 * The dashboard is view-only until sign-in, so a tile's destructive controls
 * are simply enabled when the user is authenticated. There is no per-tile
 * password or 10 s auto-relock anymore — that was the old CONTROL_PASSWORD
 * model; the single login bar is now the only gate. `locked` mirrors "not
 * signed in"; `unlock()` / `toggle()` flash the login bar when signed out.
 */
export function useControlLock(): UseControlLockResult {
  const { authenticated, requestLogin } = useUserAuth();
  const locked = !authenticated;

  const unlock = useCallback(async () => {
    if (!authenticated) requestLogin();
  }, [authenticated, requestLogin]);

  const lock = useCallback(() => {}, []);

  return { locked, countdown: 0, unlock, lock, toggle: unlock };
}
