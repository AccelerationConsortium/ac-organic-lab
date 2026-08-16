import { redirect } from "next/navigation";

// Request-time, not prerendered: a static prerender bakes the redirect into
// the RSC payload with no Location header, so only the client router can
// follow it — direct URL loads flash the shell first and curl sees a dead 307.
export const dynamic = "force-dynamic";

/** /utils index → Devices, the section's default utility. */
export default function UtilsIndexPage() {
  redirect("/utils/devices");
}
