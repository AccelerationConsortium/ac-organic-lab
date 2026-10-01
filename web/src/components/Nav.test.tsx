// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/use-platforms", () => ({ usePlatforms: () => ({ data: undefined }) }));
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => ({ identity: null }) }));

import { Nav } from "./Nav";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Nav", () => {
  it("places Notebooks immediately before Inventory", () => {
    render(<Nav />);
    const labels = screen.getAllByRole("link").map((a) => a.textContent);
    expect(labels.indexOf("Notebooks")).toBe(labels.indexOf("Inventory") - 1);
  });

  it("opens Bitácora in a popup window", () => {
    const focus = vi.fn();
    const open = vi.spyOn(window, "open").mockReturnValue({ focus } as unknown as Window);
    render(<Nav />);
    const link = screen.getByRole("link", { name: "Notebooks" });
    expect(link.getAttribute("href")).toBe("/bitacora/");
    const notPrevented = fireEvent.click(link);
    expect(open).toHaveBeenCalledWith("/bitacora/", "bitacora", "popup,width=1280,height=900");
    expect(focus).toHaveBeenCalled();
    expect(notPrevented).toBe(false);
  });

  it("falls back to the new-tab link when the popup is blocked", () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    render(<Nav />);
    expect(fireEvent.click(screen.getByRole("link", { name: "Notebooks" }))).toBe(true);
  });
});
