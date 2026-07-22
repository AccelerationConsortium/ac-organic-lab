// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import UtilsLayout from "./layout";

vi.mock("next/navigation", () => ({
  usePathname: () => "/utils/bambu_printer",
}));

afterEach(cleanup);

describe("UtilsLayout", () => {
  it("includes an active Bambu Printer utility pill", () => {
    render(<UtilsLayout><div>Printer content</div></UtilsLayout>);

    const link = screen.getByRole("tab", { name: "Bambu Printer" });
    expect(link.getAttribute("href")).toBe("/utils/bambu_printer");
    expect(link.getAttribute("aria-selected")).toBe("true");
  });
});
