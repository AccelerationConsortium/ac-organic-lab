"use client";

import { usePathname } from "next/navigation";

export function isRobotMotionWorkspace(pathname: string | null) {
  return pathname === "/utils/robot_motion" || pathname === "/utils/robot_motion/";
}

export function isFramedWorkspace(pathname: string | null) {
  return isRobotMotionWorkspace(pathname) ||
    pathname === "/equipment/lle_hplc/control" || pathname === "/equipment/lle_hplc/control/";
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
