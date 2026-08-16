import { redirect } from "next/navigation";

/** The printers page was generalised into /utils/devices (hosts + printers);
 *  this stub keeps old links working. */
export default function BambuPrinterRedirect() {
  redirect("/utils/devices");
}
