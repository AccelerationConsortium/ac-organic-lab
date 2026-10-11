"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useUserAuth } from "@/lib/user-auth";

export const previewNavigation = [
  { label: "Home", href: "/preview/dashboard" },
  { label: "Overview", href: "/preview/dashboard/overview" },
  { label: "Bookings", href: "/preview/dashboard/bookings", note: "Mock" },
  { label: "Platforms", href: "/preview/dashboard/platforms" },
  { label: "Inventory", href: "/preview/dashboard/inventory" },
  { label: "History", href: "/history", note: "Leaves preview" },
  { label: "Tools", href: "/utils", note: "Leaves preview" },
  { label: "Contact admin", href: "/preview/dashboard/contact-admin", note: "Mock" },
  { label: "Admin", href: "/admin", note: "Leaves preview" },
];

const focusStyle = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500";

/** One responsive navigation tree and one scroll owner; providers live at root. */
export function WorkspaceShell({ children, title }: { children: React.ReactNode; title: string }) {
  const pathname = usePathname();
  const { loading, authenticated, identity } = useUserAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const nav = useRef<HTMLElement>(null);
  const main = useRef<HTMLElement>(null);
  const previousPath = useRef(pathname);

  useEffect(() => {
    if (previousPath.current !== pathname) {
      setMenuOpen(false);
      main.current?.focus();
      main.current?.scrollTo?.(0, 0);
      previousPath.current = pathname;
    }
  }, [pathname]);

  useEffect(() => {
    if (menuOpen) nav.current?.querySelector<HTMLAnchorElement>("a")?.focus();
  }, [menuOpen]);

  if (loading || !authenticated || identity?.role !== "admin") {
    return <main className="p-6"><p>{loading ? "Checking your session…" : "Sign in as an admin using the existing sign-in bar, then reload this preview."}</p><Link href="/">Return to dashboard</Link></main>;
  }

  return (
    // Reserve the existing 26px state-reference tab so it cannot cover links.
    <div className="flex min-h-0 flex-1 flex-col pl-7 text-ink dark:text-slate-100 print:block print:pl-0" onKeyDown={(event) => {
      if (event.key === "Escape" && menuOpen) {
        event.preventDefault();
        setMenuOpen(false);
        toggle.current?.focus();
      }
    }}>
      <a href="#workspace-content" className="sr-only focus:not-sr-only focus:p-3">Skip to content</a>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800 print:hidden">
        <div className="flex items-center gap-3">
          <button ref={toggle} type="button" aria-expanded={menuOpen} aria-controls="workspace-navigation" onClick={() => setMenuOpen(!menuOpen)} className={`rounded-md border border-slate-300 px-3 py-2 md:hidden dark:border-slate-700 ${focusStyle}`}>Menu</button>
          <div><p className="font-semibold">Organic Self-driving Lab</p><p className="text-xs text-ink-subtle dark:text-slate-400">Sidebar preview · Admin review</p></div>
        </div>
        <button type="button" onClick={() => window.dispatchEvent(new Event("dashboard:open-assistant"))} className={`rounded-md border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 ${focusStyle}`}>Open assistant</button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row print:block">
        <aside className={`${menuOpen ? "flex" : "hidden"} max-h-[45dvh] shrink-0 flex-col overflow-y-auto border-b border-slate-200 bg-surface-raised p-3 md:flex md:max-h-none md:w-52 md:border-b-0 md:border-r dark:border-slate-800 dark:bg-slate-900 print:hidden`}>
          <nav ref={nav} id="workspace-navigation" aria-label="Workspace" className="flex flex-col gap-1">
            {previewNavigation.map(({ label, href, note }) => (
              <Link key={href} href={href} prefetch={false} aria-current={pathname.replace(/\/$/, "") === href ? "page" : undefined} className={`rounded-lg px-3 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800 ${focusStyle} ${pathname.replace(/\/$/, "") === href ? "bg-sky-100 font-semibold text-sky-900 dark:bg-sky-950 dark:text-sky-200" : ""}`}>
                {label}{note && <span className="block text-xs font-normal text-ink-subtle dark:text-slate-400">{note}</span>}
              </Link>
            ))}
            <a href="/bitacora/" target="_blank" rel="noopener noreferrer" className={`rounded-lg px-3 py-2 text-sm ${focusStyle}`}>ELN <span className="block text-xs text-ink-subtle dark:text-slate-400">Opens in a new tab</span></a>
            <Link href="/" prefetch={false} className={`mt-3 border-t border-slate-200 px-3 py-3 text-sm dark:border-slate-700 ${focusStyle}`}>Exit preview</Link>
          </nav>
        </aside>
        <main ref={main} id="workspace-content" tabIndex={-1} className="workspace-content min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-none focus:outline-none print:overflow-visible">
          <div className="mx-auto w-full max-w-7xl px-4 pb-24 sm:px-6 lg:px-8 print:pb-0">
            <h1 className="py-4 text-xl font-semibold">{title}</h1>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
