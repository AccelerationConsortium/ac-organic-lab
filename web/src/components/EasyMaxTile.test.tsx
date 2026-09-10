// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { EasyMaxTile } from "./EasyMaxTile";

const mocks = vi.hoisted(() => ({
  locked: false,
  send: vi.fn(async () => ({ ok: true })),
}));
vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: mocks.locked,
    countdown: 0,
    toggle: vi.fn(),
  }),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<object>()),
  postEasyMaxAction: mocks.send,
}));
function snapshot(): EquipmentSnapshot {
  const zone = (tr: number) => ({
    thermostat: {
      tr_c: tr,
      tj_c: 24,
      end_value_c: tr,
      mode: "Tr",
      state: "on",
    },
    stirrer: { rate_rpm: 100, end_value_rpm: 100, state: "on" },
  });
  return {
    id: "lle_easymax",
    name: "EasyMax",
    kind: "other",
    fetched_at: new Date().toISOString(),
    latency_ms: 10,
    fetch_error: null,
    status: {
      equipment_status: "ready",
      allowed_actions: ["temp.set", "temp.stop", "stir.start", "stir.stop"],
      required_actions: [],
      metrics: {},
      components: {},
      details: { zones: { "1": zone(25), "2": zone(21) } },
    },
  } as unknown as EquipmentSnapshot;
}
afterEach(() => {
  cleanup();
  mocks.send.mockReset();
  mocks.send.mockResolvedValue({ ok: true });
  mocks.locked = false;
});
describe("EasyMax human controls", () => {
  it("reviews a zone-specific command and sends exactly once after confirmation", async () => {
    render(<EasyMaxTile snapshot={snapshot()} />);
    fireEvent.click(screen.getByRole("button", { name: /Reactor 2/ }));
    fireEvent.change(screen.getByLabelText("Target temperature"), {
      target: { value: "30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review temperature" }));
    expect(mocks.send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));
    await waitFor(() =>
      expect(mocks.send).toHaveBeenCalledWith("lle_easymax", {
        action: "temp/set",
        body: {
          reactor: 2,
          mode: "Tr",
          end_value_c: 30,
          ramp_mode: "duration",
          rate_or_duration: 300,
        },
      }),
    );
    expect(mocks.send).toHaveBeenCalledTimes(1);
  });
  it.each(["locked", "claimed", "stale", "unreachable", "unavailable"])(
    "blocks commands when %s",
    (reason) => {
      const s = snapshot();
      if (reason === "locked") mocks.locked = true;
      if (reason === "claimed")
        s.status.details = {
          ...s.status.details,
          claimed_by: { owner: "operator" },
        };
      if (reason === "stale") s.fetched_at = "2020-01-01T00:00:00Z";
      if (reason === "unreachable")
        s.fetch_error = { kind: "timeout", message: "offline" };
      if (reason === "unavailable") s.status.allowed_actions = [];
      render(<EasyMaxTile snapshot={s} />);
      expect(
        (
          screen.getByRole("button", {
            name: "Review temperature",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
      expect(
        (
          screen.getByRole("button", {
            name: "Stop stirring",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
      expect(mocks.send).not.toHaveBeenCalled();
    },
  );
  it("invalidates a reviewed command when allowed actions change", () => {
    const s = snapshot();
    const { rerender } = render(<EasyMaxTile snapshot={s} />);
    fireEvent.click(screen.getByRole("button", { name: "Review stirring" }));
    rerender(
      <EasyMaxTile
        snapshot={{ ...s, status: { ...s.status, allowed_actions: [] } }}
      />,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Confirm action",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(mocks.send).not.toHaveBeenCalled();
  });
  it("does not send blank targets and clears review when switching zones", () => {
    render(<EasyMaxTile snapshot={snapshot()} />);
    fireEvent.change(screen.getByLabelText("Target temperature"), {
      target: { value: "" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Review temperature",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Stop stirring" }));
    fireEvent.click(screen.getByRole("button", { name: /Reactor 2/ }));
    expect(screen.queryByRole("button", { name: "Confirm action" })).toBeNull();
    expect(mocks.send).not.toHaveBeenCalled();
  });
  it("surfaces refusals without retrying or claiming success", async () => {
    mocks.send.mockRejectedValueOnce(new Error("Device refused the action"));
    render(<EasyMaxTile snapshot={snapshot()} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop temperature" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));
    await waitFor(() =>
      expect(screen.queryByText("Sending command…")).toBeNull(),
    );
    expect(mocks.send).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/accepted\. Live/)).toBeNull();
    expect(screen.getByRole("button", { name: /action/i })).toBeTruthy();
  });
});

it("sends a rate ramp with K/min units after review", async () => {
  render(<EasyMaxTile snapshot={snapshot()} />);
  fireEvent.change(screen.getByLabelText("Temperature ramp mode"), { target: { value: "rate" } });
  fireEvent.change(screen.getByLabelText("Temperature ramp rate"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Review temperature" }));
  expect(mocks.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));
  await waitFor(() => expect(mocks.send).toHaveBeenCalledWith("lle_easymax", {
    action: "temp/set", body: { reactor: 1, mode: "Tr", end_value_c: 25, ramp_mode: "rate", rate_or_duration: 2 },
  }));
});

it.each(["temp/reflux", "temp/distill"] as const)("preserves the Tj minus Tr sign for %s", async (action) => {
  const s = snapshot();
  s.status.allowed_actions = [...(s.status.allowed_actions ?? []), action.replace("/", ".")];
  render(<EasyMaxTile snapshot={s} />);
  fireEvent.click(screen.getByText("Reflux / Distillation"));
  fireEvent.change(screen.getByLabelText("Thermal operation"), { target: { value: action } });
  fireEvent.change(screen.getByLabelText("Jacket end temperature"), { target: { value: "40" } });
  fireEvent.change(screen.getByLabelText("Jacket minus reactor offset"), { target: { value: "-5" } });
  fireEvent.click(screen.getByRole("button", { name: "Review thermal operation" }));
  expect(mocks.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" }));
  await waitFor(() => expect(mocks.send).toHaveBeenCalledWith("lle_easymax", {
    action, body: { reactor: 1, tj_end_c: 40, tj_minus_tr_k: -5 },
  }));
});

it("uses configured limits and labels the moving value as a setpoint", () => {
  const s = snapshot();
  s.status.details = { ...s.status.details, limits: { temperature_max_c: 28, ramp_rate_max_k_per_min: 1 } };
  render(<EasyMaxTile snapshot={s} />);
  expect(screen.getByText(/Setpoint: 25.0/)).toBeTruthy();
  expect(screen.queryByText(/Target: 25.0/)).toBeNull();
  fireEvent.change(screen.getByLabelText("Target temperature"), { target: { value: "30" } });
  expect((screen.getByRole("button", { name: "Review temperature" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Target temperature"), { target: { value: "25" } });
  fireEvent.change(screen.getByLabelText("Temperature ramp mode"), { target: { value: "rate" } });
  fireEvent.change(screen.getByLabelText("Temperature ramp rate"), { target: { value: "2" } });
  expect((screen.getByRole("button", { name: "Review temperature" }) as HTMLButtonElement).disabled).toBe(true);
});


it("does not prefill a ramp's moving setpoint as the requested final target", () => {
  const s = snapshot();
  s.status.details = { ...s.status.details, zones: { "1": {
    thermostat: { state: "ramp", mode: "Tr", tr_c: 24, tj_c: 26, end_value_c: 25, remaining_s: 120 },
    stirrer: { state: "off", rate_rpm: 0 },
  } } };
  render(<EasyMaxTile snapshot={s} />);
  expect((screen.getByLabelText("Target temperature") as HTMLInputElement).value).toBe("");
  expect(screen.getByText(/Setpoint: 25.0/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Review temperature" }) as HTMLButtonElement).disabled).toBe(true);
});
