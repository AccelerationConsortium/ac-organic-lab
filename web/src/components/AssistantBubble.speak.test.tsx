// @vitest-environment jsdom
//
// Wiring tests for the read-aloud toggle. The *text shaping* is tested in
// src/lib/speech.test.ts; what matters here is that the toggle only appears
// where the browser can speak, that the preference persists, and — most
// importantly — that nothing is ever spoken while the toggle is off.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantBubble } from "./AssistantBubble";

const auth = {
  authenticated: true,
  identity: { email: "alice@example.edu", role: "operator" } as {
    email: string;
    role: string;
  } | null,
};
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));
vi.mock("@/lib/api", () => ({
  authorizeAssistantAction: vi.fn(() => Promise.resolve({ ok: true })),
  approveAssistantPlan: vi.fn(() => Promise.resolve({ approved: true })),
  finishAssistantPlan: vi.fn(() => Promise.resolve({ ok: true })),
  ApiError: class ApiError extends Error {
    status = 400;
  },
}));

function sseBody(frames: string[]) {
  const bytes = new TextEncoder().encode(frames.join(""));
  let sent = false;
  return {
    getReader: () => ({
      read: () =>
        sent
          ? Promise.resolve({ value: undefined, done: true })
          : ((sent = true), Promise.resolve({ value: bytes, done: false })),
    }),
  };
}

function installFetch(frames: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/assistant/health"))
        return Promise.resolve({ ok: true, json: async () => ({ configured: true }) });
      if (url.includes("/api/auth/mine"))
        return Promise.resolve({ ok: true, json: async () => ({ equipment: {} }) });
      if (url.includes("/api/assistant/chat"))
        return Promise.resolve({ ok: true, body: sseBody(frames) });
      return Promise.resolve({ ok: false, text: async () => "", json: async () => ({}) });
    })
  );
}

/** Minimal stand-in for the speechSynthesis API jsdom does not implement. */
function installSpeech() {
  const speak = vi.fn();
  const cancel = vi.fn();
  vi.stubGlobal("speechSynthesis", { speak, cancel, speaking: false });
  // The component constructs one of these; jsdom has no implementation.
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      text: string;
      constructor(text: string) {
        this.text = text;
      }
    }
  );
  return { speak, cancel };
}

const openPanel = async () => {
  render(<AssistantBubble />);
  fireEvent.click(await screen.findByLabelText("Open SDL Assistant"));
};

async function ask(question: string) {
  const box = await screen.findByPlaceholderText(/Ask about the lab/);
  fireEvent.change(box, { target: { value: question } });
  fireEvent.submit(box.closest("form")!);
}

const toggle = () => screen.findByRole("button", { name: /aloud/ });

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("AssistantBubble read-aloud", () => {
  it("hides the toggle when the browser cannot speak", async () => {
    installFetch([]);
    await openPanel();
    await screen.findByPlaceholderText(/Ask about the lab/);
    expect(screen.queryByRole("button", { name: /aloud/ })).toBeNull();
  });

  it("offers the toggle, off by default, when speech is available", async () => {
    installSpeech();
    installFetch([]);
    await openPanel();
    const btn = await toggle();
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  // The load-bearing one: an unprompted talking dashboard is the failure mode.
  it("stays silent while the toggle is off", async () => {
    const { speak } = installSpeech();
    installFetch([
      'data: {"type":"text","delta":"The press is ready."}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    await ask("how is the press");
    await waitFor(() => expect(screen.getByText(/The press is ready./)).toBeTruthy());
    expect(speak).not.toHaveBeenCalled();
  });

  it("speaks a shaped summary once the turn completes, when on", async () => {
    const { speak } = installSpeech();
    installFetch([
      'data: {"type":"text","delta":"The press is ready. "}\n\n',
      'data: {"type":"text","delta":"```\\nlots of code\\n```"}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    fireEvent.click(await toggle());
    await ask("how is the press");

    await waitFor(() => expect(speak).toHaveBeenCalledTimes(1));
    const spoken = speak.mock.calls[0][0].text as string;
    expect(spoken).toContain("The press is ready.");
    expect(spoken).not.toContain("lots of code"); // never reads code aloud
  });

  it("persists the preference across a remount", async () => {
    installSpeech();
    installFetch([]);
    await openPanel();
    fireEvent.click(await toggle());
    await waitFor(() => expect(localStorage.getItem("ac-assistant-speak-v1")).toBe("1"));

    cleanup();
    await openPanel();
    await waitFor(async () =>
      expect((await toggle()).getAttribute("aria-pressed")).toBe("true")
    );
  });

  it("stops speaking when switched off, and on Escape", async () => {
    const { cancel } = installSpeech();
    installFetch([]);
    await openPanel();
    const btn = await toggle();
    fireEvent.click(btn); // on
    cancel.mockClear();
    fireEvent.click(await toggle()); // off -> must cut any live utterance
    await waitFor(() => expect(cancel).toHaveBeenCalled());

    cancel.mockClear();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(cancel).toHaveBeenCalled());
  });
});
