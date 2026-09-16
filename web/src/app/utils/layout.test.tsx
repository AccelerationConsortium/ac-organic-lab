// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import UtilsLayout from "./layout";

const route = vi.hoisted(() => ({ pathname: "/utils/computers" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));

afterEach(() => { cleanup(); route.pathname = "/utils/computers"; });

describe("UtilsLayout", () => {
  it.each(["/utils/robot_motion", "/utils/robot_motion/"])("omits utility tabs on %s", pathname => {
    route.pathname = pathname;
    render(<UtilsLayout><div>Control Interface</div></UtilsLayout>);
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByText("Control Interface")).toBeTruthy();
  });
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
