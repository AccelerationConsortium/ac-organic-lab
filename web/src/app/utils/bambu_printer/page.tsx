import { redirect } from "next/navigation";

// Request-time, not prerendered — same reasoning as ../page.tsx: a static
// prerender bakes the redirect into the RSC payload with no Location header,
// so direct URL loads (old bookmarks) see a dead 307.
export const dynamic = "force-dynamic";

/** The printers page was generalised into /utils/devices (hosts + printers);
 *  this stub keeps old links working. */
export default function BambuPrinterRedirect() {
  redirect("/utils/devices");
}
