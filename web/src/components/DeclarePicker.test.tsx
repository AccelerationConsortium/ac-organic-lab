// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeclarePicker } from "./Ot2ControlPanel";

afterEach(cleanup);

function renderPicker(props: Partial<Parameters<typeof DeclarePicker>[0]> = {}) {
  const onDeclare = vi.fn();
  render(
    <DeclarePicker
      selectedSlot={3}
      currentDeclare={null}
      locked={false}
      noAccess={false}
      onDeclare={onDeclare}
      {...props}
    />,
  );
  return { onDeclare };
}

describe("DeclarePicker — exact load-name declaration", () => {
  it("declares the exact Opentrons load_name of the picked entry", () => {
    const { onDeclare } = renderPicker();
    fireEvent.click(screen.getByText("Corning 96-well plate, 360 µL flat"));
    expect(onDeclare).toHaveBeenCalledTimes(1);
    expect(onDeclare.mock.calls[0][0]?.declare).toBe("corning_96_wellplate_360ul_flat");
  });

  it("shows the exact declare string alongside each entry", () => {
    renderPicker();
    expect(screen.getByText("opentrons_96_tiprack_300ul")).toBeTruthy();
    expect(screen.getByText("agilent_1_reservoir_290ml")).toBeTruthy();
  });

  it("offers the gateway module keys and declares them verbatim", () => {
    const { onDeclare } = renderPicker();
    fireEvent.click(screen.getByText("Temperature module (GEN2)"));
    expect(onDeclare.mock.calls[0][0]?.declare).toBe("temperature_module");
  });

  it("filters the catalog by search query", () => {
    renderPicker();
    fireEvent.change(screen.getByLabelText("Search the labware catalog"), {
      target: { value: "reservoir" },
    });
    expect(screen.getByText("Agilent 1-well reservoir, 290 mL")).toBeTruthy();
    expect(screen.queryByText("Temperature module (GEN2)")).toBeNull();
  });

  it("clears the slot via the Clear button", () => {
    const { onDeclare } = renderPicker({ currentDeclare: "corning_96_wellplate_360ul_flat" });
    fireEvent.click(screen.getByText("Clear slot"));
    expect(onDeclare).toHaveBeenCalledWith(null);
  });
});

describe("DeclarePicker — custom labware", () => {
  it("shows lab-store definitions in a Custom group and declares their exact load_name", () => {
    const onDeclare = vi.fn();
    render(
      <DeclarePicker
        selectedSlot={3}
        currentDeclare={null}
        locked={false}
        noAccess={false}
        onDeclare={onDeclare}
        customEntries={[
          {
            key: "labware-store-matterlab_54_vialplate_2ml",
            label: "MatterLab 54 vial plate 2 mL",
            category: "custom",
            declare: "matterlab_54_vialplate_2ml",
          },
        ]}
      />,
    );
    expect(screen.getByText("Custom (lab store)")).toBeTruthy();
    fireEvent.click(screen.getByText("MatterLab 54 vial plate 2 mL"));
    expect(onDeclare.mock.calls[0][0]?.declare).toBe("matterlab_54_vialplate_2ml");
  });

  it("declares a free-text load_name only when it is a valid load_name", () => {
    const { onDeclare } = renderPicker();
    const input = screen.getByLabelText("Declare a custom load name");
    const button = screen.getByText("Declare custom").closest("button")!;

    // No underscore → parsed as a legacy kind by the gateway → refused here.
    fireEvent.change(input, { target: { value: "vialplate" } });
    expect(button.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "matterlab_54_vialplate_2ml" } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(onDeclare).toHaveBeenCalledTimes(1);
    expect(onDeclare.mock.calls[0][0]?.declare).toBe("matterlab_54_vialplate_2ml");
  });

  it("disables free-text declare when locked", () => {
    renderPicker({ locked: true });
    const input = screen.getByLabelText("Declare a custom load name") as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });
});

describe("DeclarePicker — authorization gating", () => {
  it("disables every entry and explains when the user is signed out", () => {
    const { onDeclare } = renderPicker({ locked: true });
    expect(screen.getByText("Sign in to declare deck intent.")).toBeTruthy();
    const entry = screen.getByText("Corning 96-well plate, 360 µL flat").closest("button");
    expect(entry?.disabled).toBe(true);
    fireEvent.click(entry!);
    expect(onDeclare).not.toHaveBeenCalled();
  });

  it("says 'no access' for a signed-in user without a role on this device", () => {
    renderPicker({ locked: true, noAccess: true });
    expect(
      screen.getByText(
        "No access — only authorized users of this device can change its declared deck.",
      ),
    ).toBeTruthy();
  });

  it("disables entries until a slot is selected", () => {
    renderPicker({ selectedSlot: null });
    expect(screen.getByText("Select a deck slot first")).toBeTruthy();
    const entry = screen.getByText("Waste bin").closest("button");
    expect(entry?.disabled).toBe(true);
  });
});
