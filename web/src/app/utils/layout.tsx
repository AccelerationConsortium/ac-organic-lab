"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { pillClass, stickyPillRow } from "@/lib/pill";

/**
 * Utils section — operator tools that aren't tied to one piece of equipment.
 * Each utility is a route under /utils/<slug>; the pill row here switches
 * between them (same pill pattern as the Platforms tab). The /utils index
 * redirects to Devices, the default utility. Register new utilities in
 * UTILS below. (Inventory graduated back to a top-level /inventory page with
 * its own Nav tab, next to Notebooks.)
 */
const UTILS: { slug: string; label: string; description: string; href?: string }[] = [
  {
    slug: "devices",
    label: "Devices",
    description:
      "Lab host machines — servers and device PCs with host-ops coverage — plus the Bambu printers.",
  },
  {
    slug: "api_reference",
    label: "API Reference",
    description: "REST API endpoints exposed by the dashboard server.",
  },
  {
    slug: "labware_builder",
    label: "Labware builder",
    description: "Build + validate Opentrons schema-2 labware definition JSON.",
  },
  {
    slug: "plates",
    label: "Plates",
    description:
      "Where every plate is — the record layer's custody ledger — and record a bench-top move.",
  },
];

export default function UtilsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex flex-col gap-4">
      <div className={`${stickyPillRow}`} role="tablist" aria-label="Utilities">
        {UTILS.map((u) => {
          const href = u.href ?? `/utils/${u.slug}`;
          const active = pathname.startsWith(href);
          return (
            <Link
              key={u.slug}
              href={href}
              role="tab"
              aria-selected={active}
              title={u.description}
              className={pillClass(active)}
            >
              {u.label}
            </Link>
          );
        })}
      </div>
      {children}
    </div>
  );
}
