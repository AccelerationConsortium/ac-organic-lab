// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import XarmControlPage from "./page";

const auth = { loading: false, authenticated: true, requestLogin: vi.fn() };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));

afterEach(() => {
  cleanup();
  auth.loading = false;
  auth.authenticated = true;
  auth.requestLogin = vi.fn();
});

const PANEL = "xArm Translocation — Control Interface";

describe("XarmControlPage", () => {
  it("frames the device panel when signed in", () => {
    render(<XarmControlPage />);

    const frame = screen.getByTitle(PANEL);
    // Same-origin path, not the device's own address: that is what puts it
    // behind the edge and lets the identity be passed through.
    expect(frame.getAttribute("src")).toBe("/xarm5/web/");
  });

  it("does not frame the panel when nobody is signed in", () => {
    // /xarm5/web/ answers an unauthenticated request with a JSON 401, which a
    // browser renders — so the frame would show raw JSON.
    auth.authenticated = false;
    render(<XarmControlPage />);

    expect(screen.queryByTitle(PANEL)).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("offers to start the login flow instead", () => {
    auth.authenticated = false;
    render(<XarmControlPage />);

    screen.getByRole("button", { name: "Sign in" }).click();
    expect(auth.requestLogin).toHaveBeenCalledOnce();
  });

  it("frames nothing while the session is still being checked", () => {
    auth.loading = true;
    render(<XarmControlPage />);

    expect(screen.queryByTitle(PANEL)).toBeNull();
    expect(screen.getByText(/Checking your sign-in/)).toBeTruthy();
  });
});
