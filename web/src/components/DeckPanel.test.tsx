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

  it("page variant labels an occupied cell with its slot number and labware name", () => {
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
    expect(cell.textContent).toContain("3");
  });

  it("marks a declared slot with the orange outline and explains it in one legend", () => {
    // Declared is the one slot state carried by colour alone, so the legend is
    // load-bearing rather than decorative: without it the outline is unnamed.
    const deck = deckWith({
      "3": labwareSlot("declared", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
      "6": labwareSlot("in_use", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
    });
    const { container } = render(
      <DeckPanel deviceDeck={deck} selectedSlot={null} onSelectSlot={() => {}} variant="page" />,
    );
    const declared = screen.getByTitle("Slot 3 — corning_96_wellplate_360ul_flat (declared)");
    expect(declared.querySelector(".border-orange-400")).not.toBeNull();

    // An observed slot must not borrow the outline, or it says nothing.
    const inUse = screen.getByTitle("Slot 6 — corning_96_wellplate_360ul_flat (in use)");
    expect(inUse.querySelector(".border-orange-400")).toBeNull();

    expect(container.textContent).toContain("declared");
  });

  it("omits the declared legend on the compact tile, which never draws the outline", () => {
    const deck = deckWith({
      "3": labwareSlot("declared", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
    });
    const { container } = render(<DeckPanel deviceDeck={deck} variant="tile" />);
    expect(container.querySelector(".border-orange-400")).toBeNull();
    expect(container.textContent).not.toContain("Orange outline");
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

describe("DeckPanel tip-state rendering", () => {
  const COLUMN_1 = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"];

  function rackDeck() {
    return deckWith({
      "5": labwareSlot("occupied", {
        kind: "tiprack",
        load_name: "opentrons_96_tiprack_20ul",
        is_tiprack: true,
        rows: 8,
        columns: 12,
        nickname: "tips_20",
      }),
    });
  }

  function summary(tips: Record<string, string>) {
    return [
      {
        slot: "5",
        total: 96,
        available: 96 - Object.keys(tips).length,
        empty: Object.values(tips).filter((s) => s === "empty").length,
        touched: Object.values(tips).filter((s) => s !== "empty").length,
        tips,
      },
    ];
  }

  /** The dots inside slot 5's mini grid, in row-major render order. */
  function wellDots(container: HTMLElement): Element[] {
    const cell = container.querySelector('[title^="Slot 5"]')!;
    return Array.from(cell.querySelectorAll("span.rounded-full"));
  }

  it("greys the wells an 8-channel pick emptied, and tints a used tip", () => {
    const tips = Object.fromEntries(COLUMN_1.map((w) => [w, "empty"]));
    const { container } = render(
      <DeckPanel deviceDeck={rackDeck()} tipRacks={summary({ ...tips, H2: "plate_D_B2" })} />,
    );
    const dots = wellDots(container);
    expect(dots).toHaveLength(96);
    // Row-major: index = row * columns + column. Column 1 is index r*12.
    for (let r = 0; r < 8; r++) {
      expect(dots[r * 12].className).toContain("bg-slate-300"); // emptied
    }
    expect(dots[7 * 12 + 1].className).toContain("bg-amber-300"); // H2, used
    // Green is the "a tip is there and unused" signal.
    expect(dots[3].className).toContain("bg-emerald-400"); // A4, still full
  });

  it("draws an untracked rack uniformly rather than claiming it is full", () => {
    // No summary for this rack: the thumbnail has no honest way to say
    // "unknown" at 2 px, so it says nothing — the inspector carries the truth.
    const { container } = render(<DeckPanel deviceDeck={rackDeck()} tipRacks={[]} />);
    const dots = wellDots(container);
    expect(dots.every((d) => d.className.includes("bg-slate-300"))).toBe(true);
    // The load-bearing part: no green anywhere. Green means "known available",
    // so an unregistered rack must never show it.
    expect(dots.some((d) => d.className.includes("emerald"))).toBe(false);
  });

  it("leaves a plate alone (tip state is a tip-rack concept)", () => {
    const deck = deckWith({
      "5": labwareSlot("occupied", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
        nickname: "plate_D",
      }),
    });
    const { container } = render(<DeckPanel deviceDeck={deck} tipRacks={summary({ A1: "empty" })} />);
    const dots = wellDots(container);
    expect(dots.every((d) => d.className.includes("bg-slate-300"))).toBe(true);
    expect(dots.some((d) => d.className.includes("emerald"))).toBe(false);
  });
});
