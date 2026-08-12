// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantBubble } from "./AssistantBubble";
import { authorizeAssistantAction } from "@/lib/api";

// --- mocks ------------------------------------------------------------------

const auth = {
  authenticated: true,
  identity: { email: "alice@example.edu", role: "operator" } as {
    email: string;
    role: string;
  } | null,
};
vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => auth,
}));

vi.mock("@/lib/api", () => ({
  authorizeAssistantAction: vi.fn(() => Promise.resolve({ ok: true })),
  ApiError: class ApiError extends Error {
    status = 400;
  },
}));

const PROPOSAL = {
  type: "proposal",
  proposal: {
    equipment_id: "xarm",
    equipment_name: "UFactory xArm5",
    kind: "robot_arm",
    action: "move.plateloc_out",
    passthrough_action: "graph/move_to",
    args: { node_id: "plateloc_out" },
    reason: "stage the plate",
    actor: "alice@example.edu",
    expires_in_s: 120,
    device_state: { equipment_status: "ready", activity: "idle", message: "ok" },
  },
};

/** A fake streaming Response body that yields the given SSE frames once. */
function sseBody(frames: string[]) {
  const bytes = new TextEncoder().encode(frames.join(""));
  let sent = false;
  return {
    getReader() {
      return {
        read() {
          if (sent) return Promise.resolve({ value: undefined, done: true });
          sent = true;
          return Promise.resolve({ value: bytes, done: false });
        },
      };
    },
  };
}

let mineEquipment: Record<string, string | null>;

function installFetch(chatFrames: string[]) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/assistant/health")) {
      return Promise.resolve({ ok: true, json: async () => ({ configured: true }) });
    }
    if (url.includes("/api/auth/mine")) {
      return Promise.resolve({ ok: true, json: async () => ({ equipment: mineEquipment }) });
    }
    if (url.includes("/api/assistant/chat")) {
      expect(init?.method).toBe("POST");
      return Promise.resolve({ ok: true, body: sseBody(chatFrames) });
    }
    return Promise.resolve({ ok: false, text: async () => "unexpected", json: async () => ({}) });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  auth.authenticated = true;
  auth.identity = { email: "alice@example.edu", role: "operator" };
  mineEquipment = { xarm: "user" };
  (authorizeAssistantAction as unknown as ReturnType<typeof vi.fn>).mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function openPanel() {
  render(<AssistantBubble />);
  // Launcher only appears once the health check resolves configured:true.
  const launcher = await screen.findByLabelText("Open lab assistant");
  fireEvent.click(launcher);
}

