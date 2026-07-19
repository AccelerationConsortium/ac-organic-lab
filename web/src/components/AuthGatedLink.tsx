"use client";

import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import Link from "next/link";
import { useUserAuth } from "@/lib/user-auth";

/** How long the "Not authorized" bubble stays visible. */
const BUBBLE_MS = 1500;

/**
 * A link to an equipment interface (edge-gated device panel, full-page
 * control interface) that only navigates when the signed-in user holds a
 * role on that equipment (`canControl`, backed by the auth sidecar's
 * /authz/mine map). An unauthorized click is swallowed and a small
 * "Not authorized" bubble pops above the link, auto-dismissing after
 * ~1.5 s; a signed-out click additionally nudges the login bar into view.
 *
 * UX only — the edge (forward_auth) and the control passthrough enforce
 * the same answer server-side.
 */
export function AuthGatedLink({
  href,
  equipmentId,
  external = false,
  className,
  title,
  children,
}: {
  href: string;
  /** Equipment whose device role gates this link; omit to gate on sign-in only. */
  equipmentId?: string;
  /** True for device panels / service UIs that open in a new tab. */
  external?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  const { authenticated, canControl, requestLogin } = useUserAuth();
  const authorized = equipmentId ? canControl(equipmentId) : authenticated;
  const [bubble, setBubble] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function onClick(e: MouseEvent) {
    if (authorized) return;
    e.preventDefault();
    if (!authenticated) requestLogin();
    setBubble(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setBubble(false), BUBBLE_MS);
  }

  const linkProps = {
    href,
    onClick,
    title,
    className,
    "aria-disabled": authorized ? undefined : true,
  } as const;

  return (
    <span className="relative inline-flex">
      {external ? (
        <a {...linkProps} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      ) : (
        <Link {...linkProps}>{children}</Link>
      )}
      {bubble && (
        <span
          role="status"
          className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1 -translate-x-1/2 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[10px] font-medium text-white shadow-lg dark:bg-slate-700"
        >
          Not authorized
        </span>
      )}
    </span>
  );
}
