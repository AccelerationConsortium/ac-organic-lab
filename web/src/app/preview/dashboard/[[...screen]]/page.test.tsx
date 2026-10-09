import { afterEach, expect, it, vi } from "vitest";
import DashboardPreviewPage from "./page";

const state = vi.hoisted(() => ({ session: "test" }));
vi.mock("next/headers", () => ({ cookies: () => ({ get: () => ({ value: state.session }) }) }));
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("404"); }, redirect: () => { throw new Error("redirect"); } }));
vi.mock("@/components/overview/OverviewContent", () => ({ default: () => null }));
vi.mock("@/app/platforms/page", () => ({ default: () => null }));
vi.mock("@/components/WorkspaceShell", () => ({ WorkspaceShell: () => null }));
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it("independently denies rendering with the default-off switch", async () => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", undefined);
  await expect(DashboardPreviewPage({ params: {} })).rejects.toThrow("404");
});
it("independently denies a non-admin page request", async () => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, headers: new Headers({ "x-auth-user": "human", "x-auth-role": "operator" }) }));
  await expect(DashboardPreviewPage({ params: { screen: ["overview"] } })).rejects.toThrow("redirect");
});
it.each([[], ["overview"], ["platforms"], ["inventory"], ["bookings"], ["contact-admin"]])("renders a known screen after verification: %s", async (...screen) => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, headers: new Headers({ "x-auth-user": "human", "x-auth-role": "admin" }) }));
  expect(await DashboardPreviewPage({ params: { screen } })).toBeTruthy();
});
it.each(["not-a-screen", "constructor", "overview/nested"])("rejects unknown screen %s", async screen => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, headers: new Headers({ "x-auth-user": "human", "x-auth-role": "admin" }) }));
  await expect(DashboardPreviewPage({ params: { screen: screen.split("/") } })).rejects.toThrow("404");
});
