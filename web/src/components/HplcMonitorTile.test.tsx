// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { HplcMonitorTile } from "./HplcMonitorTile";

function snapshot(): EquipmentSnapshot {
  return {
    id: "lle_hplc", name: "HPLC (Process Chemistry)", kind: "hplc",
    fetched_at: new Date().toISOString(), latency_ms: 12, fetch_error: null,
    adapter: "rest", enabled: true, activity: "unknown", activity_source: "device",
    health: "unknown", mode: "production", simulated: false,
    status: {
      protocol_version: "1.2", equipment_id: "lle_hplc",
      equipment_name: "HPLC (Process Chemistry)", equipment_kind: "hplc",
      equipment_status: "unknown", activity: "unknown",
      message: "Exact instrument readiness is not available from passive probes.",
      device_time: new Date().toISOString(), required_actions: [], allowed_actions: [],
      last_error: null,
      components: {
        chemstation_acquisition: { connected: true, state: "running" },
        chemstation_analysis: { connected: true, state: "running" },
        data_path: { connected: true, state: "available" },
        data_service: { connected: false, state: "stopped" },
        native_status_snapshot: { connected: false, state: "unavailable" },
      },
      metrics: { last_result_age: { value: 120, unit: "s" } },
      details: {
        monitoring_only: true, queue_available: false, queue_length: null,
        current_run: null, file_activity_inferred: false,
        last_result: { name: "example-result.D", age_s: 120 },
      },
    },
  };
}

function nativeSnapshot(): EquipmentSnapshot {
  const value = snapshot();
  value.status.components!.native_status_snapshot = { connected: true, state: "fresh" };
  value.status.details = {
    ...value.status.details,
    native_readback: { available: true, acquisition_state: "NOTREADY" },
    queue_available: true, queue_length: 0,
  };
  return value;
}

afterEach(cleanup);

describe("HplcMonitorTile", () => {
  it("uses the shared HTE shell and compact sections without offering controls", () => {
    const { container } = render(<HplcMonitorTile snapshot={snapshot()} />);
    expect(container.querySelector("article")?.className).toContain("rounded-xl");
    for (const title of ["Instrument", "ChemStation software", "Run queue", "Latest result"]) {
      expect(screen.getByRole("region", { name: title })).toBeTruthy();
    }
    expect(screen.getByText("Read-only monitoring")).toBeTruthy();
    expect(screen.getByText("example-result.D")).toBeTruthy();
    expect(screen.getByText(/File updated: 2m ago/)).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });

  it("does not confuse running software or a stopped supporting service with instrument readiness", () => {
    render(<HplcMonitorTile snapshot={snapshot()} />);
    const instrument = within(screen.getByRole("region", { name: "Instrument" }));
    expect(instrument.getByText("Unknown")).toBeTruthy();
    expect(instrument.getByText("Not observed")).toBeTruthy();
    expect(screen.queryByText("Ready")).toBeNull();
    const software = within(screen.getByRole("region", { name: "ChemStation software" }));
    expect(software.getAllByText("Running")).toHaveLength(2);
    const stopped = software.getByText("Stopped").parentElement!;
    expect(stopped.className).toContain("bg-slate-100");
    expect(stopped.className).not.toContain("amber");
    expect(stopped.className).not.toContain("rose");
  });

  it("shows zero pending only when the queue was actually observed", () => {
    const value = nativeSnapshot();
    const { rerender } = render(<HplcMonitorTile snapshot={value} />);
    expect(within(screen.getByRole("region", { name: "Run queue" })).getByText("0")).toBeTruthy();
    value.status.details!.queue_available = false;
    rerender(<HplcMonitorTile snapshot={value} />);
    expect(within(screen.getByRole("region", { name: "Run queue" })).queryByText("0")).toBeNull();
  });

  it.each([null, -1, 1.5, "0", Number.NaN, Number.POSITIVE_INFINITY])("does not render invalid queue length %s as observed", (length) => {
    const value = nativeSnapshot();
    value.status.details!.queue_length = length;
    render(<HplcMonitorTile snapshot={value} />);
    expect(within(screen.getByRole("region", { name: "Run queue" })).getAllByText("Not observed")).toHaveLength(2);
  });

  it("can display a native reading and observed run without inventing readiness", () => {
    const value = nativeSnapshot();
    value.status.details!.current_run = { title: "Example acquisition" };
    render(<HplcMonitorTile snapshot={value} />);
    expect(screen.getByText("NOTREADY")).toBeTruthy();
    expect(screen.getByText("Example acquisition")).toBeTruthy();
    expect(screen.queryByText("Ready")).toBeNull();
  });

  it("suppresses native readings if their source explicitly becomes unavailable", () => {
    const value = nativeSnapshot();
    value.status.details!.native_readback = { available: false, acquisition_state: "READY" };
    value.status.metrics!.instrument_state = { value: "READY" };
    value.status.details!.current_run = { title: "Old acquisition" };
    render(<HplcMonitorTile snapshot={value} />);
    expect(screen.queryByText("READY")).toBeNull();
    expect(screen.queryByText("Old acquisition")).toBeNull();
    expect(within(screen.getByRole("region", { name: "Run queue" })).queryByText("0")).toBeNull();
  });

  it("labels file-derived activity as inferred", () => {
    const value = snapshot();
    value.status.activity = "running";
    value.status.equipment_status = "busy";
    value.status.details!.file_activity_inferred = true;
    render(<HplcMonitorTile snapshot={value} />);
    expect(screen.getByText("Running · inferred").parentElement?.className).toContain("amber");
    expect(screen.getByTitle(/Inferred from recent result-file writes/)).toBeTruthy();
  });

  it("removes cached positive observations after a failed poll", () => {
    const value = nativeSnapshot();
    value.status.equipment_status = "ready";
    value.status.activity = "idle";
    value.status.details!.current_run = { title: "Old acquisition" };
    value.fetch_error = { kind: "timeout", message: "timed out" } as EquipmentSnapshot["fetch_error"];
    render(<HplcMonitorTile snapshot={value} />);
    expect(screen.getByText("Aggregator could not reach device")).toBeTruthy();
    for (const text of ["Ready", "Idle", "Running", "NOTREADY", "example-result.D", "Old acquisition"]) {
      expect(screen.queryByText(text)).toBeNull();
    }
    expect(screen.getByText("Status unavailable until the monitor reconnects.")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("handles missing and malformed optional fields and strips result directory paths", () => {
    const value = snapshot();
    value.status.details = { current_run: ["invalid"], last_result: { name: "C:\\Data\\example-result.D", age_s: -1 } };
    value.status.metrics = {};
    value.status.components = {};
    const { rerender } = render(<HplcMonitorTile snapshot={value} />);
    expect(screen.getByText("example-result.D")).toBeTruthy();
    expect(screen.queryByText(/C:\\Data/)).toBeNull();
    expect(screen.getByText(/File updated: Unknown/)).toBeTruthy();
    value.status.details = undefined;
    rerender(<HplcMonitorTile snapshot={value} />);
    expect(screen.queryByText("example-result.D")).toBeNull();
  });

  it("only enables layout stacking on small screens without fixed-height clipping", () => {
    render(<HplcMonitorTile snapshot={snapshot()} />);
    const grid = screen.getByRole("region", { name: "ChemStation software" }).querySelector("div")!;
    expect(grid.className).toContain("grid-cols-1");
    expect(grid.className).toContain("sm:grid-cols-2");
  });
});
