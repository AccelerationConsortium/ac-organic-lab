// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import RobotMotionPage from "./page";

const auth = { loading: false, authenticated: true, requestLogin: vi.fn(), canControl: vi.fn(() => true) };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));
const PANEL = "Robot Motion — UR5e Control Workspace";
afterEach(() => {
  cleanup();
  auth.loading = false;
  auth.authenticated = true;
  auth.canControl.mockReset().mockReturnValue(true);
  auth.requestLogin.mockClear();
});

it("frames the authenticated same-origin workspace and states its boundary", () => {
  render(<RobotMotionPage />);
  expect(screen.getByTitle(PANEL).getAttribute("src")).toBe("/api/robot-motion/ligand_ur5e/web/index.html");
  expect(auth.canControl).toHaveBeenCalledWith("ligand_ur5e");
  expect(screen.getByText(/Physical control is not enabled/)).toBeTruthy();
});

it("does not request the workspace while checking sign-in", () => {
  auth.loading = true;
  render(<RobotMotionPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  expect(screen.getByText(/Checking your sign-in/)).toBeTruthy();
});

it("uses SDL2 login rather than opening an unauthenticated device frame", () => {
  auth.authenticated = false;
  render(<RobotMotionPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(auth.requestLogin).toHaveBeenCalledOnce();
});

it("focuses the account selector in the shared SDL2 auth banner", () => {
  auth.authenticated = false;
  const slot = document.createElement("div");
  slot.id = "ac-auth-banner-slot";
  const root = slot.attachShadow({ mode: "open" });
  const select = document.createElement("select");
  root.append(select);
  document.body.append(slot);
  try {
    render(<RobotMotionPage />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(root.activeElement).toBe(select);
  } finally { slot.remove(); }
});

it("requires equipment access even when signed in", () => {
  auth.canControl.mockReturnValue(false);
  render(<RobotMotionPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("does not have access");
});

it("unmounts the iframe after logout or permission loss", () => {
  const view = render(<RobotMotionPage />);
  auth.canControl.mockReturnValue(false);
  view.rerender(<RobotMotionPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  auth.authenticated = false;
  view.rerender(<RobotMotionPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
});
