// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ZoomControls } from "./ZoomControls";

afterEach(cleanup);

const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

function renderControls(level: number, disabled = false) {
  const onZoomIn = vi.fn();
  const onZoomOut = vi.fn();
  const onReset = vi.fn();
  render(
    <ZoomControls
      level={level}
      disabled={disabled}
      onZoomIn={onZoomIn}
      onZoomOut={onZoomOut}
      onReset={onReset}
    />,
  );
  return { onZoomIn, onZoomOut, onReset };
}

describe("ZoomControls", () => {
  it("shows the level and only allows zooming in at 1×", () => {
    const { onZoomIn, onZoomOut, onReset } = renderControls(1);
    expect(button("Reset zoom").textContent).toBe("1×");
    expect(button("Zoom out").disabled).toBe(true);
    expect(button("Reset zoom").disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(onZoomIn).toHaveBeenCalledTimes(1);
    expect(onZoomOut).not.toHaveBeenCalled();
    expect(onReset).not.toHaveBeenCalled();
  });

  it("allows zooming out and resetting once zoomed, and stops at the top of the ladder", () => {
    const { onZoomOut, onReset } = renderControls(4);
    expect(button("Zoom in").disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
    expect(onZoomOut).toHaveBeenCalledTimes(1);
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(button("Reset zoom").textContent).toBe("4×");
  });

  it("disables everything when there is no stream to zoom", () => {
    renderControls(2, true);
    for (const name of ["Zoom in", "Zoom out", "Reset zoom"]) {
      expect(button(name).disabled).toBe(true);
    }
  });
});
