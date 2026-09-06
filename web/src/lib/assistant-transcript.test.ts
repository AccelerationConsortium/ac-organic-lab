import { describe, expect, it } from "vitest";
import { conversationExport, conversationMarkdown } from "./assistant-transcript";

describe("conversation downloads", () => {
  it("projects visible history without live approval state or raw tool payloads", () => {
    const turn = {
      role: "assistant" as const, text: "Proposed route", tools: [{ name: "status", ok: true, raw: "private" }],
      completion: "streaming" as const,
      plan_id: "live-plan", step_hash: "approval-hash", phaseSince: 123,
      control_events: [{ event: "action_proposed", occurred_at: "2026-09-06T00:00:00Z", detail: { action: "move.a" } }],
      images: [{ url: "/api/assistant/snapshots/example.jpg", camera_id: "camera" }],
    };
    const exported = conversationExport([turn]);
    expect(exported.executable).toBe(false);
    expect(exported.messages[0].completion).toBe("streaming");
    expect(exported.messages[0].control_events).toEqual(turn.control_events);
    expect(exported.notice).toContain("not embedded");
    expect(JSON.stringify(exported)).not.toMatch(/live-plan|approval-hash|private|phaseSince/);
  });

  it("keeps failures and interrupted text truthful in both formats", () => {
    const exported = conversationExport([
      { role: "assistant", text: "Partial ```answer```", tools: [], stopped: true },
      { role: "assistant", text: "", tools: [], completion: "failed", error: "Connection failed" },
    ], new Date("2026-09-06T00:00:00Z"));
    expect(exported.messages.map((turn) => turn.completion)).toEqual(["interrupted", "failed"]);
    const md = conversationMarkdown(exported);
    expect(md).toContain("````\nPartial ```answer```\n````");
    expect(md).toContain("Connection failed");
    expect(md).toContain("not a registered Plan");
  });
});
