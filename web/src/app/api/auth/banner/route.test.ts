import { afterEach, expect, it, vi } from "vitest";
import { GET } from "./route";

afterEach(() => vi.unstubAllGlobals());

it("serves the shared script without forwarding credentials or adopting cookies", async () => {
  const mock = vi.fn().mockResolvedValue(new Response("/* SDL2 */", { headers: {
    "Content-Type": "application/javascript", "Set-Cookie": "not-a-session=secret",
  } }));
  vi.stubGlobal("fetch", mock);
  const response = await GET();
  expect(response.status).toBe(200);
  expect(await response.text()).toBe("/* SDL2 */");
  expect(response.headers.get("set-cookie")).toBeNull();
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(mock.mock.calls[0][1]).toMatchObject({ redirect: "error", cache: "no-store" });
  expect(mock.mock.calls[0][1].headers).toBeUndefined();
});

it.each([
  new Response("bad", { status: 500 }),
  new Response("<html>not JS</html>", { headers: { "Content-Type": "text/html" } }),
  new Response("x".repeat(128 * 1024 + 1), { headers: { "Content-Type": "text/javascript" } }),
])("refuses an invalid banner response", async response => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  expect((await GET()).status).toBe(503);
});

it("reports unavailable auth honestly", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  expect((await GET()).status).toBe(503);
});
