/**
 * Digital zoom for the camera tile's live view.
 *
 * The Tapo dual-lens heads the lab runs (C245D / C246D) have no zoom axis:
 * their ONVIF PTZ node advertises pan/tilt spaces only, and the gateway
 * reports `details.has_zoom: false`. So "zoom" in the tile is a view-side
 * crop of the stream — scale the player about its centre and let the
 * operator drag the enlarged frame around. This module holds the pure
 * arithmetic so it can be unit-tested without a DOM.
 */

/** Discrete ladder of magnifications; 1 is the raw stream. */
export const ZOOM_LEVELS: readonly number[] = [1, 1.5, 2, 3, 4];

export const ZOOM_MIN = ZOOM_LEVELS[0];
export const ZOOM_MAX = ZOOM_LEVELS[ZOOM_LEVELS.length - 1];

/** Next magnification up the ladder (clamped at the top). */
export function zoomIn(level: number): number {
  const next = ZOOM_LEVELS.find((step) => step > level + 1e-9);
  return next ?? ZOOM_MAX;
}

/** Next magnification down the ladder (clamped at 1×). */
export function zoomOut(level: number): number {
  for (let i = ZOOM_LEVELS.length - 1; i >= 0; i -= 1) {
    if (ZOOM_LEVELS[i] < level - 1e-9) return ZOOM_LEVELS[i];
  }
  return ZOOM_MIN;
}

export function canZoomIn(level: number): boolean {
  return level < ZOOM_MAX - 1e-9;
}

export function canZoomOut(level: number): boolean {
  return level > ZOOM_MIN + 1e-9;
}

/** "1×", "1.5×", "2×" — no trailing ".0". */
export function formatZoom(level: number): string {
  const text = Number.isInteger(level) ? String(level) : level.toFixed(1).replace(/\.0$/, "");
  return `${text}×`;
}

export interface Offset {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

/**
 * Clamp a pan offset (CSS px, applied as `translate(x, y) scale(level)` about
 * the centre) so the enlarged frame never reveals blank space: the scaled
 * box overhangs its container by `(level - 1) / 2` of the container's size
 * on each side, and the offset may move it by at most that much. At 1× the
 * only legal offset is zero.
 */
export function clampOffset(offset: Offset, level: number, size: Size): Offset {
  if (level <= 1) return { x: 0, y: 0 };
  const maxX = (size.width * (level - 1)) / 2;
  const maxY = (size.height * (level - 1)) / 2;
  const clamp = (v: number, max: number) => Math.min(max, Math.max(-max, v));
  return { x: clamp(offset.x, maxX), y: clamp(offset.y, maxY) };
}
