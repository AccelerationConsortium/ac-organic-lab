// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MjpegPlayer } from "./MjpegPlayer";

vi.mock("@/lib/camera-session", () => ({
  openMjpegSession: vi.fn(async () => "/api/camera-streams/sessions/s1/mjpeg"),
}));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function renderStream(depth: boolean) {
  render(<MjpegPlayer src="/streams/api/ws?src=flex_pipette_depth" depth={depth} />);
  const img = (await screen.findByAltText("Live camera stream")) as HTMLImageElement;
  // A 1280x720 frame shown letterboxed in a 640x480 box: 640x360, 60 px bars.
  Object.defineProperty(img, "naturalWidth", { value: 1280 });
  Object.defineProperty(img, "naturalHeight", { value: 720 });
  img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 480 }) as DOMRect;
  return img;
}

it("maps a click to frame pixels and shows the distance there", async () => {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ distance_m: 0.4125 }) }));
  vi.stubGlobal("fetch", fetcher);
  const img = await renderStream(true);
  await act(async () => { fireEvent.click(img, { clientX: 320, clientY: 240 }); });
  expect(fetcher).toHaveBeenCalledWith("/api/camera-streams/sessions/s1/depth?x=640&y=360", expect.anything());
  expect(screen.getByTestId("depth-reading").textContent).toBe("413 mm");
});

it("ignores clicks on the letterbox bars and reports holes honestly", async () => {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ distance_m: null }) }));
  vi.stubGlobal("fetch", fetcher);
  const img = await renderStream(true);
  await act(async () => { fireEvent.click(img, { clientX: 320, clientY: 30 }); });
  expect(fetcher).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(img, { clientX: 10, clientY: 100 }); });
  expect(screen.getByTestId("depth-reading").textContent).toBe("no depth here");
});

it("does not measure on a lens without a depth readout", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const img = await renderStream(false);
  fireEvent.click(img, { clientX: 320, clientY: 240 });
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByText(/measure distance/)).toBeNull();
});
