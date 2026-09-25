// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import HplcControlPage from "./page";

const auth = { loading: false, authenticated: true, requestLogin: vi.fn() };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));
const TITLE = "HPLC acquisition control preview — simulated";
afterEach(() => { cleanup(); auth.loading = false; auth.authenticated = true; auth.requestLogin.mockClear(); });

it("embeds only the protected simulation with an isolated script context", () => {
  render(<HplcControlPage />);
  const frame = screen.getByTitle(TITLE);
  expect(frame.getAttribute("src")).toBe("/equipment/lle_hplc/control/frame#theme=light");
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
});

it("hands the dashboard theme to the frame", () => {
  document.documentElement.classList.add("dark");
  try {
    render(<HplcControlPage />);
    expect(screen.getByTitle(TITLE).getAttribute("src")).toBe("/equipment/lle_hplc/control/frame#theme=dark");
  } finally { document.documentElement.classList.remove("dark"); }
});

it("does not request the frame until sign-in is known", () => {
  auth.loading = true;
  render(<HplcControlPage />);
  expect(screen.queryByTitle(TITLE)).toBeNull();
});

it("uses the existing banner to sign in", () => {
  auth.authenticated = false;
  const slot = document.createElement("div"); slot.id = "ac-auth-banner-slot";
  const shadow = slot.attachShadow({ mode: "open" });
  const select = document.createElement("select"); shadow.append(select); document.body.append(slot);
  try {
    render(<HplcControlPage />);
    expect(screen.queryByTitle(TITLE)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(auth.requestLogin).toHaveBeenCalledOnce();
    expect(shadow.activeElement).toBe(select);
  } finally { slot.remove(); }
});

it("unmounts the preview when the shared session is lost", () => {
  const view = render(<HplcControlPage />);
  auth.authenticated = false; view.rerender(<HplcControlPage />);
  expect(screen.queryByTitle(TITLE)).toBeNull();
});
