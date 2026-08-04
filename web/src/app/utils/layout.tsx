"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { pillClass } from "@/lib/pill";

/**
 * Utils section — operator tools that aren't tied to one piece of equipment.
 * Each utility is a route under /utils/<slug>; the pill row here switches
 * between them (same pill pattern as the Platforms tab), and None (the /utils
 * index) shows nothing. Register new utilities in UTILS below.
 */
const UTILS: { slug: string; label: string; description: string }[] = [
  {
    slug: "xarm_control",
    label: "xArm Control",
    description: "Operator panel for the xArm translocation arm.",
  },
  {
    slug: "bambu_printer",
    label: "Bambu Printer",
    description: "Live monitoring for the Bambu P1S and H2D printers.",
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
];

export default function UtilsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Utilities">
        {UTILS.map((u) => {
          const href = `/utils/${u.slug}`;
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
        <Link
          href="/utils"
          role="tab"
          aria-selected={pathname === "/utils"}
          title="Show nothing"
          className={pillClass(pathname === "/utils")}
        >
          None
        </Link>
      </div>
      {children}
    </div>
  );
}
