// @vitest-environment jsdom
//
// Push-to-talk wiring. jsdom has no media APIs, so the recorder and mic are
// stubbed; what these tests pin is the POLICY: the button only renders when
// both browser and server can do voice, an Ask-mode transcript auto-sends,
// and a Control-mode transcript only fills the input box (the review gate).
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

let chatBodies: Array<{ mode: string; messages: Array<{ role: string; content: string }> }>;

function installFetch({ voiceConfigured }: { voiceConfigured: boolean }) {
  chatBodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/assistant/voice/health"))
        return Promise.resolve({ ok: true, json: async () => ({ configured: voiceConfigured }) });
      if (url.includes("/api/assistant/voice/transcribe"))
        return Promise.resolve({ ok: true, json: async () => ({ text: "is the shaker running" }) });
      if (url.includes("/api/assistant/health"))
        return Promise.resolve({ ok: true, json: async () => ({ configured: true }) });
      if (url.includes("/api/auth/mine"))
        return Promise.resolve({
          ok: true,
          json: async () => ({ equipment: { torry_pines_shaker: "operator" } }),
        });
      if (url.includes("/api/assistant/chat")) {
        chatBodies.push(JSON.parse(String(init?.body)));
        return Promise.resolve({
          ok: true,
          body: sseBody(['data: {"type":"text","delta":"Yes."}\n\n', 'data: {"type":"done"}\n\n']),
        });
      }
      return Promise.resolve({ ok: false, text: async () => "", json: async () => ({}) });
    })
  );
}

/** MediaRecorder stub: start() records, stop() fires onstop with one chunk. */
class FakeRecorder {
  state = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(public stream: unknown) {}
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["x"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

function installMedia() {
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: vi.fn(() => Promise.resolve({ getTracks: () => [] })),
    },
  });
  // No AudioContext stub: the hook must degrade to manual stop, not crash.
}

const openPanel = async () => {
  render(<AssistantBubble />);
  fireEvent.click(await screen.findByLabelText("Open SDL Assistant"));
};

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("AssistantBubble voice input", () => {
  it("hides the mic when the browser has no recorder", async () => {
    installFetch({ voiceConfigured: true }); // server ready, browser not
    await openPanel();
    await screen.findByPlaceholderText(/Ask about the lab/);
    expect(screen.queryByLabelText("Ask by voice")).toBeNull();
  });

  it("hides the mic when the STT service is not configured", async () => {
    installMedia();
    installFetch({ voiceConfigured: false });
    await openPanel();
    await screen.findByPlaceholderText(/Ask about the lab/);
    expect(screen.queryByLabelText("Ask by voice")).toBeNull();
  });

  it("Ask mode: click, speak, stop — the transcript AUTO-SENDS", async () => {
    installMedia();
    installFetch({ voiceConfigured: true });
    await openPanel();

    const mic = await screen.findByLabelText("Ask by voice");
    fireEvent.click(mic);
    const stopBtn = await screen.findByLabelText("Stop recording");
    fireEvent.click(stopBtn);

    // Transcript became a sent user turn without any Enter press.
    await waitFor(() => expect(chatBodies.length).toBe(1));
    const sent = chatBodies[0];
    expect(sent.mode).toBe("ask");
    expect(sent.messages.at(-1)?.content).toBe("is the shaker running");
    await screen.findByText("Yes.");
  });

  it("Control mode: the transcript fills the box and does NOT send", async () => {
    installMedia();
    installFetch({ voiceConfigured: true });
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);

    const mic = await screen.findByLabelText("Ask by voice");
    fireEvent.click(mic);
    fireEvent.click(await screen.findByLabelText("Stop recording"));

    const box = (await screen.findByPlaceholderText(
      /Ask me to operate a device/
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("is the shaker running"));
    expect(chatBodies.length).toBe(0); // review gate held
  });
});
