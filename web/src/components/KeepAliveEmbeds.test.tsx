// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KeepAliveEmbeds } from "./KeepAliveEmbeds";

let pathname = "/";
let authenticated = false;

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));
vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => ({ authenticated, loading: false }),
}));

afterEach(() => {
  cleanup();
  pathname = "/";
  authenticated = false;
});

describe("KeepAliveEmbeds", () => {
  it("mounts nothing until an embed tab is visited signed-in", () => {
    pathname = "/notebooks";
    authenticated = false;
    render(<KeepAliveEmbeds />);
    expect(screen.queryByTitle("Bitácora — Agentic ELN")).toBeNull();
  });

  it("keeps the frame mounted (hidden) after navigating away", () => {
    pathname = "/notebooks";
    authenticated = true;
    const { rerender } = render(<KeepAliveEmbeds />);
    const frame = screen.getByTitle("Bitácora — Agentic ELN");
    expect((frame.parentElement as HTMLElement).hidden).toBe(false);

    // Switch to another dashboard tab: the frame must survive, hidden —
    // unmounting would discard the ELN's in-page state.
    pathname = "/";
    rerender(<KeepAliveEmbeds />);
    const kept = screen.getByTitle("Bitácora — Agentic ELN");
    expect(kept).toBe(frame);
    expect((kept.parentElement as HTMLElement).hidden).toBe(true);

    // And coming back shows the same element again.
    pathname = "/notebooks";
    rerender(<KeepAliveEmbeds />);
    expect((screen.getByTitle("Bitácora — Agentic ELN").parentElement as HTMLElement).hidden).toBe(false);
  });

  it("hides the frame when the session expires (russian-doll guard)", () => {
    pathname = "/inventory";
    authenticated = true;
    const { rerender } = render(<KeepAliveEmbeds />);
    expect((screen.getByTitle("Chemical inventory").parentElement as HTMLElement).hidden).toBe(false);

    authenticated = false;
    rerender(<KeepAliveEmbeds />);
    expect((screen.getByTitle("Chemical inventory").parentElement as HTMLElement).hidden).toBe(true);
  });
});
