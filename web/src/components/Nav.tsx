"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { usePlatforms } from "@/lib/use-platforms";
import { useUserAuth } from "@/lib/user-auth";

const STATIC_BEFORE = [{ href: "/", label: "Overview" }];
const STATIC_AFTER = [
  { href: "/history", label: "History" },
  { href: "/api-reference", label: "API Reference" },
];

export function Nav() {
  const pathname = usePathname();
  const { data: platforms } = usePlatforms();
  const { identity } = useUserAuth();

  const platformTabs = (platforms?.sections ?? [])
    .filter((s) => s.href != null)
    .map((s) => ({ href: s.href as string, label: s.title }));

  // Visibility only — the /admin route is enforced by the middleware + sidecar.
  const adminTabs =
    identity?.role === "admin" ? [{ href: "/admin", label: "Admin" }] : [];

  const tabs = [...STATIC_BEFORE, ...platformTabs, ...STATIC_AFTER, ...adminTabs];

  return (
    <nav className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
      {tabs.map((tab) => {
        const active =
          tab.href === "/"
            ? pathname === "/"
            : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              active
                ? "border-sky-600 text-ink dark:border-sky-400 dark:text-slate-100"
                : "border-transparent text-ink-muted hover:text-ink dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
