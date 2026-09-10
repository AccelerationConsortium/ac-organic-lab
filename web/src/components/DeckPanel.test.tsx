// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deckWith,
  emptySlot,
  labwareSlot as baseLabwareSlot,
  mismatchSlot,
  moduleSlot,
} from "@/lib/ot2-deck-test-helpers";

import { buildDefinition, defaultSpec } from "@/lib/labware-schema";
import { DeckPanel } from "./DeckPanel";

function labwareSlot(...args: Parameters<typeof baseLabwareSlot>) {
  const slot = baseLabwareSlot(...args);
  slot.labware!.definition = buildDefinition({ ...defaultSpec(), loadName: args[1].load_name,
    displayCategory: args[1].is_tiprack ? "tipRack" : "wellPlate", tipLength: 50 });
  return slot;
}
vi.mock("@/lib/api", async importOriginal => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, getStandardLabwareDefinition: vi.fn(async () => ({ definition: {} })) };
});

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

  it("renders and selects the running Flex deck's 16 alphabetic slots", () => {
    const onSelectSlot = vi.fn();
    const deck = deckWith({ A1: emptySlot() });
    render(
      <DeckPanel deviceDeck={deck} selectedSlot={null} onSelectSlot={onSelectSlot} />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(16);
    expect(buttons[0].title).toBe("Slot A1 — empty");
    expect(buttons[15].title).toBe("Slot D4 — empty");
    fireEvent.click(buttons[5]);
    expect(onSelectSlot).toHaveBeenLastCalledWith("B2");
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

  it("names the labware under each slot on the compact tile, not just on the page", () => {
    const deck = deckWith({
      "3": labwareSlot("observed", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
    });
    const { container } = render(<DeckPanel deviceDeck={deck} variant="tile" />);
    // The tile used to render the bare box, so "what is in slot 3" needed the
    // gateway panel. The label row is the page treatment, adopted here.
    expect(container.textContent).toContain("corning_96_wellplate_360ul_flat");
  });

  it("lays the tile deck out in responsive columns rather than a fixed-width strip", () => {
    const { container } = render(<DeckPanel deviceDeck={deckWith({})} variant="tile" />);
    const grid = container.querySelector<HTMLElement>(".grid");
    // A fixed `repeat(n, 160px)` track overflowed the tile and scrolled
    // sideways once the cells became deck-proportioned.
    expect(grid?.style.gridTemplateColumns).toBe("repeat(3, minmax(0, 1fr))");
    expect(grid?.className).not.toContain("overflow-x-auto");
  });

  it("keeps a temperature module and readout in its assigned slot", () => {
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
      "Slot 11 — temperature module gen2 (declared)",
    );
    expect(screen.getByTitle("Slot 10 — empty")).toBeTruthy();
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
    return Array.from(cell.querySelectorAll("[data-well]")).sort((a, b) => {
      const x = a.getAttribute("data-well")!, y = b.getAttribute("data-well")!;
      return x.charCodeAt(0) - y.charCodeAt(0) || Number(x.slice(1)) - Number(y.slice(1));
    });
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
      expect(dots[r * 12].getAttribute("class")).toContain("fill-slate-300"); // emptied
    }
    expect(dots[7 * 12 + 1].getAttribute("class")).toContain("fill-amber-300"); // H2, used
    // Green is the "a tip is there and unused" signal.
    expect(dots[3].getAttribute("class")).toContain("fill-emerald-400"); // A4, still full
  });

  it("draws an untracked rack uniformly rather than claiming it is full", () => {
    // No summary for this rack: the thumbnail has no honest way to say
    // "unknown" at 2 px, so it says nothing — the inspector carries the truth.
    const { container } = render(<DeckPanel deviceDeck={rackDeck()} tipRacks={[]} />);
    const dots = wellDots(container);
    expect(dots.every((d) => (d.getAttribute("class") ?? "").includes("fill-slate-300"))).toBe(true);
    // The load-bearing part: no green anywhere. Green means "known available",
    // so an unregistered rack must never show it.
    expect(dots.some((d) => (d.getAttribute("class") ?? "").includes("emerald"))).toBe(false);
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
    expect(dots.every((d) => (d.getAttribute("class") ?? "").includes("fill-slate-300"))).toBe(true);
    expect(dots.some((d) => (d.getAttribute("class") ?? "").includes("emerald"))).toBe(false);
  });
});

it("uses exact mixed well sizes, rectangular shapes and a padding-free viewBox in the overview", () => {
  const slot = labwareSlot("declared", { kind: "unknown", load_name: "mixed_test" });
  slot.labware!.definition = {
    dimensions: { xDimension: 127.75, yDimension: 85.5, zDimension: 20 },
    ordering: [["A1", "B1"], ["A2"]],
    wells: {
      A1: { x: 15, y: 70, z: 2, depth: 18, shape: "circular", diameter: 14.7 },
      B1: { x: 15, y: 30, z: 2, depth: 18, shape: "circular", diameter: 27.81 },
      A2: { x: 70, y: 60, z: 2, depth: 18, shape: "rectangular", xDimension: 12, yDimension: 8 },
    },
  };
  const { container } = render(<DeckPanel deviceDeck={deckWith({ "2": slot })} />);
  expect(container.querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 127.75 85.5");
  expect(container.querySelectorAll("[data-well]")).toHaveLength(3);
  expect(container.querySelector('[data-well="A1"]')?.getAttribute("rx")).toBe("7.35");
  expect(container.querySelector('[data-well="B1"]')?.getAttribute("rx")).toBe("13.905");
  const rect = container.querySelector('[data-well="A2"]')!;
  expect(rect.tagName).toBe("rect");
  expect(rect.getAttribute("x")).toBe("64");
  expect(rect.getAttribute("y")).toBe("21.5");
  expect(rect.getAttribute("width")).toBe("12");
  const cell = screen.getByTitle("Slot 2 — mixed_test (declared)");
  expect(cell.querySelector(".absolute.left-0\\.5")?.textContent).toBe("2");
});

it("reserves the thermocycler footprint without inventing an adjacent temperature-module slot", () => {
  render(<DeckPanel deviceDeck={deckWith({ "7": moduleSlot("declared", "thermocycler module gen2") })} />);
  expect(screen.getByTitle("Slot 8 — occupied by the thermocycler module gen2 anchored at slot 7")).toBeTruthy();
  expect(screen.getByTitle("Slot 10 — occupied by the thermocycler module gen2 anchored at slot 7")).toBeTruthy();
  expect(screen.getByTitle("Slot 11 — occupied by the thermocycler module gen2 anchored at slot 7")).toBeTruthy();
});
