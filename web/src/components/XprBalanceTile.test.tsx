// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { XprBalanceTile } from "./XprBalanceTile";
import type { EquipmentSnapshot } from "@/types/api";
const mocks = vi.hoisted(() => ({ locked: false, email: "owner@example.test", access: vi.fn(), send: vi.fn(), live: vi.fn() }));
vi.mock("@/lib/xpr-api", () => ({ getXprAccess: mocks.access, postXprAction: mocks.send, getXprLiveStatus: mocks.live }));
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => ({ identity: { email: mocks.email } }) }));
vi.mock("@/lib/use-control-lock", () => ({ useControlLock: () => ({ locked: mocks.locked, unlock: vi.fn() }) }));
const snapshot = () => ({ id: "lle_xpr_balance", name: "XPR", kind: "other", fetched_at: new Date().toISOString(), latency_ms: 1, fetch_error: null,
 status: { equipment_status: "ready", allowed_actions: ["weigh", "tare", "zero", "dose.start", "door.open", "door.close"], required_actions: [], metrics: { weight: { value: 1.2345, unit: "g" } }, components: {}, details: {} } }) as unknown as EquipmentSnapshot;
afterEach(() => { cleanup(); mocks.access.mockReset(); mocks.send.mockReset(); mocks.live.mockReset(); mocks.locked = false; mocks.email = "owner@example.test"; });
it("keeps controls disabled when device-side access is refused", async () => {
 mocks.access.mockRejectedValue(new Error("Account not authorized"));
 render(<XprBalanceTile snapshot={snapshot()} />);
 await screen.findByText("Account not authorized");
 expect((screen.getByRole("button", { name: "Weigh" }) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole("button", { name: "Weigh" }));
 expect(mocks.send).not.toHaveBeenCalled();
});
it("requires owner access and an explicit dose confirmation", async () => {
 mocks.access.mockResolvedValue({ allowed: true }); mocks.send.mockResolvedValue({ ok: true, details: { job_id: "job-1" } });
 render(<XprBalanceTile snapshot={snapshot()} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Review dose" }) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.change(screen.getByLabelText("Substance"), { target: { value: "NaCl" } });
 fireEvent.change(screen.getByLabelText("Dose amount (mg)"), { target: { value: "10" } });
 fireEvent.click(screen.getByRole("button", { name: "Review dose" }));
 expect(mocks.send).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button", { name: "Confirm dose" }));
 await screen.findByText(/Dose accepted/);
 expect(mocks.send).toHaveBeenCalledTimes(1);
 expect(mocks.send).toHaveBeenCalledWith("lle_xpr_balance", "dose/start", { substance_name: "NaCl", dose_amount_mg: 10 });
});
it("does not carry owner access to another signed-in identity", async () => {
 mocks.access.mockResolvedValue({ allowed: true });
 const { rerender } = render(<XprBalanceTile snapshot={snapshot()} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Weigh" }) as HTMLButtonElement).disabled).toBe(false));
 mocks.email = "other@example.test"; mocks.access.mockImplementation(() => new Promise(() => {}));
 rerender(<XprBalanceTile snapshot={snapshot()} />);
 expect((screen.getByRole("button", { name: "Weigh" }) as HTMLButtonElement).disabled).toBe(true);
});
it("honors live allowed_actions and blocks double submissions", async () => {
 mocks.access.mockResolvedValue({ allowed: true }); mocks.send.mockImplementation(() => new Promise(() => {}));
 const s = snapshot(); s.status.allowed_actions = ["weigh"];
 render(<XprBalanceTile snapshot={s} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Weigh" }) as HTMLButtonElement).disabled).toBe(false));
 expect((screen.getByRole("button", { name: "Tare" }) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole("button", { name: "Weigh" }));
 fireEvent.click(screen.getByRole("button", { name: "Weigh" }));
 expect(mocks.send).toHaveBeenCalledTimes(1);
});
it("offers Clear error only while last_error is set, and posts it", async () => {
 mocks.access.mockResolvedValue({ allowed: true }); mocks.send.mockResolvedValue({ ok: true, message: "Error cleared" });
 const s = snapshot(); s.status.equipment_status = "error"; s.status.allowed_actions = ["startup", "shutdown", "cancel", "clear_error"];
 s.status.last_error = { code: "request_failed", message: "tare: Tare did not complete within 60 s: the balance never reported a stable reading", severity: "error", timestamp: new Date().toISOString() };
 render(<XprBalanceTile snapshot={s} />);
 const clear = await screen.findByRole("button", { name: "Clear error" });
 await waitFor(() => expect((clear as HTMLButtonElement).disabled).toBe(false));
 expect((screen.getByRole("button", { name: "Tare" }) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(clear);
 await screen.findByText("Error cleared");
 expect(mocks.send).toHaveBeenCalledWith("lle_xpr_balance", "clear_error", {});
});
it("shows no Clear error button when there is nothing to clear", async () => {
 mocks.access.mockResolvedValue({ allowed: true });
 render(<XprBalanceTile snapshot={snapshot()} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Weigh" }) as HTMLButtonElement).disabled).toBe(false));
 expect(screen.queryByRole("button", { name: "Clear error" })).toBeNull();
});
it("waits for stability by default and sends immediately=true when the wait is switched off", async () => {
 mocks.access.mockResolvedValue({ allowed: true }); mocks.send.mockResolvedValue({ ok: true, message: "Tared" });
 render(<XprBalanceTile snapshot={snapshot()} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Tare" }) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(screen.getByRole("button", { name: "Tare" }));
 await screen.findByText("Tared");
 expect(mocks.send).toHaveBeenLastCalledWith("lle_xpr_balance", "tare", { immediately: false });
 fireEvent.click(screen.getByLabelText(/Wait for a stable reading/));
 fireEvent.click(screen.getByRole("button", { name: "Zero" }));
 await waitFor(() => expect(mocks.send).toHaveBeenLastCalledWith("lle_xpr_balance", "zero", { immediately: true }));
});
it("tells the operator what a long stable weigh is doing", async () => {
 mocks.access.mockResolvedValue({ allowed: true }); mocks.send.mockImplementation(() => new Promise(() => {}));
 render(<XprBalanceTile snapshot={snapshot()} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Weigh" }) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(screen.getByRole("button", { name: "Weigh" }));
 await screen.findByText(/waiting for a stable reading \(up to 60 s\)/);
});
it("does not offer moving a door to where it already is", async () => {
 mocks.access.mockResolvedValue({ allowed: true });
 const s = snapshot();
 s.status.components = { door_left: { connected: true, state: "open", message: "100 %" }, door_right: { connected: true, state: "closed", message: "0 %" } };
 render(<XprBalanceTile snapshot={s} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Close left door" }) as HTMLButtonElement).disabled).toBe(false));
 expect((screen.getByRole("button", { name: "Open left door" }) as HTMLButtonElement).disabled).toBe(true);
 expect((screen.getByRole("button", { name: "Open right door" }) as HTMLButtonElement).disabled).toBe(false);
 expect((screen.getByRole("button", { name: "Close right door" }) as HTMLButtonElement).disabled).toBe(true);
 screen.getByText("Doors: left open · right closed");
});
it("shows the device's live state right after an action, until the polled snapshot is newer", async () => {
 mocks.access.mockResolvedValue({ allowed: true }); mocks.send.mockResolvedValue({ ok: true, message: "left door -> 100 %" });
 const polled = snapshot(); polled.fetched_at = "2026-09-10T23:00:00Z";
 polled.status.components = { door_left: { connected: true, state: "closed", message: "0 %" }, door_right: { connected: true, state: "closed", message: "0 %" } };
 const live = snapshot(); live.fetched_at = "2026-09-10T23:00:03Z";
 live.status.components = { door_left: { connected: true, state: "open", message: "100 %" }, door_right: { connected: true, state: "closed", message: "0 %" } };
 mocks.live.mockResolvedValue(live);
 const { rerender } = render(<XprBalanceTile snapshot={polled} />);
 await waitFor(() => expect((screen.getByRole("button", { name: "Open left door" }) as HTMLButtonElement).disabled).toBe(false));
 fireEvent.click(screen.getByRole("button", { name: "Open left door" }));
 await screen.findByText("Doors: left open · right closed");          // live read won, ~0.6 s after the action, not 5 s
 expect(mocks.live).toHaveBeenCalledWith("lle_xpr_balance");
 expect((screen.getByRole("button", { name: "Open left door" }) as HTMLButtonElement).disabled).toBe(true);
 const stale = snapshot(); stale.fetched_at = "2026-09-10T23:00:01Z"; stale.status.components = polled.status.components;
 rerender(<XprBalanceTile snapshot={stale} />);
 screen.getByText("Doors: left open · right closed");                 // an older poll does not roll the display back
 const fresh = snapshot(); fresh.fetched_at = "2026-09-10T23:00:06Z"; fresh.status.components = live.status.components;
 rerender(<XprBalanceTile snapshot={fresh} />);
 screen.getByText("Doors: left open · right closed");
});
