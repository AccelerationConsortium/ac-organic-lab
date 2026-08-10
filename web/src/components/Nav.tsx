"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { usePlatforms } from "@/lib/use-platforms";
import { useUserAuth } from "@/lib/user-auth";

const STATIC_BEFORE = [{ href: "/", label: "Overview" }];
const STATIC_AFTER = [
  { href: "/history", label: "History" },
  // Operator tools (API reference, labware builder, …) — pills inside,
  // one route per tool.
  { href: "/utils", label: "Utils" },
];

type Tab = { href: string; label: string };

export function Nav() {
  const pathname = usePathname();
  const { data: platforms } = usePlatforms();
  const { authenticated, identity } = useUserAuth();

  // One "Platforms" tab groups every `kind: platform` section — the page's
  // pill row switches between them (the old per-platform routes still exist
  // and are linked from there). The tab renders only when platforms exist.
  const platformTabs = (platforms?.sections ?? []).some((s) => s.kind === "platform")
    ? [{ href: "/platforms", label: "Platforms" }]
    : [];

  // Notebooks embeds Bitácora (the agentic ELN) in an iframe at /notebooks.
  // The tab renders only after sign-in so an anonymous visitor never sees the
  // ELN's own auth screen nested inside this dashboard (the middleware
  // redirects direct navigations too — this is just the visibility half).
  const notebooksTabs = authenticated
    ? [{ href: "/notebooks", label: "Notebooks" }]
    : [];

  // Visibility only — the /admin route is enforced by the middleware + sidecar.
  const adminTabs =
    identity?.role === "admin" ? [{ href: "/admin", label: "Admin" }] : [];

  const tabs: Tab[] = [
    ...STATIC_BEFORE,
    ...platformTabs,
    ...notebooksTabs,
    ...STATIC_AFTER,
    ...adminTabs,
  ];

  return (
    <nav className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
      {tabs.map((tab) => {
        const active =
          tab.href === "/"
            ? pathname === "/"
            : pathname.startsWith(tab.href);
        const cls = `-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
          active
            ? "border-sky-600 text-ink dark:border-sky-400 dark:text-slate-100"
            : "border-transparent text-ink-muted hover:text-ink dark:text-slate-400 dark:hover:text-slate-200"
        }`;
        return (
          <Link key={tab.href} href={tab.href} className={cls}>{tab.label}</Link>
        );
      })}
    </nav>
  );
}
