// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import UtilsLayout from "./layout";

vi.mock("next/navigation", () => ({
  usePathname: () => "/utils/devices",
}));

afterEach(cleanup);

describe("UtilsLayout", () => {
  it("includes an active Devices utility pill", () => {
    render(<UtilsLayout><div>Devices content</div></UtilsLayout>);

    const link = screen.getByRole("tab", { name: "Devices" });
    expect(link.getAttribute("href")).toBe("/utils/devices");
    expect(link.getAttribute("aria-selected")).toBe("true");
  });

  it("no longer offers an Inventory pill (it is a top-level tab now)", () => {
    render(<UtilsLayout><div>Devices content</div></UtilsLayout>);

    expect(screen.queryByRole("tab", { name: "Inventory" })).toBeNull();
  });
});
