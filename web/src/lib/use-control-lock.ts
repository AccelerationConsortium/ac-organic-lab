"use client";

import { useCallback } from "react";
import { useUserAuth } from "@/lib/user-auth";

export interface UseControlLockResult {
  /** True when controls should be disabled — signed out, or signed in
   *  without a role on this equipment. */
  locked: boolean;
  /** True when signed in but not authorized on this equipment (so tiles can
   *  say "no access" instead of "sign in"). */
  noAccess: boolean;
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
 * The dashboard is view-only until sign-in, and — since Phase 2 — a tile's
 * destructive controls are enabled only when the signed-in user holds a role
 * on *that equipment* (`canControl(equipmentId)`, backed by the auth
 * sidecar's /authz/mine map). Pass the tile's `snapshot.id`; omitting it
 * keeps the old authentication-only behavior. `locked` is UX: the control
 * passthrough enforces the same answer server-side (403).
 */
export function useControlLock(equipmentId?: string): UseControlLockResult {
  const { authenticated, canControl, requestLogin } = useUserAuth();
  const authorized = equipmentId ? canControl(equipmentId) : authenticated;
  const locked = !authenticated || !authorized;
  const noAccess = authenticated && !authorized;

  const unlock = useCallback(async () => {
    if (!authenticated) requestLogin();
  }, [authenticated, requestLogin]);

  const lock = useCallback(() => {}, []);

  return { locked, noAccess, countdown: 0, unlock, lock, toggle: unlock };
}
