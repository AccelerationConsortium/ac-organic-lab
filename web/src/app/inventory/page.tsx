"use client";

import { useUserAuth } from "@/lib/user-auth";

/**
 * Inventory — the lab's chemical stock, framed from Bitácora.
 *
 * A page of its own rather than a tab inside a notebook, because the inventory
 * is lab-wide: it is not scoped to a project, a room or a protocol, and the
 * question it answers ("do we have any tempo, and where") is asked far more
 * often than any one notebook is open (INVENTORY_DESIGN, "Placement and
 * permissions").
 *
 * Framed rather than reimplemented: one implementation, in the app that owns
 * the data. As with /notebooks, the iframe lives in the root layout's
 * <KeepAliveEmbeds /> (hidden, not unmounted, between visits) so the search
 * and scroll state survive dashboard tab switches; this page contributes only
 * the auth-guard states. Uploading is admin-only and enforced by Bitácora's
 * API, not here; the guard gates on *sign-in* only, so the whole lab can read
 * the shelf.
 */
export default function InventoryPage() {
  const { loading, authenticated } = useUserAuth();

  if (loading) {
    return <div className="mt-6 h-24 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;
  }
  if (!authenticated) {
    return (
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
        <p className="text-sm font-medium text-ink-muted dark:text-slate-400">
          Inventory — sign in to view the lab&apos;s chemical stock.
        </p>
      </div>
    );
  }

  // Signed in: the keep-alive frame in the root layout shows here.
  return null;
}
