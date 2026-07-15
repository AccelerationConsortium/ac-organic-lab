import { describe, expect, it } from "vitest";

import { ackFailureMessage } from "./control-ack";

describe("ackFailureMessage", () => {
  it("returns the message for a soft-failed ack (ok: false)", () => {
    expect(
      ackFailureMessage({ ok: false, message: "pan limit reached", state: null }),
    ).toBe("pan limit reached");
  });

  it("falls back to a generic message when ok: false has no message", () => {
    expect(ackFailureMessage({ ok: false })).toBe(
      "Device reported the action failed",
    );
    expect(ackFailureMessage({ ok: false, message: null })).toBe(
      "Device reported the action failed",
    );
  });

  it("returns null for a successful ack", () => {
    expect(ackFailureMessage({ ok: true, message: "nudged left" })).toBeNull();
  });

  it("returns null when ok is omitted (defaults to success on the wire)", () => {
    expect(ackFailureMessage({ message: "stopped", state: null })).toBeNull();
  });

  it("returns null for non-ControlAck response shapes", () => {
    expect(ackFailureMessage(undefined)).toBeNull();
    expect(ackFailureMessage(null)).toBeNull();
    expect(ackFailureMessage("stopped")).toBeNull();
    expect(ackFailureMessage({ path: "/x.jpg", url: "/y" })).toBeNull();
  });
});
