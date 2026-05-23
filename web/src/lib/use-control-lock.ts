"use client";

import { useEffect, useRef, useState } from "react";

const DEFAULT_UNLOCK_SECONDS = 5;

export interface UseControlLockResult {
  /** True when controls are locked; tiles should disable destructive UI. */
  locked: boolean;
  /** Seconds remaining before auto-relock; meaningless while locked. */
  countdown: number;
  /** Unlock controls and start the auto-relock countdown. */
  unlock: () => void;
  /** Lock controls immediately and clear any running countdown. */
  lock: () => void;
  /** Convenience: toggle between locked and unlocked. */
  toggle: () => void;
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

  function unlock() {
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

  function toggle() {
    if (locked) unlock();
    else lock();
  }

  useEffect(() => () => clearTimer(), []);

  return { locked, countdown, unlock, lock, toggle };
}
