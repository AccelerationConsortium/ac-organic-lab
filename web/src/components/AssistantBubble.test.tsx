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
import {
  approveAssistantPlan,
  authorizeAssistantAction,
  finishAssistantPlan,
} from "@/lib/api";

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
  approveAssistantPlan: vi.fn(() => Promise.resolve({ approved: true })),
  finishAssistantPlan: vi.fn(() => Promise.resolve({ ok: true })),
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
    action: "move.uplc_draw_home",
    passthrough_action: "graph/move_to",
    args: { node_id: "uplc_draw_home" },
    reason: "stage the plate",
    actor: "alice@example.edu",
    expires_in_s: 120,
    device_state: { equipment_status: "ready", activity: "idle", message: "ok" },
  },
};

const PLAN_HASH = "h".repeat(64);
const PLAN = {
  type: "plan",
  plan: {
    plan_id: "p1",
    equipment_id: "xarm",
    equipment_name: "UFactory xArm5",
    kind: "robot_arm",
    steps: [
      { action: "move.a", passthrough_action: "graph/move_to", args: { node_id: "a" } },
      { action: "move.b", passthrough_action: "graph/move_to", args: { node_id: "b" } },
      { action: "gripper.grip_120", passthrough_action: "graph/gripper", args: { state: "grip_120" } },
    ],
    step_hash: PLAN_HASH,
    reason: "fetch the plate",
    actor: "alice@example.edu",
    expires_in_s: 600,
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

/** Like `sseBody`, but the stream never closes: the turn stays in flight,
 *  so the live progress pills stay rendered long enough to assert on. A
 *  pending read rejects with AbortError when `signal` aborts — what a real
 *  browser does when the fetch is aborted, and what the Stop button relies on. */
function sseBodyOpen(frames: string[], signal?: AbortSignal | null) {
  const bytes = new TextEncoder().encode(frames.join(""));
  let sent = false;
  return {
    getReader() {
      return {
        read() {
          if (sent) {
            return new Promise<never>((_, reject) => {
              const abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
              if (signal?.aborted) abort();
              else signal?.addEventListener("abort", abort, { once: true });
            });
          }
          sent = true;
          return Promise.resolve({ value: bytes, done: false });
        },
      };
    },
  };
}

let mineEquipment: Record<string, string | null>;

function installFetch(chatFrames: string[], opts: { open?: boolean } = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/assistant/health")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ configured: true, model: "sonnet", backend: "claude-code-cli" }),
      });
    }
    if (url.includes("/api/auth/mine")) {
      return Promise.resolve({ ok: true, json: async () => ({ equipment: mineEquipment }) });
    }
    if (url.includes("/api/assistant/chat")) {
      expect(init?.method).toBe("POST");
      return Promise.resolve({
        ok: true,
        body: opts.open ? sseBodyOpen(chatFrames, init?.signal) : sseBody(chatFrames),
      });
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
  (approveAssistantPlan as unknown as ReturnType<typeof vi.fn>).mockClear();
  (finishAssistantPlan as unknown as ReturnType<typeof vi.fn>).mockClear();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: 1280,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    writable: true,
    value: 800,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  sessionStorage.clear();
});

