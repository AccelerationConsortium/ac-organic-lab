"use client";

import { useUserAuth } from "@/lib/user-auth";
import { usePathname } from "next/navigation";

/** Routes that frame a device panel full-height without dashboard chrome. The
 *  UR5e panel left this list 2026-10-06: it is now the device's own page at
 *  the edge path /ur5e/web/ (lib/device-panels.ts), like the xArm's. */
export function isFramedWorkspace(pathname: string | null) {
  return pathname === "/equipment/lle_hplc/control" || pathname === "/equipment/lle_hplc/control/";
}

export function isDashboardPreview(pathname: string | null) {
  return pathname === "/preview/dashboard" || !!pathname?.startsWith("/preview/dashboard/");
}

/** Presentation only: the root auth banner/providers and API gate stay intact. */
export function DashboardChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return isFramedWorkspace(pathname) || isDashboardPreview(pathname) ? null : <>{children}</>;
}

/** Keep the existing assistant mounted once across ordinary/preview navigation. */
export function DashboardTools({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { loading, authenticated, identity } = useUserAuth();
  if (isFramedWorkspace(pathname)) return null;
  if (isDashboardPreview(pathname) && (loading || !authenticated || identity?.role !== "admin")) return null;
  return <>{children}</>;
}

export function DashboardContent({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (isDashboardPreview(pathname)) return <>{children}</>;
  const standalone = isFramedWorkspace(pathname);
  return (
    <main className={standalone
      ? "flex min-h-0 flex-1 flex-col overflow-hidden"
      : "min-h-0 flex-1 overflow-y-auto overscroll-none print:overflow-visible"}>
      <div className={standalone
        ? "flex min-h-0 w-full flex-1 flex-col"
        : "mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 pb-6 sm:px-6 lg:px-8"}>
        {children}
      </div>
    </main>
  );
}
