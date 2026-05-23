"use client";

import { useEffect, useRef, useState } from "react";
import { useControlAuth } from "@/lib/control-auth";

const DEFAULT_UNLOCK_SECONDS = 5;

export interface UseControlLockResult {
  /** True when controls are locked; tiles should disable destructive UI. */
  locked: boolean;
  /** Seconds remaining before auto-relock; meaningless while locked. */
  countdown: number;
  /**
   * Unlock controls and start the auto-relock countdown. When the dashboard
   * has CONTROL_PASSWORD enabled and the user is not yet authenticated,
   * this first pops the shared password modal; the unlock only proceeds on
   * successful auth.
   */
  unlock: () => Promise<void>;
  /** Lock controls immediately and clear any running countdown. */
  lock: () => void;
  /** Convenience: toggle between locked and unlocked. */
  toggle: () => Promise<void>;
}

/**
 * Shared lock state for control tiles.
 *
 * When unlocked, a `unlockSeconds`-long countdown begins; on reaching zero
 * the controls auto-relock. Calling `lock()` or `unlock()` directly always
 * supersedes the countdown.
 *
 * One hook instance per tile - state is intentionally local (each tile has
 * its own lock chip). For a single dashboard-wide lock that would change.
 */
export function useControlLock(
  opts: { unlockSeconds?: number } = {},
): UseControlLockResult {
  const unlockSeconds = opts.unlockSeconds ?? DEFAULT_UNLOCK_SECONDS;
  const { ensureAuth } = useControlAuth();

  const [locked, setLocked] = useState(true);
  const [countdown, setCountdown] = useState(unlockSeconds);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function clearTimer() {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  function lock() {
    clearTimer();
    setLocked(true);
    setCountdown(unlockSeconds);
  }

  async function unlock() {
    const ok = await ensureAuth();
    if (!ok) return;

    clearTimer();
    setCountdown(unlockSeconds);
    setLocked(false);

    let remaining = unlockSeconds;
    timerRef.current = setInterval(() => {
      remaining -= 1;
      setCountdown(remaining);
      if (remaining <= 0) lock();
    }, 1000);
  }

  async function toggle() {
    if (locked) await unlock();
    else lock();
  }

  useEffect(() => () => clearTimer(), []);

  return { locked, countdown, unlock, lock, toggle };
}
