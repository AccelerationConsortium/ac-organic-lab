import { describe, expect, it } from "vitest";

import {
  ZOOM_MAX,
  ZOOM_MIN,
  canZoomIn,
  canZoomOut,
  clampOffset,
  formatZoom,
  zoomIn,
  zoomOut,
} from "./video-zoom";

describe("zoom ladder", () => {
  it("steps up and down the ladder and clamps at both ends", () => {
    expect(zoomIn(1)).toBe(1.5);
    expect(zoomIn(1.5)).toBe(2);
    expect(zoomIn(3)).toBe(4);
    expect(zoomIn(4)).toBe(ZOOM_MAX);
    expect(zoomOut(4)).toBe(3);
    expect(zoomOut(1.5)).toBe(1);
    expect(zoomOut(1)).toBe(ZOOM_MIN);
  });

  it("snaps an off-ladder level to the neighbouring steps", () => {
    expect(zoomIn(1.7)).toBe(2);
    expect(zoomOut(1.7)).toBe(1.5);
  });

  it("reports whether either direction is still possible", () => {
    expect(canZoomIn(1)).toBe(true);
    expect(canZoomIn(ZOOM_MAX)).toBe(false);
    expect(canZoomOut(1)).toBe(false);
    expect(canZoomOut(2)).toBe(true);
  });

  it("formats levels without a trailing .0", () => {
    expect(formatZoom(1)).toBe("1×");
    expect(formatZoom(1.5)).toBe("1.5×");
    expect(formatZoom(2)).toBe("2×");
  });
});

describe("clampOffset", () => {
  const size = { width: 400, height: 200 };

  it("forces zero offset at 1× (nothing to pan)", () => {
    expect(clampOffset({ x: 50, y: -30 }, 1, size)).toEqual({ x: 0, y: 0 });
  });

  it("keeps the enlarged frame covering the container", () => {
    // 2×: the scaled box overhangs by half the container on each side.
    expect(clampOffset({ x: 500, y: -500 }, 2, size)).toEqual({ x: 200, y: -100 });
    expect(clampOffset({ x: 10, y: 20 }, 2, size)).toEqual({ x: 10, y: 20 });
  });

  it("re-clamps a previously legal offset when zooming back out", () => {
    const at4x = clampOffset({ x: 600, y: 300 }, 4, size);
    expect(at4x).toEqual({ x: 600, y: 300 });
    expect(clampOffset(at4x, 1.5, size)).toEqual({ x: 100, y: 50 });
  });
});
