// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DobotConnectionBanner } from "./DobotConnectionBanner";

const query = vi.hoisted(() => ({ data: undefined as unknown, error: null as Error | null }));
vi.mock("@/lib/use-equipment", () => ({ useEquipmentStatus: () => query }));
afterEach(() => { cleanup(); query.data = undefined; query.error = null; });
const snapshot = () => ({
  fetched_at: new Date().toISOString(), fetch_error: null, simulated: false,
  status: { equipment_status: "ready", components: { controller: { connected: true, state: "enabled" } } },
});
it("shows the confirmed controller connection with a platform link", () => {
  query.data = snapshot();
  render(<DobotConnectionBanner />);
  expect(screen.getByText("Connected · enabled")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Ligand Development" }).getAttribute("href")).toBe("/platforms/ligand_development");
});
it("does not present simulated feedback as a physical connection", () => {
  const data = snapshot(); data.status.equipment_status = "dry_run"; query.data = data;
  render(<DobotConnectionBanner />);
  expect(screen.getByText("Simulation · physical connection not verified")).toBeTruthy();
});
it("does not preserve a connected label after a failed poll", () => {
  query.data = snapshot(); query.error = new Error("Unavailable");
  render(<DobotConnectionBanner />);
  expect(screen.getByText("Connection unknown · gateway unavailable")).toBeTruthy();
});
it("labels stale feedback as unknown", () => {
  query.data = { ...snapshot(), fetched_at: new Date(Date.now() - 60_000).toISOString() };
  render(<DobotConnectionBanner />);
  expect(screen.getByText("Connection unknown · status stale")).toBeTruthy();
});
it("reports a disconnected controller separately from gateway availability", () => {
  const data = snapshot(); data.status.components.controller.connected = false; query.data = data;
  render(<DobotConnectionBanner />);
  expect(screen.getByText("Disconnected")).toBeTruthy();
});
