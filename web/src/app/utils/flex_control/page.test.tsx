// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FlexControlPage from "./page";

const auth = { loading: false, authenticated: true, requestLogin: vi.fn(), canControl: vi.fn(() => true) };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));
const PANEL = "Opentrons Flex — Gibbie Control Interface";
afterEach(() => {
  cleanup();
  auth.loading = false;
  auth.authenticated = true;
  auth.canControl.mockReset().mockReturnValue(true);
  auth.requestLogin.mockClear();
});

it("frames the gateway through the authenticated same-origin route", () => {
  render(<FlexControlPage />);
  expect(screen.getByTitle(PANEL).getAttribute("src")).toBe("/flex/gibbie/ui/");
  expect(auth.canControl).toHaveBeenCalledWith("gibbie_flex");
});

it("does not load the gateway while checking sign-in", () => {
  auth.loading = true;
  render(<FlexControlPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
});

it("requests SDL2 login before loading the gateway", () => {
  auth.authenticated = false;
  render(<FlexControlPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(auth.requestLogin).toHaveBeenCalledOnce();
});

it("requires the existing Gibbie Flex equipment role", () => {
  auth.canControl.mockReturnValue(false);
  render(<FlexControlPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("does not have access");
});

it("unloads the iframe after logout or loss of equipment access", () => {
  const view = render(<FlexControlPage />);
  auth.canControl.mockReturnValue(false);
  view.rerender(<FlexControlPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
  auth.authenticated = false;
  view.rerender(<FlexControlPage />);
  expect(screen.queryByTitle(PANEL)).toBeNull();
});