describe("AssistantBubble control mode", () => {
  it("defaults to Ask and offers a Control toggle when eligible", async () => {
    installFetch([]);
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    // Read-only subtitle until switched.
    expect(screen.getByText(/Read-only/)).toBeTruthy();
  });

  it("disables the Control toggle for a viewer with no equipment roles", async () => {
    auth.identity = { email: "viewer@example.edu", role: "none" };
    mineEquipment = {};
    installFetch([]);
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(true));
  });

  it("renders a confirm card from a proposal frame and authorizes via the passthrough", async () => {
    installFetch([
      'data: {"type":"text","delta":"Proposing a move."}\n\n',
      `data: ${JSON.stringify(PROPOSAL)}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();

    // Enter Control mode.
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);

    // Send a message; the mocked stream returns a proposal frame.
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: "move the arm to plateloc-out" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // Confirm card shows the authoritative fields.
    await screen.findByText("Authorize action");
    expect(screen.getByText(/UFactory xArm5 \(xarm\)/)).toBeTruthy();
    expect(screen.getByText("move.plateloc_out")).toBeTruthy();
    expect(screen.getByText(/node_id=plateloc_out/)).toBeTruthy();

    // Authorize routes through the control passthrough with the passthrough
    // action + validated args.
    fireEvent.click(screen.getByRole("button", { name: "Authorize" }));
    await waitFor(() =>
      expect(authorizeAssistantAction).toHaveBeenCalledWith(
        "xarm",
        "graph/move_to",
        { node_id: "plateloc_out" }
      )
    );
    await screen.findByText(/Authorized move\.plateloc_out/);
  });

  it("marks a proposal expired after its TTL and blocks authorize", async () => {
    vi.useFakeTimers();
    const shortProposal = {
      ...PROPOSAL,
      proposal: { ...PROPOSAL.proposal, expires_in_s: 5 },
    };
    installFetch([
      `data: ${JSON.stringify(shortProposal)}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    render(<AssistantBubble />);
    const launcher = await vi.waitFor(() =>
      screen.getByLabelText("Open lab assistant")
    );
    fireEvent.click(launcher);
    const control = await vi.waitFor(() =>
      screen.getByRole("button", { name: "Control" })
    );
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: "move" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await vi.waitFor(() => screen.getByText("Authorize action"));
    await vi.advanceTimersByTimeAsync(6000);
    expect(screen.getByText(/expired/i)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Authorize" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it("renders record-edit args as JSON rather than [object Object]", async () => {
    installFetch([
      `data: ${JSON.stringify({
        type: "proposal",
        proposal: {
          ...PROPOSAL.proposal,
          equipment_id: "ot2_hte",
          equipment_name: "Opentrons OT-2 HTE",
          kind: "liquid_handler",
          action: "plate.load",
          passthrough_action: "plate/load",
          args: {
            plate_id: "p-7",
            model: "corning_96_wellplate_360ul_flat",
            wells: [{ well: "A1", volume_ul: 50 }],
          },
        },
      })}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: "record the plate" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Authorize action");
    // The nested wells list must stay checkable; String(v) would flatten it.
    expect(screen.getByText(/wells=\[\{"well":"A1","volume_ul":50\}\]/)).toBeTruthy();
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });

  it("warns that a deck.declare with no slots clears the whole declaration", async () => {
    installFetch([
      `data: ${JSON.stringify({
        type: "proposal",
        proposal: {
          ...PROPOSAL.proposal,
          equipment_id: "ot2_hte",
          equipment_name: "Opentrons OT-2 HTE",
          kind: "liquid_handler",
          action: "deck.declare",
          passthrough_action: "deck/declare",
          args: { slots: {} },
          reason: "reset the layout",
        },
      })}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: "clear the deck" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // slots={} reads as a no-op in the arg table, so the card says it in words.
    await screen.findByText("Authorize action");
    expect(screen.getByText(/Clears the entire deck declaration/)).toBeTruthy();
  });


  it("renders a large setup body as a full pretty-printed block, untruncated", async () => {
    const setupArgs = {
      labware: [
        { nickname: "tips", location: "1", ot_default: true, loadname: "opentrons_96_tiprack_300ul" },
        { nickname: "plate", location: "2", ot_default: true, loadname: "corning_96_wellplate_360ul_flat" },
        { nickname: "reservoir", location: "3", ot_default: true, loadname: "nest_12_reservoir_15ml" },
      ],
      instruments: [
        { nickname: "p300", mount: "right", ot_default: true, instrument_name: "p300_multi_gen2" },
      ],
    };
    installFetch([
      `data: ${JSON.stringify({
        type: "proposal",
        proposal: {
          ...PROPOSAL.proposal,
          equipment_id: "ot2_hte",
          equipment_name: "Opentrons OT-2 HTE",
          kind: "liquid_handler",
          action: "setup",
          passthrough_action: "setup",
          args: setupArgs,
        },
      })}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: "set up the deck" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Authorize action");
    // Past the compact threshold the card switches to a scrollable block —
    // the args ARE the Authorize payload, so nothing may be truncated.
    const pre = document.querySelector("pre");
    expect(pre).toBeTruthy();
    expect(pre?.textContent).toContain("opentrons_96_tiprack_300ul");
    expect(pre?.textContent).toContain("nest_12_reservoir_15ml");
    expect(pre?.textContent).toContain("p300_multi_gen2");
    expect(pre?.textContent).not.toContain("…");
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });

});
