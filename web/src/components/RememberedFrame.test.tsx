// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RememberedFrame } from "./RememberedFrame";

const PROPS = {
  storageKey: "eln:test",
  defaultSrc: "/bitacora/",
  scope: "/bitacora/",
  title: "test frame",
};

function frameSrc(): string | null {
  return screen.getByTitle("test frame").getAttribute("src");
}

beforeEach(() => sessionStorage.clear());
afterEach(cleanup);

describe("RememberedFrame", () => {
  it("starts at defaultSrc with nothing saved", () => {
    render(<RememberedFrame {...PROPS} />);
    expect(frameSrc()).toBe("/bitacora/");
  });

  it("resumes at the saved in-scope path", () => {
    sessionStorage.setItem("eln:test", "/bitacora/rooms/42?tab=protocol");
    render(<RememberedFrame {...PROPS} />);
    expect(frameSrc()).toBe("/bitacora/rooms/42?tab=protocol");
  });

  it("ignores a saved path outside the scope", () => {
    // sessionStorage is user-writable; an out-of-scope value must never
    // become the frame target (and Inventory must not resurrect a
    // notebooks path).
    sessionStorage.setItem("eln:test", "https://evil.example/phish");
    render(<RememberedFrame {...PROPS} />);
    expect(frameSrc()).toBe("/bitacora/");

    cleanup();
    sessionStorage.setItem("eln:test", "/bitacora/rooms/7");
    render(
      <RememberedFrame {...PROPS} scope="/bitacora/inventory" defaultSrc="/bitacora/inventory/embed" />,
    );
    expect(frameSrc()).toBe("/bitacora/inventory/embed");
  });
});
