// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deckWith,
  labwareSlot,
  mismatchSlot,
  moduleSlot,
} from "@/lib/ot2-deck-test-helpers";

import { DeckPanel } from "./DeckPanel";

afterEach(cleanup);

describe("DeckPanel slot selection", () => {
  it("reports a click on a slot and toggles it off on re-click", () => {
    const onSelectSlot = vi.fn();
    const deck = deckWith({});
    const { rerender } = render(
      <DeckPanel deviceDeck={deck} selectedSlot={null} onSelectSlot={onSelectSlot} />,
    );

    fireEvent.click(screen.getByTitle("Slot 5 — empty"));
    expect(onSelectSlot).toHaveBeenLastCalledWith(5);

    rerender(<DeckPanel deviceDeck={deck} selectedSlot={5} onSelectSlot={onSelectSlot} />);
    fireEvent.click(screen.getByTitle("Slot 5 — empty"));
    expect(onSelectSlot).toHaveBeenLastCalledWith(null);
  });

  it("renders all 12 slots, top row first (10 11 12 … 1 2 3)", () => {
    render(<DeckPanel deviceDeck={deckWith({})} selectedSlot={null} onSelectSlot={() => {}} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(12);
    expect(buttons[0].title).toBe("Slot 10 — empty");
    expect(buttons[2].title).toBe("Slot 12 — empty");
    expect(buttons[11].title).toBe("Slot 3 — empty");
  });
});

describe("DeckPanel read-only mode", () => {
  it("renders no buttons when onSelectSlot is omitted (read-only tile)", () => {
    render(<DeckPanel deviceDeck={deckWith({})} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    // Cells still render with their tooltips.
    expect(screen.getByTitle("Slot 5 — empty")).toBeTruthy();
  });
});

describe("DeckPanel declared vs observed rendering", () => {
  it("flags a mismatch slot with the ≠ badge and declared/observed tooltip", () => {
    const deck = deckWith({
      "2": mismatchSlot(
        { kind: "tiprack", load_name: "opentrons_96_tiprack_300ul" },
        { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" },
      ),
    });
    render(<DeckPanel deviceDeck={deck} selectedSlot={null} onSelectSlot={() => {}} />);
    const cell = screen.getByTitle(
      "Slot 2 — declared opentrons_96_tiprack_300ul, observed corning_96_wellplate_360ul_flat",
    );
    expect(cell.textContent).toContain("≠");
  });

  it("badges observed in-use labware as busy", () => {
    const deck = deckWith({
      "1": labwareSlot("in_use", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
    });
    render(<DeckPanel deviceDeck={deck} selectedSlot={null} onSelectSlot={() => {}} />);
    expect(
      screen.getByTitle("Slot 1 — corning_96_wellplate_360ul_flat (in use)").textContent,
    ).toContain("busy");
  });

  it("page variant labels occupied cells with the labware name and a declared badge", () => {
    const deck = deckWith({
      "3": labwareSlot("declared", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
    });
    render(
      <DeckPanel deviceDeck={deck} selectedSlot={null} onSelectSlot={() => {}} variant="page" />,
    );
    const cell = screen.getByTitle("Slot 3 — corning_96_wellplate_360ul_flat (declared)");
    expect(cell.textContent).toContain("corning_96_wellplate_360ul_flat");
    expect(cell.textContent).toContain("declared");
  });

  it("renders a declared temperature module with its overhang readout cell", () => {
    const deck = deckWith({ "11": moduleSlot("declared", "temperature module gen2") });
    render(
      <DeckPanel
        deviceDeck={deck}
        robotModules={[
          {
            model: "temperatureModuleV2",
            type: "temperatureModuleType",
            status: "heating",
            current_temperature: 37,
            target_temperature: 40,
          },
        ]}
        selectedSlot={null}
        onSelectSlot={() => {}}
      />,
    );
    const overhang = screen.getByTitle(
      "Slot 10 — overhang of the temperature module gen2 at slot 11",
    );
    expect(overhang.textContent).toContain("37 °C");
    expect(overhang.textContent).toContain("→ 40 °C");
  });
});
