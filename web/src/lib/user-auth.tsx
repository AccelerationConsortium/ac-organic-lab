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

export interface Identity {
  email: string;
  role: string;
}

export interface UserSummary {
  email: string;
  role: string;
}

export interface UserAuthValue {
  /** True until the initial GET /api/auth/me resolves. */
  loading: boolean;
  /** A valid session cookie is held (best-effort; the server is the authority). */
  authenticated: boolean;
  /** The signed-in identity, or null when logged out. */
  identity: Identity | null;
  /** Active human accounts offered in the login dropdown. */
  users: UserSummary[];
  usersLoading: boolean;

  /** Ask the sidecar to email a one-time code to `email`. */
  requestCode: (email: string) => Promise<{ ok: boolean; error?: string }>;
  /** Submit a code; on success the session cookie is set and state updates. */
  verifyCode: (
    email: string,
    code: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** Revoke the session and clear local state. */
  logout: () => Promise<void>;

  /**
   * Per-equipment authorization for the UI: true when the signed-in user
   * holds a role on this equipment (from the sidecar's /authz/mine map).
   * UX only — the control passthrough enforces the same answer server-side.
   */
  canControl: (equipmentId: string) => boolean;

  /**
   * Gate used by control tiles (`useControlLock`). Resolves true when the user
   * is signed in; otherwise nudges the login bar into view and resolves false.
   * This is what makes the dashboard view-only until login.
   */
  ensureAuth: () => Promise<boolean>;
  /** Flash / scroll the login bar into view (called by ensureAuth). */
  requestLogin: () => void;
  /** LoginBar registers its scroll-into-view+flash handler here. */
  registerLoginHint: (fn: () => void) => void;
}

const UserAuthContext = createContext<UserAuthValue>({
  loading: false,
  authenticated: false,
  identity: null,
  users: [],
  usersLoading: false,
  requestCode: async () => ({ ok: false }),
  verifyCode: async () => ({ ok: false }),
  logout: async () => {},
  canControl: () => false,
  ensureAuth: async () => false,
  requestLogin: () => {},
  registerLoginHint: () => {},
});

export function UserAuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  // equipment_id -> device role ("user"/"service"/"automation") or null for
  // no access; null map = not loaded (fall back to flat-role behavior).
  const [equipmentRoles, setEquipmentRoles] = useState<Record<
    string,
    string | null
  > | null>(null);

  const loginHintRef = useRef<(() => void) | null>(null);

  // Resolve current identity + the allow-list once on mount.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { authenticated?: boolean; identity?: Identity | null }) => {
        if (cancelled) return;
        setAuthenticated(Boolean(d.authenticated));
        setIdentity(d.identity ?? null);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    fetch("/api/auth/users", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { users: [] }))
      .then((d: { users?: UserSummary[] }) => {
        if (!cancelled) setUsers(d.users ?? []);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setUsersLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch the per-equipment role map whenever a session becomes active.
  useEffect(() => {
    if (!authenticated) {
      setEquipmentRoles(null);
      return;
    }
    let cancelled = false;
    fetch("/api/auth/mine", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { equipment?: Record<string, string | null> } | null) => {
        if (!cancelled) setEquipmentRoles(d?.equipment ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

  const canControl = useCallback(
    (equipmentId: string): boolean => {
      if (!authenticated) return false;
      if (equipmentRoles && equipmentId in equipmentRoles) {
        return equipmentRoles[equipmentId] != null;
      }
      // Equipment not in the map (not in platforms.yaml) or the map hasn't
      // loaded: a flat global role (operator/admin) reaches everything; a
      // role:none account reaches only its granted equipment, which the map
      // always lists. The server-side gate is authoritative either way.
      return identity?.role !== "none";
    },
    [authenticated, equipmentRoles, identity],
  );

  const requestCode = useCallback(async (email: string) => {
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (r.ok) return { ok: true };
      const d = await r.json().catch(() => null);
      return { ok: false, error: d?.detail ?? `Error ${r.status}` };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : "Network error" };
    }
  }, []);

  const verifyCode = useCallback(async (email: string, code: string) => {
    try {
      const r = await fetch("/api/auth/verify-code", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, code }),
      });
      if (r.ok) {
        const d = await r.json().catch(() => null);
        setAuthenticated(true);
        setIdentity({ email: d?.email ?? email, role: d?.role ?? "user" });
        return { ok: true };
      }
      const d = await r.json().catch(() => null);
      return { ok: false, error: d?.detail ?? "Invalid or expired code." };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : "Network error" };
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {
      // best-effort; clear local state regardless
    }
    setAuthenticated(false);
    setIdentity(null);
  }, []);

  const requestLogin = useCallback(() => {
    loginHintRef.current?.();
  }, []);

  const registerLoginHint = useCallback((fn: () => void) => {
    loginHintRef.current = fn;
  }, []);

  // Stable ensureAuth that reads live auth state via a ref.
  const authedRef = useRef(authenticated);
  authedRef.current = authenticated;
  const ensureAuth = useCallback(async (): Promise<boolean> => {
    if (authedRef.current) return true;
    loginHintRef.current?.();
    return false;
  }, []);

  const value = useMemo<UserAuthValue>(
    () => ({
      loading,
      authenticated,
      identity,
      users,
      usersLoading,
      requestCode,
      verifyCode,
      logout,
      canControl,
      ensureAuth,
      requestLogin,
      registerLoginHint,
    }),
    [
      loading,
      authenticated,
      identity,
      users,
      usersLoading,
      requestCode,
      verifyCode,
      logout,
      canControl,
      ensureAuth,
      requestLogin,
      registerLoginHint,
    ],
  );

  return (
    <UserAuthContext.Provider value={value}>
      {children}
    </UserAuthContext.Provider>
  );
}

export function useUserAuth(): UserAuthValue {
  return useContext(UserAuthContext);
}
