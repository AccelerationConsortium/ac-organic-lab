// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserAuthProvider, useUserAuth } from "./user-auth";

function CurrentUser() {
  const auth = useUserAuth();
  return <>
    <span>{auth.loading ? "loading" : auth.identity?.email ?? "signed out"}</span>
    <button onClick={() => void auth.logout()}>Logout</button>
    <button onClick={() => void auth.verifyCode("bob", "123456")}>Login</button>
  </>;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

it("refreshes shared-cookie identity after another tab changes session, and on focus", async () => {
  let user: string | null = "alice@example.edu";
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    return { ok: true, json: async () => url === "/api/auth/me"
      ? { authenticated: !!user, identity: user ? { email: user, role: "operator" } : null }
      : {} };
  });
  vi.stubGlobal("fetch", fetch);
  render(<UserAuthProvider><CurrentUser /></UserAuthProvider>);
  await screen.findByText("alice@example.edu");
  user = "bob@example.edu";
  fireEvent(window, new StorageEvent("storage", { key: "ac-auth-session-changed", newValue: "changed" }));
  await screen.findByText("bob@example.edu");
  expect(screen.queryByText("alice@example.edu")).toBeNull();
  user = null;
  fireEvent.focus(window);
  await screen.findByText("signed out");
});

it("announces local login/logout and ignores an identity response older than logout", async () => {
  let finishLookup!: (value: unknown) => void;
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/auth/me") return { ok: true, json: () => new Promise((resolve) => { finishLookup = resolve; }) };
    return { ok: true, json: async () => ({ email: "bob@example.edu", role: "operator" }) };
  });
  vi.stubGlobal("fetch", fetch);
  render(<UserAuthProvider><CurrentUser /></UserAuthProvider>);
  await waitFor(() => expect(finishLookup).toBeDefined());
  fireEvent.click(screen.getByText("Login"));
  await screen.findByText("bob@example.edu");
  const loginNotice = localStorage.getItem("ac-auth-session-changed");
  expect(loginNotice).toBeTruthy();
  fireEvent.click(screen.getByText("Logout"));
  await screen.findByText("signed out");
  expect(localStorage.getItem("ac-auth-session-changed")).not.toBe(loginNotice);
  await act(async () => { finishLookup({ authenticated: true, identity: { email: "alice@example.edu", role: "operator" } }); });
  expect(screen.getByText("signed out")).toBeTruthy();
});
