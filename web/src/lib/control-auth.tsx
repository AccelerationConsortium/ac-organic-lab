"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { PasswordModal } from "@/components/PasswordModal";

interface ControlAuthValue {
  /** True until the GET /api/control-unlock status check returns. */
  loading: boolean;
  /** Server says CONTROL_PASSWORD is set on the web service. */
  enabled: boolean;
  /** Cookie is currently valid (best-effort - may be stale by up to 30 min). */
  authenticated: boolean;
  /**
   * Resolves true when the caller may proceed with a control action.
   * When disabled or already authenticated, resolves immediately. Otherwise
   * pops the password modal and resolves on success / false on cancel.
   */
  ensureAuth: () => Promise<boolean>;
}

const ControlAuthContext = createContext<ControlAuthValue>({
  loading: false,
  enabled: false,
  authenticated: true,
  ensureAuth: async () => true,
});

interface PendingPrompt {
  resolve: (ok: boolean) => void;
}

export function ControlAuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [pending, setPending] = useState<PendingPrompt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Lifecycle: query the server once on mount to learn whether a password
  // is configured and whether we already hold a valid cookie.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/control-unlock", { cache: "no-store" })
      .then((r) => r.json())
      .then((data: { enabled: boolean; authenticated: boolean }) => {
        if (cancelled) return;
        setEnabled(Boolean(data.enabled));
        setAuthenticated(Boolean(data.authenticated));
      })
      .catch(() => {
        // Failed to reach the route - leave defaults (disabled, unauth).
        // The server-side middleware is the actual authority, so the
        // worst case is the user gets a 401 on first control action and
        // we recover from there.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Use a ref so ensureAuth's identity stays stable but reads current state.
  const stateRef = useRef({ enabled, authenticated });
  stateRef.current = { enabled, authenticated };

  const ensureAuth = useCallback(async (): Promise<boolean> => {
    const { enabled: en, authenticated: ok } = stateRef.current;
    if (!en || ok) return true;
    return new Promise<boolean>((resolve) => {
      setError(null);
      setPending({ resolve });
    });
  }, []);

  async function handleSubmit(password: string) {
    setBusy(true);
    try {
      const response = await fetch("/api/control-unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (response.ok) {
        setAuthenticated(true);
        setError(null);
        if (pending) pending.resolve(true);
        setPending(null);
      } else if (response.status === 401) {
        setError("Wrong password");
      } else {
        setError(`Unexpected ${response.status} from server`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusy(false);
    }
  }

  function handleCancel() {
    if (pending) pending.resolve(false);
    setPending(null);
    setError(null);
  }

  const value = useMemo<ControlAuthValue>(
    () => ({ loading, enabled, authenticated, ensureAuth }),
    [loading, enabled, authenticated, ensureAuth],
  );

  return (
    <ControlAuthContext.Provider value={value}>
      {children}
      <PasswordModal
        open={pending !== null}
        error={error}
        busy={busy}
        onSubmit={handleSubmit}
        onCancel={handleCancel}
      />
    </ControlAuthContext.Provider>
  );
}

export function useControlAuth(): ControlAuthValue {
  return useContext(ControlAuthContext);
}
