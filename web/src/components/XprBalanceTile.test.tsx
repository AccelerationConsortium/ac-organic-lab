// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { XprBalanceTile } from "./XprBalanceTile";
import type { EquipmentSnapshot } from "@/types/api";
const mocks = vi.hoisted(() => ({ locked: false, email: "owner@example.test", access: vi.fn(), send: vi.fn() }));
vi.mock("@/lib/xpr-api", () => ({ getXprAccess: mocks.access, postXprAction: mocks.send }));
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => ({ identity: { email: mocks.email } }) }));
vi.mock("@/lib/use-control-lock", () => ({ useControlLock: () => ({ locked: mocks.locked, unlock: vi.fn() }) }));
const snapshot = () => ({ id: "lle_xpr_balance", name: "XPR", kind: "other", fetched_at: new Date().toISOString(), latency_ms: 1, fetch_error: null,
 status: { equipment_status: "ready", allowed_actions: ["weigh", "tare", "zero", "dose.start", "door.open", "door.close"], required_actions: [], metrics: { weight: { value: 1.2345, unit: "g" } }, components: {}, details: {} } }) as unknown as EquipmentSnapshot;
afterEach(() => { cleanup(); mocks.access.mockReset(); mocks.send.mockReset(); mocks.locked = false; mocks.email = "owner@example.test"; });
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
