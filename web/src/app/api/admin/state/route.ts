import { adminProxy } from "@/lib/admin-proxy";

// Literal route (not a catch-all) so it wins over the /api/:path* rewrite —
// see lib/admin-proxy.ts.
export const GET = adminProxy("state");
