// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import OverviewPage from "@/app/page";
import OverviewContent from "./OverviewContent";

const state = vi.hoisted(() => ({ authenticated: true, role: "admin" }));
vi.mock("@/lib/use-equipment", () => ({ useEquipmentList: () => ({ data: { equipment: [] }, isPending: false }) }));
vi.mock("@/lib/use-platforms", () => ({ usePlatforms: () => ({ data: { sections: [
  { id: "bench", title: "Bench", kind: "platform", equipment: [], href: "/platforms/bench" },
  { id: "web_services", title: "Services", kind: "platform", equipment: [] },
] }, isPending: false }) }));
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => ({ authenticated: state.authenticated, identity: { role: state.role } }) }));
vi.mock("@/components/AccountsActivitiesTile", () => ({ AccountsActivitiesTile: ({ adminLink }: { adminLink: boolean }) => <section>Accounts {adminLink && <a href="/admin">Admin detail</a>}</section> }));
vi.mock("@/components/PlatformCard", () => ({ PlatformCard: ({ title, href }: { title: string; href?: string }) => <section aria-label={title}><h2>{title}</h2>{href && <a href={href}>Details</a>}</section> }));
beforeEach(() => { sessionStorage.clear(); state.authenticated = true; state.role = "admin"; });
afterEach(cleanup);

it("renders the same Overview in the legacy and shared entry", () => {
  const legacy = render(<OverviewPage />);
  const html = legacy.container.innerHTML;
  legacy.unmount();
  const shared = render(<OverviewContent />);
  expect(shared.container.innerHTML).toBe(html);
  expect(screen.getByRole("link", { name: "Details" }).getAttribute("href")).toBe("/platforms/bench");
});

it("shares existing section visibility storage across both entries", () => {
  const shared = render(<OverviewContent />);
  fireEvent.click(screen.getByRole("button", { name: "None" }));
  expect(screen.queryByRole("region", { name: "Bench" })).toBeNull();
  expect(screen.getByRole("link", { name: "Admin detail" })).toBeTruthy();
  shared.unmount();
  render(<OverviewPage />);
  expect(screen.getByRole("button", { name: "Bench 0" }).getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  expect(screen.getByRole("region", { name: "Bench" })).toBeTruthy();
});

it("preserves account tile and admin-link permissions", () => {
  state.role = "operator";
  const view = render(<OverviewContent />);
  expect(screen.getByText("Accounts")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Admin detail" })).toBeNull();
  state.authenticated = false;
  view.rerender(<OverviewContent />);
  expect(screen.queryByText("Accounts")).toBeNull();
});
