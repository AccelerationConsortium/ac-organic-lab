// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ManualCard, type ManualRequest } from "./manual-card";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const item: ManualRequest = {
  request_id: "request-1", step_id: "carry", state: "waiting", since: "2026-09-30",
  manual: { title: "Load reader", instructions: "Move PLT-1 to the reader nest", confirmation_text: "Plate is seated", access_roles: ["reader"] },
  custody: { hid: "PLT-1", from: "bench/source", to: "reader/nest" }, decision: null,
};
function mount(request = item) {
  const changed = vi.fn();
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ManualCard runId="run-1" item={request} onChanged={changed} /></QueryClientProvider>);
  return changed;
}

describe("human action card", () => {
  it("requires explicit confirmation and sends the exact request once", async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ state: "acknowledged" }), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const changed = mount();
    const button = screen.getByRole("button", { name: "Confirm completed" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Plate is seated"));
    fireEvent.click(button);
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string)).toEqual({ request_id: "request-1", outcome: "done", note: "" });
    expect(button.disabled).toBe(true);
  });
  it("retries the recorded decision without asking for the physical move again", async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    mount({ ...item, state: "uncertain", decision: { by: "chemist", outcome: "done", note: "seated", at: "now" } });
    expect(screen.queryByRole("button", { name: "Confirm completed" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Do not repeat the physical move");
    fireEvent.click(screen.getByRole("button", { name: "Retry recording" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string)).toEqual({ request_id: "request-1", outcome: "done", note: "seated" });
  });
  it("reports failure without completion confirmation and exposes rejected responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) => new Response("Project membership required", { status: 403 })));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Report failure" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Acknowledgment was not confirmed"));
  });
});
