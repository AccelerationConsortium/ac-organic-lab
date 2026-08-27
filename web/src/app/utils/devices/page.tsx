import { redirect } from "next/navigation";

// Request-time, not prerendered — same reasoning as ../page.tsx: a static
// prerender bakes the redirect into the RSC payload with no Location header,
// so direct URL loads (old bookmarks) see a dead 307.
export const dynamic = "force-dynamic";

/** The combined hosts+printers page was split into /utils/computers and
 *  /utils/printers; this stub keeps old links landing on the hosts half. */
export default function DevicesRedirect() {
  redirect("/utils/computers");
}
