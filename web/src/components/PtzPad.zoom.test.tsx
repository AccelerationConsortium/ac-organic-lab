// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PtzPad } from "./PtzPad";

afterEach(cleanup);

describe("PtzPad optical zoom column", () => {
  it("is absent by default — the Tapo dual-lens heads have no zoom axis", () => {
    render(<PtzPad onMove={vi.fn()} onStop={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /optical zoom/i })).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(9); // 8 directions + stop
  });

  it("renders press-and-hold zoom cells when the camera reports a zoom axis", () => {
    const onMove = vi.fn();
    const onStop = vi.fn();
    render(<PtzPad onMove={onMove} onStop={onStop} zoomAxis />);
    const zoomIn = screen.getByRole("button", { name: "Optical zoom in" });
    fireEvent.pointerDown(zoomIn, { pointerId: 1 });
    expect(onMove).toHaveBeenCalledWith("zoom_in");
    fireEvent.pointerUp(zoomIn, { pointerId: 1 });
    expect(onStop).toHaveBeenCalledTimes(1);

    fireEvent.pointerDown(screen.getByRole("button", { name: "Optical zoom out" }), { pointerId: 1 });
    expect(onMove).toHaveBeenLastCalledWith("zoom_out");
  });
});
