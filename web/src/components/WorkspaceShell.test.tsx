// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkspaceShell } from "./WorkspaceShell";
import { DashboardChrome, DashboardTools } from "./DashboardShell";
import { MockBookings, MockContactAdmin } from "./DashboardPreviewContent";

const state = vi.hoisted(() => ({ pathname: "/preview/dashboard", loading: false, authenticated: true, identity: { role: "admin" } }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => state }));
beforeEach(() => { state.pathname = "/preview/dashboard"; state.authenticated = true; state.identity.role = "admin"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("uses one nav, marks the active route, opens the existing assistant", () => {
  const open = vi.fn(); window.addEventListener("dashboard:open-assistant", open);
  render(<><DashboardChrome>Old navigation</DashboardChrome><WorkspaceShell title="Home">Body</WorkspaceShell><DashboardTools>Existing assistant</DashboardTools></>);
  expect(screen.queryByText("Old navigation")).toBeNull();
  expect(screen.getAllByRole("navigation")).toHaveLength(1);
  expect(screen.getByRole("link", { name: "Home" }).getAttribute("aria-current")).toBe("page");
  expect(screen.getAllByText("Existing assistant")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
  expect(open).toHaveBeenCalledOnce(); window.removeEventListener("dashboard:open-assistant", open);
});

it("supports menu focus, Escape, and focus return", () => {
  render(<WorkspaceShell title="Home">Body</WorkspaceShell>);
  const menu = screen.getByRole("button", { name: "Menu" });
  fireEvent.click(menu);
  expect(menu.getAttribute("aria-expanded")).toBe("true");
  expect(document.activeElement).toBe(screen.getByRole("link", { name: "Home" }));
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(menu.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(menu);
});

it("closes menu and focuses content on navigation/back", () => {
  const view = render(<WorkspaceShell title="Home">Body</WorkspaceShell>);
  fireEvent.click(screen.getByRole("button", { name: "Menu" }));
  state.pathname = "/preview/dashboard/overview";
  view.rerender(<WorkspaceShell title="Overview">Tiles</WorkspaceShell>);
  expect(screen.getByRole("button", { name: "Menu" }).getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(screen.getByRole("main"));
  expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
});

it("unmounts preview content and tools when admin access is lost", () => {
  const view = render(<><WorkspaceShell title="Home"><div>Private tiles</div></WorkspaceShell><DashboardTools>Assistant</DashboardTools></>);
  state.identity.role = "operator";
  view.rerender(<><WorkspaceShell title="Home"><div>Private tiles</div></WorkspaceShell><DashboardTools>Assistant</DashboardTools></>);
  expect(screen.queryByText("Private tiles")).toBeNull();
  expect(screen.queryByText("Assistant")).toBeNull();
});

it("keeps mock interactions local and explicitly unsent", () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  render(<><MockBookings /><MockContactAdmin /></>);
  fireEvent.click(screen.getByRole("button", { name: "Try mock reservation" }));
  expect(screen.getByText("Mock reservation added in this screen.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel mock reservation" }));
  fireEvent.change(screen.getByLabelText("Example message"), { target: { value: "Example only" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview mock submission" }));
  expect(screen.getByText("Mock submission previewed. Nothing was sent or saved.")).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});
