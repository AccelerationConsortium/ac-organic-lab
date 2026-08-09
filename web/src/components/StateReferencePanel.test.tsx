// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StateReferencePanel } from "./StateReferencePanel";

afterEach(cleanup);

/**
 * Regression: the panel is a `fixed` left-edge flyout whose collapsed state is
 * a CSS transform. Transforms move pixels, not layout boxes — so without
 * `pointer-events-none` the wrapper hit-tests over a ~176px invisible column
 * at the left edge and swallows taps on whatever is under it. Invisible on a
 * wide desktop (empty margin), reported live on an iPhone Pro Max in
 * landscape, where that column covers ~19% of the viewport and the left-hand
 * controls stopped responding.
 *
 * These assertions are class-level on purpose: jsdom has no layout engine, so
 * a behavioural "is this tappable" test is not available. The invariant worth
 * pinning is that the overlay never claims pointer events outside the chrome
 * the user can actually see.
 */
describe("StateReferencePanel does not capture taps outside its visible chrome", () => {
  it("wrapper is transparent to pointer events", () => {
    const { container } = render(<StateReferencePanel />);
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("fixed");
    expect(wrapper.className).toContain("pointer-events-none");
  });

  it("collapsed: only the toggle tag is interactive", () => {
    render(<StateReferencePanel />);
    const aside = screen.getByLabelText("Equipment state reference");
    expect(aside.className).toContain("pointer-events-none");

    const toggle = screen.getByRole("button", { name: "Show state reference" });
    expect(toggle.className).toContain("pointer-events-auto");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("open: the panel body becomes interactive", () => {
    render(<StateReferencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Show state reference" }));

    const aside = screen.getByLabelText("Equipment state reference");
    expect(aside.className).toContain("pointer-events-auto");
    // ...and the legend it exists to show is actually rendered.
    expect(screen.getByText("Unreachable")).toBeTruthy();
    expect(screen.getByText("Running")).toBeTruthy();
  });
});
