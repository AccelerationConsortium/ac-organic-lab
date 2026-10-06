"use client";

import { usePathname } from "next/navigation";

/** Routes that frame a device panel full-height without dashboard chrome. The
 *  UR5e panel left this list 2026-10-06: it is now the device's own page at
 *  the edge path /ur5e/web/ (lib/device-panels.ts), like the xArm's. */
export function isFramedWorkspace(pathname: string | null) {
  return pathname === "/equipment/lle_hplc/control" || pathname === "/equipment/lle_hplc/control/";
}

/** Presentation only: the root auth banner/providers and API gate stay intact. */
export function DashboardChrome({ children }: { children: React.ReactNode }) {
  return isFramedWorkspace(usePathname()) ? null : <>{children}</>;
}

export function DashboardContent({ children }: { children: React.ReactNode }) {
  const standalone = isFramedWorkspace(usePathname());
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
