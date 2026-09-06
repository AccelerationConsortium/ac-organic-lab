import { describe, expect, it } from "vitest";

import {
  TAG_TITLE,
  endpointMatches,
  groupByTag,
  sharedDepth,
  splitSubModules,
  subModuleOf,
} from "./grouping";
import type { Endpoint, OpenApiDoc } from "./types";

function endpoint(method: string, path: string, summary = ""): Endpoint {
  return { method, path, op: { summary } };
}

describe("sub-module grouping", () => {
  it("names a sub-module after the first segment that differs", () => {
    const paths = [
      "/api/assistant/chat",
      "/api/assistant/sessions",
      "/api/assistant/sessions/{session_id}",
    ];
    const depth = sharedDepth(paths);
    expect(depth).toBe(1); // every path starts with "assistant"
    expect(paths.map((p) => subModuleOf(p, depth))).toEqual([
      "chat",
      "sessions",
      "sessions",
    ]);
  });

  it("skips a path parameter, so sibling routes under one id split properly", () => {
    // /api/equipment/{id}/control and /api/equipment/{id}/media share both the
    // prefix and the id; grouping on the id would put every route in one pile.
    const paths = [
      "/api/equipment/{equipment_id}/control/{action}",
      "/api/equipment/{equipment_id}/media",
      "/api/equipment/{equipment_id}/plate/{sub}",
    ];
    const depth = sharedDepth(paths);
    expect(paths.map((p) => subModuleOf(p, depth))).toEqual(["control", "media", "plate"]);
  });

  it("splits a long tag into sub-modules, sorted by name", () => {
    const modules = splitSubModules([
      endpoint("POST", "/api/assistant/chat"),
      endpoint("GET", "/api/assistant/health"),
      endpoint("GET", "/api/assistant/sessions"),
      endpoint("POST", "/api/assistant/sessions"),
      endpoint("GET", "/api/assistant/sessions/{session_id}"),
      endpoint("POST", "/api/assistant/voice/speak"),
      endpoint("POST", "/api/assistant/voice/transcribe"),
    ]);
    expect(modules?.map((m) => [m.name, m.endpoints.length])).toEqual([
      ["chat", 1],
      ["health", 1],
      ["sessions", 3],
      ["voice", 2],
    ]);
  });

  it("leaves a tag of unrelated singletons flat", () => {
    // The `meta` tag: six one-off routes with nothing in common. Folding each
    // into its own block would trade one readable list for six clicks.
    expect(
      splitSubModules([
        endpoint("GET", "/api/catalog"),
        endpoint("GET", "/api/health"),
        endpoint("GET", "/api/locations"),
        endpoint("GET", "/api/openapi.json"),
        endpoint("GET", "/api/platforms"),
        endpoint("GET", "/status"),
      ]),
    ).toBeNull();
  });

  it("leaves a short tag flat", () => {
    expect(
      splitSubModules([endpoint("GET", "/api/hosts"), endpoint("GET", "/api/health")]),
    ).toBeNull();
  });

  it("leaves a tag flat when only one bucket has more than one endpoint", () => {
    // A "/schema" fold holding a single row, next to everything else, is a fold
    // that hides nothing.
    expect(
      splitSubModules([
        endpoint("GET", "/api/labware/definitions"),
        endpoint("POST", "/api/labware/definitions"),
        endpoint("GET", "/api/labware/definitions/{name}"),
        endpoint("DELETE", "/api/labware/definitions/{name}"),
        endpoint("GET", "/api/labware/definitions/{name}/schema"),
      ]),
    ).toBeNull();
  });

  it("keeps the tag's own root path as a named bucket", () => {
    // /api/labware and /api/labware/{load_name} have no distinct segment; they
    // belong under "/labware", which is exactly the path they are.
    const modules = splitSubModules([
      endpoint("GET", "/api/labware"),
      endpoint("POST", "/api/labware"),
      endpoint("GET", "/api/labware/{load_name}"),
      endpoint("DELETE", "/api/labware/{load_name}"),
      endpoint("GET", "/api/labware/standard"),
      endpoint("GET", "/api/labware/standard/{load_name}"),
    ]);
    expect(modules?.map((m) => [m.name, m.endpoints.length])).toEqual([
      ["labware", 4],
      ["standard", 2],
    ]);
  });
});

describe("filtering", () => {
  const ep = endpoint("POST", "/api/equipment/{equipment_id}/control/{action}", "Run one action");

  it("matches on path, method and summary, case-insensitively", () => {
    expect(endpointMatches(ep, "control")).toBe(true);
    expect(endpointMatches(ep, "post")).toBe(true);
    expect(endpointMatches(ep, "one action")).toBe(true);
    expect(endpointMatches(ep, "history")).toBe(false);
  });

  it("treats an empty or blank query as no filter", () => {
    expect(endpointMatches(ep, "")).toBe(true);
    expect(endpointMatches(ep, "   ")).toBe(true);
  });
});

describe("groupByTag", () => {
  const doc: OpenApiDoc = {
    paths: {
      "/api/history/uptime": { get: { tags: ["history"] } },
      "/api/equipment": { get: { tags: ["equipment"] } },
      "/api/zzz": { get: { tags: ["unlisted-router"] } },
      "/api/health": { get: { tags: ["meta"] } },
      "/api/assistant/chat": { post: { tags: ["assistant"] } },
    },
  };

  it("orders tags the way an operator meets them, unknown tags last", () => {
    expect(groupByTag(doc).map(([tag]) => tag)).toEqual([
      "meta",
      "equipment",
      "history",
      "assistant",
      "unlisted-router",
    ]);
  });

  it("gives every known tag a human title", () => {
    for (const [tag] of groupByTag(doc)) {
      if (tag === "unlisted-router") continue;
      expect(TAG_TITLE[tag]).toBeTruthy();
    }
  });

  it("sorts endpoints within a tag by path then method", () => {
    const many: OpenApiDoc = {
      paths: {
        "/api/history/uptime": { get: { tags: ["history"] } },
        "/api/history/runs": { post: { tags: ["history"] }, get: { tags: ["history"] } },
      },
    };
    const [, endpoints] = groupByTag(many)[0];
    expect(endpoints.map((e) => `${e.method} ${e.path}`)).toEqual([
      "GET /api/history/runs",
      "POST /api/history/runs",
      "GET /api/history/uptime",
    ]);
  });
});