async function openPanel() {
  render(<AssistantBubble />);
  // Launcher only appears once the health check resolves configured:true.
  const launcher = await screen.findByLabelText("Open SDL Assistant");
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
    expect(screen.getByText("move.uplc_draw_home")).toBeTruthy();
    expect(screen.getByText(/node_id=uplc_draw_home/)).toBeTruthy();

    // Authorize routes through the control passthrough with the passthrough
    // action + validated args.
    fireEvent.click(screen.getByRole("button", { name: "Authorize" }));
    await waitFor(() =>
      expect(authorizeAssistantAction).toHaveBeenCalledWith(
        "xarm",
        "graph/move_to",
        { node_id: "uplc_draw_home" }
      )
    );
    await screen.findByText(/Authorized move\.uplc_draw_home/);
  });

  async function sendInControlMode(text: string) {
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
  }

  it("names the resolved place and the current deck on an OT-2 card (Step 1m)", async () => {
    const ot2 = {
      type: "proposal",
      proposal: {
        equipment_id: "ot2_hte",
        equipment_name: "Opentrons OT-2 HTE",
        kind: "liquid_handler",
        action: "tips.reset",
        passthrough_action: "tips/reset",
        args: { slot: "2" },
        reason: "refill the rack",
        actor: "alice@example.edu",
        expires_in_s: 120,
        device_state: { equipment_status: "ready", activity: "idle", message: "idle" },
        resolved_locations: [
          { field: "slot", value: "2", location: "ot2_hte/slot_2", label: "OT-2 HTE · slot 2", given: "ot2_hte/slot_2" },
        ],
        deck_checks: [
          {
            equipment_id: "ot2_hte",
            touched_slots: ["2"],
            slots: {
              "4": { labware: "agilent_96_2ml_deep_square", id: "slot_4" },
              "11": { labware: "opentrons_96_tiprack_1000ul", id: "slot_11", tips_available: 12 },
            },
          },
        ],
      },
    };
    installFetch([`data: ${JSON.stringify(ot2)}\n\n`, 'data: {"type":"done"}\n\n']);
    await sendInControlMode("refill the tips in slot 2");

    await screen.findByText("Authorize action");
    // The bare key the device receives, and the place in the registry's words.
    expect(screen.getByText(/slot=2 \(OT-2 HTE · slot 2\)/)).toBeTruthy();
    // The deck as the gateway sees it, touched slot starred, empty said out loud.
    expect(
      screen.getByText(
        /ot2_hte · 2\*: empty · 4: agilent_96_2ml_deep_square · 11: opentrons_96_tiprack_1000ul \(12 tips\)/
      )
    ).toBeTruthy();
    expect(screen.getByText(/Check the physical deck matches this before authorizing/)).toBeTruthy();
  });

  it("shows a captured camera frame in the turn, with the progress pills at the bottom", async () => {
    installFetch([
      'data: {"type":"tool_use","name":"capture_camera_snapshot"}\n\n',
      'data: {"type":"tool_result","name":"capture_camera_snapshot"}\n\n',
      'data: {"type":"image","image":{"url":"/api/assistant/snapshots/cam_hte_tapo_c245_wide_20260904T180000Z_ab12cd.jpg","camera_id":"cam_hte_tapo_c245","camera_name":"HTE bench camera","lens":"wide","taken_at":"2026-09-04T18:00:00+00:00","bytes":206799}}\n\n',
      'data: {"type":"text","delta":"The deck is empty and the sash is down."}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what does the HTE camera see?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const img = (await screen.findByAltText(/HTE bench camera \(wide\) snapshot/)) as HTMLImageElement;
    expect(img.getAttribute("src")).toBe(
      "/api/assistant/snapshots/cam_hte_tapo_c245_wide_20260904T180000Z_ab12cd.jpg"
    );
    expect(screen.getByText(/HTE bench camera · wide ·/)).toBeTruthy();
    const text = await screen.findByText(/The deck is empty/);
    const pill = screen.getByText(/capture camera snapshot/);
    // The pill row comes AFTER the answer text in the bubble (DOCUMENT_POSITION_FOLLOWING = 4).
    expect(text.compareDocumentPosition(pill) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("Stop aborts the in-flight turn and marks it stopped", async () => {
    // A stream that never closes: the turn stays in flight until we stop it.
    installFetch(['data: {"type":"status","phase":"thinking","label":"reasoning…"}\n\n'], {
      open: true,
    });
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what ran today?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    // While in flight, Stop stands where Send was.
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    fireEvent.click(stop);

    // The fetch was aborted, the composer is back, the turn says it was stopped.
    const chatCall = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find((c) =>
      String(c[0]).includes("/api/assistant/chat")
    );
    expect((chatCall?.[1] as RequestInit).signal?.aborted).toBe(true);
    await screen.findByRole("button", { name: "Send" });
    expect(screen.getByText(/Stopped by you/)).toBeTruthy();
    expect(screen.queryByText(/Connection lost/)).toBeNull();
  });

  it("renders a plan card, approves it by hash, then runs the steps in order (Step 1i)", async () => {
    installFetch([
      'data: {"type":"text","delta":"Proposing a route."}\n\n',
      `data: ${JSON.stringify(PLAN)}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    await sendInControlMode("fetch the plate");

    // The whole ordered list is on one card, nothing sent to the device yet.
    await screen.findByText(/Authorize plan · 3 steps/);
    expect(screen.getByText(/1\. move\.a/)).toBeTruthy();
    expect(screen.getByText(/3\. gripper\.grip_120/)).toBeTruthy();
    expect(authorizeAssistantAction).not.toHaveBeenCalled();

    // Approve sends the hash of exactly what was rendered.
    fireEvent.click(screen.getByRole("button", { name: "Approve these 3 steps" }));
    await waitFor(() => expect(approveAssistantPlan).toHaveBeenCalledWith("p1", PLAN_HASH));
    expect(authorizeAssistantAction).not.toHaveBeenCalled();

    // Run: this browser sends each step through the passthrough, in order,
    // stamped with the plan ref so the audit rows join back to the approval.
    fireEvent.click(await screen.findByRole("button", { name: "Run" }));
    await screen.findByText(/All 3 steps ran/);
    const calls = (authorizeAssistantAction as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.map((c) => c[1])).toEqual(["graph/move_to", "graph/move_to", "graph/gripper"]);
    expect(calls[0][0]).toBe("xarm");
    expect(calls[0][2]).toEqual({ node_id: "a" });
    expect(calls.map((c) => c[3])).toEqual([{ plan: "p1#1" }, { plan: "p1#2" }, { plan: "p1#3" }]);
    expect(finishAssistantPlan).toHaveBeenCalledWith(
      "p1",
      expect.objectContaining({ status: "executed" })
    );
  });

  it("halts a plan at the first refused step and skips the rest", async () => {
    (authorizeAssistantAction as unknown as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(new Error("412: not reachable from here"));
    installFetch([`data: ${JSON.stringify(PLAN)}\n\n`, 'data: {"type":"done"}\n\n']);
    await sendInControlMode("fetch the plate");

    await screen.findByText(/Authorize plan · 3 steps/);
    fireEvent.click(screen.getByRole("button", { name: "Approve these 3 steps" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run" }));

    // Fail-fast: step 3 is never sent.
    await screen.findByText(/Plan halted/);
    expect(authorizeAssistantAction).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Halted: step 2 \(move\.b\) failed: 412/)).toBeTruthy();
    expect(finishAssistantPlan).toHaveBeenCalledWith(
      "p1",
      expect.objectContaining({
        status: "failed",
        results: [
          { index: 1, outcome: "ok" },
          expect.objectContaining({ index: 2, outcome: "failed" }),
          { index: 3, outcome: "skipped" },
        ],
      })
    );
  });

  it("shows a plate-reader measurement response without sending it back to the model", async () => {
    (
      authorizeAssistantAction as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValueOnce({ wells: { A1: 0.042 }, wavelength_nm: 600 });
    installFetch([
      `data: ${JSON.stringify({
        type: "proposal",
        proposal: {
          ...PROPOSAL.proposal,
          equipment_id: "cytation_5",
          equipment_name: "BioTek Cytation 5",
          kind: "plate_reader",
          action: "read.absorbance",
          passthrough_action: "read/absorbance",
          args: { wells: ["A1"], wavelength_nm: 600 },
          reason: "read the diagnostic well",
        },
      })}\n\n`,
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: "read A1 at 600 nm" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Authorize action");
    fireEvent.click(screen.getByRole("button", { name: "Authorize" }));
    await screen.findByText("Device response");
    expect(screen.getByText(/0\.042/)).toBeTruthy();
    expect(screen.getByText(/wavelength_nm/)).toBeTruthy();
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
      screen.getByLabelText("Open SDL Assistant")
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


  it("shows the backing model under the chat box and a disabled Clear chip when empty", async () => {
    // jsdom sessionStorage survives across tests; drop turns persisted by
    // earlier ones so this panel opens genuinely empty.
    sessionStorage.clear();
    installFetch([]);
    await openPanel();
    // Model attribution comes from /api/assistant/health; the caption names the
    // agent layer rather than the transport, so the backend id is not shown.
    expect((await screen.findByText(/Hermes agents: sonnet/)).textContent).toContain(
      "Hermes agents"
    );
    // Clear is always rendered for discoverability; disabled until there are turns.
    const clear = screen.getByRole("button", { name: "Clear" });
    expect((clear as HTMLButtonElement).disabled).toBe(true);
  });


  it("keeps each turn's accent from the mode it was sent under", async () => {
    sessionStorage.clear();
    installFetch(['data: {"type":"text","delta":"ok"}\n\n', 'data: {"type":"done"}\n\n']);
    await openPanel();

    // Send one message in Ask mode -> emerald bubble.
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what ran today?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const askBubble = await screen.findByText("what ran today?");
    expect(askBubble.closest("div")?.className).toContain("bg-emerald-600");

    // Flip to Control and send another -> purple bubble, history untouched.
    const control = screen.getByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box2 = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box2, { target: { value: "home the ot2" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const controlBubble = await screen.findByText("home the ot2");
    expect(controlBubble.closest("div")?.className).toContain("bg-purple-600");
    // The earlier Ask turn did NOT get repainted by the toggle.
    expect(screen.getByText("what ran today?").closest("div")?.className).toContain(
      "bg-emerald-600"
    );
  });

  it("renders finished tool calls as green pills", async () => {
    sessionStorage.clear();
    installFetch([
      'data: {"type":"tool_use","name":"list_equipment_now"}\n\n',
      'data: {"type":"tool_result","name":"list_equipment_now"}\n\n',
      'data: {"type":"text","delta":"three devices are ready."}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const pill = await screen.findByText(/list equipment now/);
    expect(pill.className).toContain("rounded-full");
    expect(pill.className).toContain("bg-emerald-50");
    expect(pill.textContent).toMatch(/^✓/);
  });

  it("covers a silent reasoning stretch with a live thinking pill", async () => {
    // The complaint this answers: a reasoning model can burn 30-40 s before
    // its first visible token, and the bubble used to render nothing at all
    // for that whole stretch.
    sessionStorage.clear();
    installFetch(['data: {"type":"status","phase":"thinking"}\n\n'], {
      open: true,
    });
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const pill = await screen.findByText(/thinking/);
    expect(pill.className).toContain("rounded-full");
    expect(pill.className).toContain("bg-purple-50");
    expect(pill.className).toContain("animate-pulse");
    expect(pill.textContent).toMatch(/^↻/);
  });

  it("hands the live pill off to the tool once one starts", async () => {
    sessionStorage.clear();
    installFetch(
      [
        'data: {"type":"status","phase":"thinking"}\n\n',
        'data: {"type":"tool_use","name":"list_equipment_now"}\n\n',
      ],
      { open: true }
    );
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const pill = await screen.findByText(/list equipment now/);
    expect(pill.className).toContain("animate-pulse");
    // One live pill at a time: naming the tool is strictly more informative
    // than "thinking", so the phase pill retires.
    expect(screen.queryByText(/thinking/)).toBeNull();
  });

  it("stops pulsing a tool pill once the turn ends without a result", async () => {
    // An aborted or truncated turn must not leave a pill pulsing "running"
    // forever — nothing is running.
    sessionStorage.clear();
    installFetch(['data: {"type":"tool_use","name":"list_equipment_now"}\n\n']);
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const pill = await screen.findByText(/list equipment now/);
    await waitFor(() => expect(pill.className).not.toContain("animate-pulse"));
    expect(pill.className).toContain("bg-slate-100");
    expect(pill.textContent).toMatch(/^◦/);
  });

  it("does not resurrect a phase pill on a turn that is no longer streaming", async () => {
    // A turn restored from sessionStorage mid-flight must not pulse forever.
    sessionStorage.clear();
    installFetch([
      'data: {"type":"status","phase":"thinking"}\n\n',
      'data: {"type":"text","delta":"three devices are ready."}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("three devices are ready.");
    expect(screen.queryByText(/thinking/)).toBeNull();
  });

  it("shows the live stage label on the thinking pill", async () => {
    // A reasoning model spends tens of seconds thinking; the pill should say
    // what stage it is at ("waiting…" then "reasoning…") rather than a static
    // "thinking" that reads as hung.
    sessionStorage.clear();
    installFetch(
      [
        'data: {"type":"status","phase":"thinking","label":"waiting…"}\n\n',
        'data: {"type":"status","phase":"thinking","label":"reasoning…"}\n\n',
      ],
      { open: true }
    );
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    // The latest label wins.
    const pill = await screen.findByText(/reasoning/);
    expect(pill.textContent).toMatch(/reasoning/);
  });

  it("surfaces a connection-lost notice when the stream ends without a done frame", async () => {
    // A turn cut mid-flight (proxy drop, server restart) ends the fetch stream
    // with no "done"/"error" frame. This is the "assistant went quiet / non-
    // responsive" case and must be surfaced, not left as a frozen pill.
    // NOTE: deliberately NOT `open: true` — that stream never closes, so the
    // connection-lost branch would never be reached. We want the stream to
    // END normally but without a terminal frame.
    sessionStorage.clear();
    installFetch([
      'data: {"type":"status","phase":"thinking","label":"reasoning…"}\n\n',
      // No "done"/"error" frame follows: the stream just ends.
    ]);
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/connection lost/i);
  });

  it("does not raise a connection-lost notice on a normal completed turn", async () => {
    sessionStorage.clear();
    installFetch(
      [
        'data: {"type":"status","phase":"thinking","label":"reasoning…"}\n\n',
        'data: {"type":"text","delta":"all good."}\n\n',
        'data: {"type":"done"}\n\n',
      ],
      { open: true }
    );
    await openPanel();
    const box = screen.getByPlaceholderText(/ask about the lab/i);
    fireEvent.change(box, { target: { value: "what's running?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("all good.");
    expect(screen.queryByText(/connection lost/i)).toBeNull();
  });

});

describe("AssistantBubble resize", () => {
  it("exposes corner handles and restores a saved size", async () => {
    sessionStorage.setItem(
      "ac-assistant-size-v1",
      JSON.stringify({ w: 700, h: 640 })
    );
    installFetch([]);
    await openPanel();
    expect(screen.getByLabelText("Resize assistant panel from top left")).toBeTruthy();
    expect(screen.getByLabelText("Resize assistant panel")).toBeTruthy();
    const panel = screen.getByRole("dialog", { name: "SDL Assistant" });
    await waitFor(() => {
      expect(panel.style.width).toBe("700px");
      expect(panel.style.height).toBe("640px");
    });
  });

  it("clamps a restored size below the minimum usable dimensions", async () => {
    sessionStorage.setItem(
      "ac-assistant-size-v1",
      JSON.stringify({ w: 50, h: 50 })
    );
    installFetch([]);
    await openPanel();
    const panel = screen.getByRole("dialog", { name: "SDL Assistant" });
    await waitFor(() => {
      expect(Number.parseInt(panel.style.width, 10)).toBeGreaterThanOrEqual(360);
      expect(Number.parseInt(panel.style.height, 10)).toBeGreaterThanOrEqual(400);
    });
  });
});

describe("AssistantBubble Step 1j refusal/decline chips", () => {
  async function sendInControlMode(text: string) {
    await openPanel();
    const control = await screen.findByRole("button", { name: "Control" });
    await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(control);
    const box = screen.getByPlaceholderText(/operate a device/i);
    fireEvent.change(box, { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
  }

  it("renders an amber chip from a proposal_refused frame", async () => {
    installFetch([
      'data: {"type":"text","delta":"That is not currently allowed."}\n\n',
      'data: {"type":"proposal_refused","refusal":{"code":"not_allowed","message":"seal.start is not in allowed_actions"}}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await sendInControlMode("seal the plate");
    await screen.findByText(/Proposal refused \(not_allowed\)/);
    expect(screen.getByText(/seal\.start is not in allowed_actions/)).toBeTruthy();
  });

  it("renders a muted chip from a declined frame", async () => {
    installFetch([
      'data: {"type":"text","delta":"Stop verbs stay operator-only."}\n\n',
      'data: {"type":"declined","declined":{"reason_code":"safety_floor","explanation":"stop verbs are operator-only"}}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await sendInControlMode("stop the shaker");
    await screen.findByText(/No action proposed — stop verbs are operator-only/);
  });

  it("hides informational declines (they only terminate the turn)", async () => {
    installFetch([
      'data: {"type":"text","delta":"The shaker is idle."}\n\n',
      'data: {"type":"declined","declined":{"reason_code":"informational","explanation":"no action requested"}}\n\n',
      'data: {"type":"done"}\n\n',
    ]);
    await sendInControlMode("what is the shaker doing?");
    await screen.findByText(/The shaker is idle\./);
    expect(screen.queryByText(/No action proposed/)).toBeNull();
  });
});
