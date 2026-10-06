// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DashboardChrome, DashboardContent } from "./DashboardShell";

const route = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
afterEach(cleanup);

function Shell() {
  return <>
    <div id="ac-auth-banner-slot">Auth banner</div>
    <DashboardChrome><nav>Dashboard navigation</nav></DashboardChrome>
    <DashboardContent>Control Interface</DashboardContent>
    <DashboardChrome><footer>Dashboard footer</footer></DashboardChrome>
  </>;
}

it.each(["/equipment/lle_hplc/control", "/equipment/lle_hplc/control/"])("shows only auth and content on %s", pathname => {
  route.pathname = pathname;
  render(<Shell />);
  expect(screen.getByText("Auth banner")).toBeTruthy();
  expect(screen.getByRole("main").textContent).toBe("Control Interface");
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(screen.queryByText("Dashboard footer")).toBeNull();
  expect(screen.getByRole("main").className).toContain("overflow-hidden");
});

// /utils/robot_motion was retired 2026-10-06 (the UR5e panel is the device's
// own page at /ur5e/web/); it must render as an ordinary dashboard route now.
it.each(["/", "/platforms", "/utils/computers", "/utils/robot_motion", "/equipment/lle_hplc/control_extra"])("preserves dashboard chrome on %s", pathname => {
  route.pathname = pathname;
  render(<Shell />);
  expect(screen.getByRole("navigation")).toBeTruthy();
  expect(screen.getByText("Dashboard footer")).toBeTruthy();
  expect(screen.getByRole("main").className).toContain("overflow-y-auto");
});

it("restores dashboard chrome after client-side navigation", () => {
  route.pathname = "/equipment/lle_hplc/control";
  const view = render(<Shell />);
  route.pathname = "/utils/computers";
  view.rerender(<Shell />);
  expect(screen.getByRole("navigation")).toBeTruthy();
});
