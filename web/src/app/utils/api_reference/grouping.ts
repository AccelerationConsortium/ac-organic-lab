/** Grouping rules for the Dashboard API reference.
 *
 * A Next.js page module may export only its component, so these live here —
 * which is also what makes them testable. The page renders what these return:
 * tags in a deliberate order, split into sub-modules when a tag is long enough
 * that one flat list stops being readable.
 */

import type { Endpoint, OpenApiDoc, SubModule } from "./types";

// Tags in the order an operator meets them, not alphabetically: what the lab
// *is* (meta, equipment), what you can do to it (control), what it recorded
// (history), then the specialised surfaces. Anything unlisted sorts last, so a
// newly-tagged router shows up rather than disappearing.
export const TAG_ORDER = [
  "meta",
  "equipment",
  "equipment-control",
  "history",
  "workflow",
  "labware",
  "deck",
  "custody",
  "assistant",
  "agent-bridge",
  "hosts",
  "ssh-console",
];

/** Human titles for the OpenAPI tags. Without these the page shows a router's
 *  internal tag string, which reads like an implementation detail. */
export const TAG_TITLE: Record<string, string> = {
  meta: "Server & reference",
  equipment: "Equipment state",
  "equipment-control": "Equipment control",
  history: "History & ingest",
  workflow: "Workflow runs",
  labware: "Labware",
  deck: "Deck layouts",
  custody: "Plate custody",
  assistant: "Assistant",
  "agent-bridge": "Agent bridge",
  hosts: "Host inventory",
  "ssh-console": "SSH console",
};

export const TAG_BLURB: Record<string, string> = {
  meta: "Server identity, health, and the two reference documents (this one and the device catalog).",
  equipment: "Aggregated device state — the poll loop's view of the lab.",
  "equipment-control":
    "Operator-initiated writes. Each call runs claim → action → release against the device and writes one audit row.",
  history: "Read the history DB; the /api/ingest/* routes are how device services write to it.",
  workflow: "Authorized plan runs: start, watch the step stream, abort.",
  labware: "Custom labware definitions plus the read-only standard Opentrons set.",
  deck: "Per-equipment deck layout the OT-2 surfaces share.",
  custody: "Where a plate is and who moved it last.",
  assistant: "The chat bubble's backend. Read-only in Ask mode; propose-only in Control mode.",
  "agent-bridge": "The boxed lab agent's way in.",
  hosts: "Which machine runs what, derived from the registry and the console whitelist.",
  "ssh-console": "Ticket + WebSocket behind the admin-only browser terminal.",
};

/** Path segments, minus the "api" prefix: "/api/assistant/sessions/{id}" ->
 *  ["assistant", "sessions", "{id}"]. */
function segments(path: string): string[] {
  return path.split("/").filter(Boolean).filter((s) => s !== "api");
}

/** How many leading segments every path in the group shares. Used so the
 *  sub-module name is the first segment that actually differs, not the tag's
 *  own prefix repeated on every row. */
export function sharedDepth(paths: string[]): number {
  if (paths.length === 0) return 0;
  const first = segments(paths[0]);
  let depth = 0;
  while (depth < first.length) {
    const seg = first[depth];
    if (!paths.every((p) => segments(p)[depth] === seg)) break;
    depth += 1;
  }
  return depth;
}

/** The sub-module an endpoint belongs to: the first differing segment that is
 *  not a path parameter. `/api/equipment/{id}/control/{action}` and
 *  `/api/equipment/{id}/media` share "equipment" and a parameter, so they
 *  land under "control" and "media" rather than both under the id. */
export function subModuleOf(path: string, depth: number): string {
  const segs = segments(path);
  for (let i = depth; i < segs.length; i += 1) {
    if (!segs[i].startsWith("{")) return segs[i];
  }
  return segs[Math.max(0, depth - 1)] ?? "/";
}

/** Split a tag's endpoints into sub-modules, or return null when splitting
 *  would not help.
 *
 *  Three cases are deliberately left flat, because a fold that hides nothing is
 *  worse than no fold: a short list, a list that all lands in one bucket, and a
 *  list of unrelated singletons (the `meta` tag is six one-off routes — folding
 *  each into its own block would turn one readable list into six clicks). The
 *  rule that survives all three: at least two buckets must hold more than one
 *  endpoint. */
export function splitSubModules(endpoints: Endpoint[]): SubModule[] | null {
  if (endpoints.length < 5) return null;
  const depth = sharedDepth(endpoints.map((e) => e.path));
  const buckets = new Map<string, Endpoint[]>();
  for (const endpoint of endpoints) {
    const key = subModuleOf(endpoint.path, depth);
    buckets.set(key, [...(buckets.get(key) ?? []), endpoint]);
  }
  const substantial = [...buckets.values()].filter((list) => list.length > 1).length;
  if (buckets.size < 2 || substantial < 2) return null;
  return [...buckets.entries()]
    .map(([name, list]) => ({ name, endpoints: list }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Free-text match over what a reader would type: method, path, summary. */
export function endpointMatches(endpoint: Endpoint, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    endpoint.path.toLowerCase().includes(q) ||
    endpoint.method.toLowerCase().includes(q) ||
    (endpoint.op.summary ?? "").toLowerCase().includes(q)
  );
}

export function groupByTag(doc: OpenApiDoc): [string, Endpoint[]][] {
  const groups = new Map<string, Endpoint[]>();
  for (const [path, methods] of Object.entries(doc.paths ?? {})) {
    for (const [method, op] of Object.entries(methods)) {
      const tag = op.tags?.[0] ?? "other";
      const list = groups.get(tag) ?? [];
      list.push({ method: method.toUpperCase(), path, op });
      groups.set(tag, list);
    }
  }
  const rank = (tag: string) => {
    const i = TAG_ORDER.indexOf(tag);
    return i === -1 ? TAG_ORDER.length : i;
  };
  return [...groups.entries()]
    .map(([tag, list]) => {
      list.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
      return [tag, list] as [string, Endpoint[]];
    })
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

