"use client";

import { useUserAuth } from "@/lib/user-auth";

/**
 * Inventory — the lab chemical stock, framed from Bitacora.
 *
 * Lives under /utils so the pill row (Inventory, Bambu Printer, API Reference,
 * Labware builder, None) stays visible while the iframe renders below it.
 * Originally a top-level /inventory route, which navigated away from the
 * Utils layout and lost the pills.
 */
export default function UtilsInventoryPage() {
  const { loading, authenticated } = useUserAuth();

  if (loading) {
    return <div className="mt-6 h-24 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;
  }
  if (!authenticated) {
    return (
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
        <p className="text-sm font-medium text-ink-muted dark:text-slate-400">
          {"Inventory — sign in to view the lab's chemical stock."}
        </p>
      </div>
    );
  }

  return (
    <div>
      <iframe
        src="/bitacora/inventory/embed"
        title="Chemical inventory"
        className="h-[calc(100vh-190px)] min-h-[640px] w-full rounded-xl border border-slate-200 bg-transparent dark:border-slate-800"
      />
    </div>
  );
}
