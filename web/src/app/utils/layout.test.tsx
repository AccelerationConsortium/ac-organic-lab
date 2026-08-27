// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import UtilsLayout from "./layout";

vi.mock("next/navigation", () => ({
  usePathname: () => "/utils/computers",
}));

afterEach(cleanup);

describe("UtilsLayout", () => {
  it("offers Computers and Servers and 3D Printers as separate pills", () => {
    render(<UtilsLayout><div>Computers content</div></UtilsLayout>);

    const computers = screen.getByRole("tab", { name: "Computers and Servers" });
    expect(computers.getAttribute("href")).toBe("/utils/computers");
    expect(computers.getAttribute("aria-selected")).toBe("true");

    const printers = screen.getByRole("tab", { name: "3D Printers" });
    expect(printers.getAttribute("href")).toBe("/utils/printers");
    expect(printers.getAttribute("aria-selected")).toBe("false");
  });

  it("no longer offers the combined Devices pill", () => {
    render(<UtilsLayout><div>Computers content</div></UtilsLayout>);

    expect(screen.queryByRole("tab", { name: "Devices" })).toBeNull();
  });

  it("no longer offers an Inventory pill (it is a top-level tab now)", () => {
    render(<UtilsLayout><div>Computers content</div></UtilsLayout>);

    expect(screen.queryByRole("tab", { name: "Inventory" })).toBeNull();
  });
});
