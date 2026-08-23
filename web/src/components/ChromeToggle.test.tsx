// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CHROME_COLLAPSED_CLASS, CHROME_STORAGE_KEY, ChromeToggle } from "./ChromeToggle";

beforeEach(() => {
  document.documentElement.classList.remove(CHROME_COLLAPSED_CLASS);
  localStorage.removeItem(CHROME_STORAGE_KEY);
});
afterEach(cleanup);

describe("ChromeToggle", () => {
  it("collapses and restores the heading via the <html> class, persisting the choice", () => {
    render(<ChromeToggle />);
    const btn = screen.getByRole("button", { name: "Hide heading" });
    expect(btn.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(btn);
    expect(document.documentElement.classList.contains(CHROME_COLLAPSED_CLASS)).toBe(true);
    expect(localStorage.getItem(CHROME_STORAGE_KEY)).toBe("collapsed");
    expect(screen.getByRole("button", { name: "Show heading" }).getAttribute("aria-expanded")).toBe(
      "false",
    );

    fireEvent.click(screen.getByRole("button", { name: "Show heading" }));
    expect(document.documentElement.classList.contains(CHROME_COLLAPSED_CLASS)).toBe(false);
    expect(localStorage.getItem(CHROME_STORAGE_KEY)).toBe("expanded");
  });

  it("picks up a collapse applied before mount (the init script's class)", () => {
    document.documentElement.classList.add(CHROME_COLLAPSED_CLASS);
    render(<ChromeToggle />);
    expect(screen.getByRole("button", { name: "Show heading" })).toBeTruthy();
  });
});
