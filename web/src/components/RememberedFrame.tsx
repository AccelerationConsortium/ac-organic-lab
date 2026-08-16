"use client";

import { useEffect, useRef, useState } from "react";

/**
 * An iframe that resumes where the user left off.
 *
 * The embedded app (Bitácora, served same-origin under /bitacora) does its
 * own client-side routing, so a plain <iframe src=...> restarts it at the
 * default page every time the dashboard tab remounts. Because the frame is
 * same-origin, the parent may read its location: this component saves the
 * frame's last path on unmount (dashboard navigation) and on pagehide (full
 * reload / tab close), and uses it as the initial src on the next mount.
 *
 * `scope` is a required path prefix guard, doing two jobs: a saved path
 * outside it is ignored (sessionStorage is user-writable — never a source of
 * arbitrary frame targets), and each embed stays in its own slice of the
 * embedded app (the Inventory tab must not resurrect a notebooks path).
 */
export function RememberedFrame({
  storageKey,
  defaultSrc,
  scope,
  title,
  className,
}: {
  /** sessionStorage key; one per embed surface. */
  storageKey: string;
  /** Where the frame starts with nothing (valid) saved. */
  defaultSrc: string;
  /** Only saved paths starting with this prefix are restored or recorded. */
  scope: string;
  title: string;
  className?: string;
}) {
  // Read once per mount: restoring mid-session would yank the user's page.
  const [src] = useState(() => {
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved && saved.startsWith(scope)) return saved;
    } catch {
      // Storage unavailable (privacy mode) — just start at the default.
    }
    return defaultSrc;
  });
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const save = () => {
      try {
        // Throws if the frame ever ends up cross-origin; then nothing saves.
        const loc = frameRef.current?.contentWindow?.location;
        if (!loc) return;
        const path = loc.pathname + loc.search + loc.hash;
        if (path.startsWith(scope)) sessionStorage.setItem(storageKey, path);
      } catch {
        /* cross-origin or storage failure — keep the previous value */
      }
    };
    window.addEventListener("pagehide", save);
    return () => {
      window.removeEventListener("pagehide", save);
      save(); // unmount = the user navigated to another dashboard tab
    };
  }, [storageKey, scope]);

  return (
    <iframe ref={frameRef} src={src} title={title} className={className} />
  );
}
