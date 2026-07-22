// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGatedLink } from "./AuthGatedLink";

// Controllable stand-in for the auth context. Individual tests mutate the
// fields; the component reads them through useUserAuth().
const auth = {
  authenticated: false,
  canControl: vi.fn(() => false),
  requestLogin: vi.fn(),
};
vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => auth,
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  auth.authenticated = false;
  auth.canControl = vi.fn(() => false);
  auth.requestLogin = vi.fn();
});

function renderLink() {
  render(
    <AuthGatedLink href="http://edge/xarm5/web/" equipmentId="xarm" external>
      Open control panel ↗
    </AuthGatedLink>,
  );
  return screen.getByText("Open control panel ↗");
}

describe("AuthGatedLink", () => {
  it("navigates normally when the user holds a role on the equipment", () => {
    auth.authenticated = true;
    auth.canControl = vi.fn(() => true);
    const link = renderLink();
    const click = fireEvent.click(link);
    expect(click).toBe(true); // default not prevented
    expect(link.getAttribute("aria-disabled")).toBeNull();
    expect(screen.queryByText("Not authorized")).toBeNull();
  });

  it("blocks the click and shows the bubble when signed in without access", () => {
    auth.authenticated = true;
    auth.canControl = vi.fn(() => false);
    const link = renderLink();
    const click = fireEvent.click(link);
    expect(click).toBe(false); // preventDefault fired
    expect(screen.getByText("Not authorized")).toBeTruthy();
    expect(auth.requestLogin).not.toHaveBeenCalled();
  });

  it("auto-dismisses the bubble after ~1.5 s", () => {
    vi.useFakeTimers();
    auth.authenticated = true;
    const link = renderLink();
    fireEvent.click(link);
    expect(screen.getByText("Not authorized")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(screen.queryByText("Not authorized")).toBeNull();
  });

  it("also nudges the login bar when signed out", () => {
    const link = renderLink();
    const click = fireEvent.click(link);
    expect(click).toBe(false);
    expect(screen.getByText("Not authorized")).toBeTruthy();
    expect(auth.requestLogin).toHaveBeenCalledTimes(1);
  });

  it("renders nothing for unauthorized viewers when hideUnauthorized is set", () => {
    auth.authenticated = true;
    auth.canControl = vi.fn(() => false);
    render(
      <AuthGatedLink
        href="http://100.64.254.6:8005/"
        equipmentId="uptime_kuma"
        external
        hideUnauthorized
      >
        Open ↗
      </AuthGatedLink>,
    );
    expect(screen.queryByText("Open ↗")).toBeNull();
  });

  it("still renders for authorized viewers when hideUnauthorized is set", () => {
    auth.authenticated = true;
    auth.canControl = vi.fn(() => true);
    render(
      <AuthGatedLink
        href="http://100.64.254.6:8005/"
        equipmentId="uptime_kuma"
        external
        hideUnauthorized
      >
        Open ↗
      </AuthGatedLink>,
    );
    const link = screen.getByText("Open ↗");
    expect(fireEvent.click(link)).toBe(true);
  });
});
