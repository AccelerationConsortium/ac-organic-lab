"use client";

import type { MouseEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { usePlatforms } from "@/lib/use-platforms";
import { useUserAuth } from "@/lib/user-auth";
import { ChromeToggle } from "./ChromeToggle";

const STATIC_BEFORE = [{ href: "/", label: "Overview" }];
const STATIC_AFTER = [
  { href: "/history", label: "History" },
  // Operator tools (API reference, labware builder, …) — pills inside,
  // one route per tool.
  { href: "/utils", label: "Utils" },
];

// `popup` opens the link in a separate popup window (falling back to a new
// tab when the browser blocks it); `external` opens a plain new tab.
type Tab = { href: string; label: string; external?: boolean; popup?: boolean };

// Bitácora is a separate app on the same edge origin with its own sign-in
// gate, so the tab is visible to everyone; access is enforced there.
const notebooksTab: Tab[] = [{ href: "/bitacora/", label: "Notebooks", popup: true }];

function openPopup(event: MouseEvent<HTMLAnchorElement>, href: string) {
  // Modified clicks keep the browser's own new-tab / new-window behaviour.
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const win = window.open(href, "bitacora", "popup,width=1280,height=900");
  if (win) {
    event.preventDefault();
    win.focus();
  }
}

export function Nav() {
  const pathname = usePathname();
  const { data: platforms } = usePlatforms();
  const { identity } = useUserAuth();

  // One "Platforms" tab groups every `kind: platform` section — the page's
  // pill row switches between them (the old per-platform routes still exist
  // and are linked from there). The tab renders only when platforms exist.
  const platformTabs = (platforms?.sections ?? []).some((s) => s.kind === "platform")
    ? [{ href: "/platforms", label: "Platforms" }]
    : [];

  // Inventory is a public, chrome-less read-only embed — it stays inside the
  // dashboard at /inventory and is visible to everyone.
  const inventoryTab = [{ href: "/inventory", label: "Inventory" }];

  // Visibility only — the /admin route is enforced by the middleware + sidecar.
  const adminTabs =
    identity?.role === "admin" ? [{ href: "/admin", label: "Admin" }] : [];

  const tabs: Tab[] = [
    ...STATIC_BEFORE,
    ...platformTabs,
    ...notebooksTab,
    ...inventoryTab,
    ...STATIC_AFTER,
    ...adminTabs,
  ];

  return (
    <nav className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
      {tabs.map((tab) => {
        const active = tab.external || tab.popup
          ? false
          : tab.href === "/"
            ? pathname === "/"
            : pathname.startsWith(tab.href);
        const cls = `-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
          active
            ? "border-sky-600 text-ink dark:border-sky-400 dark:text-slate-100"
            : "border-transparent text-ink-muted hover:text-ink dark:text-slate-300 dark:hover:text-slate-200"
        }`;
        // External and popup tabs leave the dashboard; a popup tab is an
        // ordinary new-tab link until the click handler opens the window.
        return tab.external || tab.popup ? (
          <a
            key={tab.href}
            href={tab.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={tab.popup ? (e) => openPopup(e, tab.href) : undefined}
            className={cls}
          >
            {tab.label}
          </a>
        ) : (
          <Link key={tab.href} href={tab.href} className={cls}>{tab.label}</Link>
        );
      })}
      {/* Right end of the row: hide / show the title + logo above. Lives in
          the tab row (not the heading) so it is still reachable once the
          heading is collapsed. */}
      <ChromeToggle />
    </nav>
  );
}
