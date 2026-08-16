"use client";

import { RememberedFrame } from "@/components/RememberedFrame";
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
 * the data. Same reasoning — and the same russian-doll guard — as /notebooks.
 * Uploading is admin-only and enforced by Bitácora's API, not here; this page
 * gates on *sign-in* only, so the whole lab can read the shelf.
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

  return (
    <div>
      <RememberedFrame
        storageKey="eln:inventory"
        defaultSrc="/bitacora/inventory/embed"
        scope="/bitacora/inventory"
        title="Chemical inventory"
        className="h-[calc(100vh-190px)] min-h-[640px] w-full rounded-xl border border-slate-200 bg-transparent dark:border-slate-800"
      />
    </div>
  );
}
