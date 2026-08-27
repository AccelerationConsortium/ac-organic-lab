import { overviewProxy } from "@/lib/overview-proxy";

// Roster headline counts for the Overview's "Accounts & Activities" tile.
// Literal route wins over the /api/:path* rewrite — see lib/overview-proxy.ts.
export const GET = overviewProxy("state");
