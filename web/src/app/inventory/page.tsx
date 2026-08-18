/**
 * Inventory — the lab's chemical stock, embedded from Bitácora.
 *
 * A read-only, chrome-less embed (/bitacora/inventory/embed). The embed has no
 * Bitácora banner/heading, and its reads are public — so this page requires no
 * sign-in; anyone can browse the shelf (uploading stays admin-only, enforced by
 * Bitácora's API). Notebooks, by contrast, opens the full Bitácora app in its
 * own browser tab.
 */
export default function InventoryPage() {
  return (
    <div className="h-[calc(100vh-190px)] min-h-[640px] w-full overflow-hidden rounded-xl border border-slate-200 bg-transparent dark:border-slate-800">
      <iframe
        src="/bitacora/inventory/embed"
        title="Chemical inventory"
        className="h-full w-full border-0"
      />
    </div>
  );
}
