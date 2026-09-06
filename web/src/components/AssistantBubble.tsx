"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  ApiError,
  approveAssistantPlan,
  authorizeAssistantAction,
  finishAssistantPlan,
  type AssistantPlanStepResult,
} from "@/lib/api";
import { speakableFromMarkdown } from "@/lib/speech";
import { useVoiceInput } from "@/lib/use-voice-input";
import { useUserAuth } from "@/lib/user-auth";
import { downloadConversation, type ControlHistoryEvent } from "@/lib/assistant-transcript";

/**
 * Floating bottom-right chat bubble that talks to the dashboard's
 * `/api/assistant/chat` endpoint. The endpoint streams Server-Sent Events:
 *
 *   data: {"type":"text","delta":"..."}
 *   data: {"type":"tool_use","name":"...","input":{...}}
 *   data: {"type":"tool_result","name":"..."}
 *   data: {"type":"proposal","proposal":{...}}   // Control mode only
 *   data: {"type":"done"}
 *   data: {"type":"error","message":"..."}
 *
 * Two modes (UI_DESIGN §5):
 *  - Ask (default): read-only. The backend tools only query the history DB and
 *    tail whitelisted systemd units, never actuate hardware.
 *  - Control: adds the propose-only `lab-control` server. The model can PROPOSE
 *    one equipment action; a `proposal` frame renders a confirm card the
 *    operator must click *Authorize* on. Nothing actuates until that click,
 *    which runs the existing control passthrough as the human. The model never
 *    holds an actuating tool — the safety property is the toolset, not the
 *    prompt.
 */

type Role = "user" | "assistant";

/** What the backend is doing during a stretch that produces no visible
 *  token. Today there is one: the model is reasoning (or still queued). */
type Phase = "thinking";

interface ToolCall {
  name: string;
  ok: boolean;
  /** Epoch ms the call started, for the elapsed counter on the live pill.
   *  Absent on turns restored from an older sessionStorage payload. */
  startedAt?: number;
}

/** A camera frame the assistant captured during this turn
 *  (`capture_camera_snapshot` → an `image` SSE frame). `url` is the API's
 *  own copy of the frame (`/api/assistant/snapshots/…`, pruned after 24 h),
 *  so the picture in the chat is the moment that was captured, not a live
 *  re-grab on every render. */
interface ChatImage {
  url: string;
  camera_id: string;
  camera_name?: string;
  lens?: string;
  taken_at?: string;
  bytes?: number;
}

interface ChatTurn {
  id?: string;
  completion?: "streaming" | "completed" | "interrupted" | "failed";
  error?: string;
  control_events?: ControlHistoryEvent[];
  role: Role;
  /** Plain text content. For assistants, accumulates as `text` deltas arrive. */
  text: string;
  /** Tool calls observed during this assistant turn, oldest first. */
  tools: ToolCall[];
  /** Live phase, set by `status` frames and cleared by the first visible
   *  token or tool call. Only rendered on the streaming turn, so a value
   *  left behind by an aborted turn can never show as a stuck pill. */
  phase?: Phase | null;
  /** Human stage under the current phase (e.g. "waiting…" vs "reasoning…"),
   *  so the pill says what is happening instead of a static "thinking". */
  phaseLabel?: string;
  /** Epoch ms the current phase began — same role as `ToolCall.startedAt`. */
  phaseSince?: number;
  /** Which mode the turn was sent under — history keeps its original accent
   * (Ask emerald / Control purple) when the toggle later flips. Absent on
   * turns persisted before this field existed; those render as Ask, which
   * matches the old behavior exactly (mode resets to Ask on reload). */
  mode?: Mode;
  /** The operator pressed Stop while this turn was in flight: the fetch was
   *  aborted (which cancels the API's generator), whatever the model was doing
   *  was discarded, and no button could have been produced. Rendered as a
   *  muted chip so a half-written answer is not mistaken for a finished one. */
  stopped?: boolean;
  /** Camera frames captured during this turn, oldest first. */
  images?: ChatImage[];
  /** Control mode (Step 1j): a propose_action/propose_plan refusal surfaced
   * by the backend (`proposal_refused` frame). Rendered as an amber chip in
   * the turn, so "why is there no authorize button" is always on screen
   * instead of living only in the model's prose. */
  refusal?: { code: string; message: string };
  /** Control mode (Step 1j): the model's explicit no-proposal terminal
   * (`decline_proposal`). Informational declines are dropped at ingest —
   * they exist to end the turn, not to be read. */
  declined?: { reason_code: string; explanation: string };
}

/** A validated, propose-only action from the lab-control MCP server. */
/** A slot-carrying argument lab-control canonicalised against
 *  `locations.yaml` (UI_DESIGN §5 Step 1m): which argument, the device key it
 *  now carries, and the registry place + human label — so the card can name
 *  the place in words next to the bare key the device receives. `given` is
 *  the spelling the model supplied when it differed; `step` is set on plans. */
interface ResolvedLocation {
  field: string;
  value: string;
  location: string | null;
  label: string | null;
  given?: string;
  step?: number;
}

/** What an OT-2 gateway believes is on its deck at proposal time, for the
 *  device a proposal touches (the OT-2 itself, or the OT-2 an xArm move
 *  reaches into). `touched_slots` are the slots this action names or uses.
 *  The snapshot is the gateway's belief; the card asks the operator to check
 *  the physical deck against it before authorizing. */
interface DeckCheck {
  equipment_id: string;
  touched_slots: string[];
  slots: Record<string, { labware: string | null; id?: string | null; tips_available?: number }>;
  unreachable?: string;
}

interface Proposal {
  /** Local transcript association; never sent as authorization. */
  turn_id?: string;
  equipment_id: string;
  equipment_name: string;
  kind: string;
  action: string;
  /** The `{action}` segment the control passthrough expects (e.g. graph/move_to). */
  passthrough_action: string;
  args: Record<string, unknown>;
  reason: string;
  actor: string;
  expires_in_s: number;
  device_state: {
    equipment_status: string;
    activity: string;
    message: string | null;
  };
  resolved_locations?: ResolvedLocation[];
  deck_checks?: DeckCheck[];
}

/** One step of a plan: the same shape as a Proposal's action triple. */
interface PlanStep {
  action: string;
  passthrough_action: string;
  args: Record<string, unknown>;
}

/** A validated, ordered multi-step plan on ONE device from lab-control's
 *  `propose_plan` (UI_DESIGN §5 Step 1i). Approved as a whole — by the hash
 *  of exactly these steps — then run by this browser one step at a time. */
interface Plan {
  turn_id?: string;
  plan_id: string;
  equipment_id: string;
  equipment_name: string;
  kind: string;
  steps: PlanStep[];
  step_hash: string;
  reason: string;
  actor: string;
  expires_in_s: number;
  device_state: {
    equipment_status: string;
    activity: string;
    message: string | null;
  };
  resolved_locations?: ResolvedLocation[];
  deck_checks?: DeckCheck[];
}

type PlanPhase = "draft" | "approving" | "approved" | "running" | "executed" | "failed";
type StepOutcome = "pending" | "running" | "ok" | "failed" | "skipped";

/** Where a plan card is in its life, kept beside the immutable Plan. */
interface PlanRun {
  phase: PlanPhase;
  outcomes: StepOutcome[];
  messages: (string | null)[];
  haltReason: string | null;
  error: string | null;
  expired: boolean;
}

type Mode = "ask" | "control";

const STORAGE_KEY = "ac-assistant-history-v2";
const POSITION_KEY = "ac-assistant-position-v1";
const SIZE_KEY = "ac-assistant-size-v1";
// Read-aloud is a per-person preference, not per-conversation, so unlike the
// transcript above it lives in localStorage and survives a tab close.
const SPEAK_KEY = "ac-assistant-speak-v1";
const MAX_STORED_TURNS = 20;
const PANEL_W = 460;
const PANEL_H = 520;
const MIN_PANEL_W = 360;
const MIN_PANEL_H = 400;
// How much of the header must stay on screen so the user can always grab it
// to drag the panel back into view.
const MIN_VISIBLE = 80;

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function viewport(): { w: number; h: number } {
  if (typeof window === "undefined") return { w: PANEL_W, h: PANEL_H };
  const w = window.innerWidth;
  const h = window.innerHeight;
  return {
    w: Number.isFinite(w) && w > 0 ? w : PANEL_W,
    h: Number.isFinite(h) && h > 0 ? h : PANEL_H,
  };
}

function clampSize(w: number, h: number): { w: number; h: number } {
  const view = viewport();
  const maxW = Math.max(16, view.w - 16);
  const maxH = Math.max(16, view.h - 16);
  return {
    w: clamp(Math.round(w), Math.min(MIN_PANEL_W, maxW), maxW),
    h: clamp(Math.round(h), Math.min(MIN_PANEL_H, maxH), maxH),
  };
}

function defaultSize(): { w: number; h: number } {
  return clampSize(PANEL_W, PANEL_H);
}

/**
 * Default placement: tucked into the bottom-right corner just above the
 * launcher bubble, mirroring the prior fixed `bottom-20 right-5` Tailwind
 * classes. Computed lazily because `window` isn't available during SSR.
 */
function defaultPosition(size: { w: number; h: number } = defaultSize()): {
  x: number;
  y: number;
} {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  const view = viewport();
  return {
    x: Math.max(0, view.w - size.w - 20),
    y: Math.max(0, view.h - size.h - 80),
  };
}

export function AssistantBubble() {
  const { loading, authenticated, identity } = useUserAuth();
  if (loading) return null;
  const owner = authenticated ? identity?.email ?? null : null;
  // Remount clears messages, pending approvals, and speech when identity changes.
  return <AssistantBubbleInner key={owner ?? "anonymous"} owner={owner} />;
}

function AssistantBubbleInner({ owner }: { owner: string | null }) {
  const { authenticated, identity } = useUserAuth();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [cacheReady, setCacheReady] = useState(false);
  const activeTurnIdRef = useRef<string>();
  const disposedRef = useRef(false);
  const recordControl = useCallback((turnId: string | undefined, event: string, detail: Record<string, unknown>) => {
    if (!turnId) return;
    const entry = { event, detail, occurred_at: new Date().toISOString() };
    setTurns((prev) => prev.map((turn) => turn.id === turnId
      ? { ...turn, control_events: [...(turn.control_events ?? []), entry] }
      : turn));
  }, []);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Drives the elapsed counter on in-flight pills. One interval for the
  // whole bubble, and only while a turn is actually running.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!sending) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [sending]);
  const [error, setError] = useState<string | null>(null);
  // True when the last turn was cut mid-run (watchdog fired / stream ended
  // without a terminal frame) — i.e. the case where a resend of the last user
  // message is a sensible recovery. Real server-side errors and explicit
  // user aborts are not retryable from here.
  const [retryable, setRetryable] = useState(false);
  // The text of the last user message sent, so a Retry can re-submit it
  // without the user retyping.
  const lastInputRef = useRef("");
  // Ask (read-only) vs Control (propose-only). Deliberately NOT persisted:
  // resets to Ask on reload / panel close (UI_DESIGN §5.2). The server decides
  // the real toolset from the verified identity regardless of this value.
  const [mode, setMode] = useState<Mode>("ask");
  // null = not yet resolved. True when the signed-in user holds a role on at
  // least one equipment (operator+), from /api/auth/mine + identity role.
  const [controlEligible, setControlEligible] = useState<boolean | null>(null);
  // Read answers aloud. Off by default — a dashboard that starts talking
  // unprompted in a shared lab is a bad neighbour. Hidden entirely where the
  // browser has no speechSynthesis (it is not universal, and jsdom lacks it).
  const [speakAloud, setSpeakAloud] = useState(false);
  const [speechSupported, setSpeechSupported] = useState(false);
  // applyEvent is a []-dep callback, so the live toggle value reaches it via a
  // ref rather than by rebuilding the callback on every flip.
  const speakAloudRef = useRef(false);
  // Assistant text accumulated for THIS turn, mirrored outside the setTurns
  // updater. Speaking has to happen on the `done` frame, and a state updater
  // is the wrong place for a side effect (React may invoke it twice).
  const streamTextRef = useRef("");
  // Whether this turn's answer has already been spoken mid-stream. Speaking
  // the first sentence as soon as it completes — rather than on `done` —
  // is most of the perceived latency win for voice: the ear gets the answer
  // while the tail of the turn is still streaming to the eye.
  const spokeThisTurnRef = useRef(false);

  // Voice input (push-to-talk). The transcript is auto-SENT in Ask mode —
  // that mode is read-only, so the cost of a mishearing is one wasted query —
  // and only fills the input box in Control mode, where a turn can end in a
  // proposal card and the operator must see what they asked before it goes.
  const voice = useVoiceInput((text) => {
    if (modeRef.current === "ask") {
      void sendMessageRef.current(text);
    } else {
      setInput((prev) => (prev ? `${prev} ${text}` : text));
    }
  });
  const modeRef = useRef<Mode>("ask");
  const sendMessageRef = useRef<(t: string) => Promise<void>>(async () => {});

  // Server-side neural TTS (Kokoro on the lab GPU, /api/assistant/voice/speak)
  // is preferred: the browser's speechSynthesis voices are the espeak-era
  // robots on most lab machines. The browser voice survives only as the
  // fallback when the service is down or the fetch fails.
  const serverTtsRef = useRef(false);
  const speakAudioRef = useRef<HTMLAudioElement | null>(null);

  const stopSpeaking = useCallback(() => {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    const a = speakAudioRef.current;
    if (a) {
      a.pause();
      speakAudioRef.current = null;
    }
  }, []);

  // The single outstanding proposal (one at a time, UI_DESIGN §5.3).
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposalExpired, setProposalExpired] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const [authorizeResult, setAuthorizeResult] = useState<string | null>(null);
  const [authorizeResponse, setAuthorizeResponse] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [authorizeError, setAuthorizeError] = useState<string | null>(null);
  // Step 1i: the plan card. `plan` is what the tool produced (never edited
  // here — the hash Approve sends must be the hash of exactly this), `planRun`
  // is its review/run state.
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planRun, setPlanRun] = useState<PlanRun | null>(null);
  // null = still checking; once resolved, we know whether to render at all.
  // The bubble only renders if the backend's health endpoint reports it can
  // spawn `claude` (the Claude Code CLI subprocess backend). If the CLI
  // isn't installed on the dashboard host, the lab tooling is still
  // reachable directly via the `lab-history` MCP server in a terminal
  // session of Claude Code.
  const [configured, setConfigured] = useState<boolean | null>(null);
  // Which model/backend answers the chat (from /api/assistant/health), shown
  // under the input so operators know what they are talking to.
  const [backendInfo, setBackendInfo] = useState<{
    model?: string;
    backend?: string;
  } | null>(null);
  // Pixel coords of the panel's top-left corner. Null while the user hasn't
  // dragged yet -- we compute a default on first open from the viewport
  // size. Persisted to sessionStorage so the panel stays where the user
  // last left it within one tab.
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);
  // Null until the operator resizes (or a prior size is restored). Default
  // is PANEL_W × PANEL_H, clamped to the viewport.
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [resizing, setResizing] = useState(false);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Set only by the Stop button, so the AbortError it causes can be told
  // apart from the aborts a new question or a closed panel issue.
  const stoppedRef = useRef(false);
  const expiryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const planExpiryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // True while runPlan's step loop is in flight. A running plan is never
  // cleared out from under its loop — not by a new turn, not by closing the
  // panel — because the device calls keep going regardless and the operator
  // must be able to see how far they got.
  const planRunningRef = useRef(false);
  // True once a run has reached a terminal SSE frame ("done" or "error").
  // A stream that ends without one was cut mid-turn, not completed — that is
  // the "went quiet / non-responsive" case, and it must be surfaced instead
  // of letting the turn freeze in silence.
  const terminatedRef = useRef(false);
  // Captured at pointerdown; (px,py) = pointer at start, (bx,by) = panel
  // top-left at start. Cleared on pointerup.
  const dragStartRef = useRef<
    null | { px: number; py: number; bx: number; by: number }
  >(null);
  const resizeStartRef = useRef<
    null | {
      px: number;
      py: number;
      x: number;
      y: number;
      w: number;
      h: number;
      corner: "nw" | "se";
    }
  >(null);

  const panelSize = size ?? defaultSize();
  const panelPos = position ?? defaultPosition(panelSize);

  const controlMode = mode === "control";

  // One-shot health check on mount. Fail closed -- if the endpoint is
  // missing or we can't parse a JSON body, just hide the bubble.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/assistant/health");
        if (!r.ok) {
          if (!cancelled) setConfigured(false);
          return;
        }
        const body = await r.json();
        if (!cancelled) {
          setConfigured(Boolean(body?.configured));
          setBackendInfo({ model: body?.model, backend: body?.backend });
        }
      } catch {
        if (!cancelled) setConfigured(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Control-mode eligibility: signed in AND operator+ on >=1 equipment. A
  // global operator/admin qualifies even if the per-equipment map is empty;
  // a role:none account qualifies only through its equipment grants. The
  // server (assistant.py + propose_action) re-checks this — the gate here is
  // UX only (UI_DESIGN §5.2).
  useEffect(() => {
    if (!authenticated) {
      setControlEligible(false);
      return;
    }
    const roleEligible =
      identity?.role === "operator" || identity?.role === "admin";
    let cancelled = false;
    fetch("/api/auth/mine", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { equipment?: Record<string, string | null> } | null) => {
        if (cancelled) return;
        const anyGrant = Object.values(d?.equipment ?? {}).some((v) => v != null);
        setControlEligible(roleEligible || anyGrant);
      })
      .catch(() => {
        if (!cancelled) setControlEligible(roleEligible);
      });
    return () => {
      cancelled = true;
    };
  }, [authenticated, identity]);

  // If the user loses eligibility (e.g. logout), fall back to Ask.
  useEffect(() => {
    if (controlEligible === false && mode === "control") setMode("ask");
  }, [controlEligible, mode]);

  // Read-aloud: probe support and restore the saved preference once, on mount.
  // Support is probed rather than assumed — speechSynthesis is missing in some
  // browsers and in jsdom, and the toggle should not render where it is a no-op.
  useEffect(() => {
    const supported =
      typeof window !== "undefined" && typeof window.speechSynthesis !== "undefined";
    setSpeechSupported(supported);
    // Server TTS makes the toggle meaningful even where the browser has no
    // speechSynthesis of its own.
    fetch("/api/assistant/voice/health")
      .then((r) => (r.ok ? r.json() : { tts: false }))
      .then((j) => {
        serverTtsRef.current = j.tts === true;
        if (j.tts === true) setSpeechSupported(true);
      })
      .catch(() => undefined);
    if (!supported) return;
    try {
      if (localStorage.getItem(SPEAK_KEY) === "1") {
        setSpeakAloud(true);
        speakAloudRef.current = true;
      }
    } catch {
      /* private mode / storage disabled — stay off */
    }
  }, []);

  // Keep the ref in sync for applyEvent, persist the preference, and never
  // leave an utterance running when the feature is switched off.
  useEffect(() => {
    speakAloudRef.current = speakAloud;
    try {
      localStorage.setItem(SPEAK_KEY, speakAloud ? "1" : "0");
    } catch {
      /* ignore */
    }
    if (!speakAloud) stopSpeaking();
  }, [speakAloud, stopSpeaking]);

  // Silence on the way out: closing the panel, navigating away, or unmounting
  // must not leave the browser talking to an empty room. Escape stops it too —
  // the one control someone reaches for when they want it to stop NOW.
  useEffect(() => {
    if (!open) stopSpeaking();
  }, [open, stopSpeaking]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stopSpeaking();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      stopSpeaking();
    };
  }, [stopSpeaking]);

  // Only the verified owner may recover this tab's cache. Legacy unowned
  // history is discarded; anonymous chats remain in memory only.
  useEffect(() => {
    try {
      sessionStorage.removeItem("ac-assistant-history-v1");
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (owner && parsed?.owner === owner && Array.isArray(parsed.turns) &&
            parsed.turns.every((turn: ChatTurn) => turn &&
              (turn.role === "user" || turn.role === "assistant") &&
              typeof turn.text === "string" && Array.isArray(turn.tools) &&
              turn.tools.every((tool) => tool && typeof tool.name === "string" && typeof tool.ok === "boolean") &&
              (turn.images === undefined || (Array.isArray(turn.images) && turn.images.every((img) => img && typeof img.url === "string"))) &&
              (turn.control_events === undefined || Array.isArray(turn.control_events)))) {
          setTurns(parsed.turns.map((turn: ChatTurn) => ({
            ...turn, phase: null,
            completion: turn.completion === "streaming" ? "interrupted" : turn.completion,
          })));
        } else {
          sessionStorage.removeItem(STORAGE_KEY);
        }
      }
    } catch {
      /* ignore corrupt cache */
    }
    setCacheReady(true);
  }, [owner]);

  useEffect(() => {
    if (!cacheReady || !owner) return;
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ owner, turns: turns.slice(-MAX_STORED_TURNS) })
      );
    } catch {
      /* quota / private mode */
    }
  }, [turns, cacheReady, owner]);

  // Restore last drag position on mount; persist on each change. Like the
  // chat history, this is per-tab — closing the tab resets the position.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(POSITION_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (typeof parsed?.x === "number" && typeof parsed?.y === "number") {
          setPosition(parsed);
        }
      }
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    if (!position) return;
    try {
      sessionStorage.setItem(POSITION_KEY, JSON.stringify(position));
    } catch {
      /* quota */
    }
  }, [position]);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(SIZE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (typeof parsed?.w === "number" && typeof parsed?.h === "number") {
          setSize(clampSize(parsed.w, parsed.h));
        }
      }
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    if (!size) return;
    try {
      sessionStorage.setItem(SIZE_KEY, JSON.stringify(size));
    } catch {
      /* quota */
    }
  }, [size]);

  const clampPanelPos = useCallback(
    (x: number, y: number, w: number) => {
      const view = viewport();
      const maxX = Math.max(0, view.w - MIN_VISIBLE);
      const maxY = Math.max(0, view.h - 40);
      const minX = -(w - MIN_VISIBLE);
      return { x: clamp(x, minX, maxX), y: clamp(y, 0, maxY) };
    },
    []
  );

  // Pointer drag handlers. Attaches global pointermove/pointerup on
  // pointerdown so the drag keeps tracking even if the pointer leaves the
  // header element.
  const onDragStart = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Let header buttons (minimize / clear / mode toggle) and the corner
      // resize handles handle their own pointerdowns.
      if ((e.target as HTMLElement).closest("button")) return;
      if ((e.target as HTMLElement).closest("[data-resize]")) return;
      e.preventDefault();
      const base = position ?? defaultPosition(panelSize);
      dragStartRef.current = {
        px: e.clientX,
        py: e.clientY,
        bx: base.x,
        by: base.y,
      };
      setDragging(true);

      const onMove = (ev: PointerEvent) => {
        const start = dragStartRef.current;
        if (!start) return;
        const dx = ev.clientX - start.px;
        const dy = ev.clientY - start.py;
        setPosition(clampPanelPos(start.bx + dx, start.by + dy, panelSize.w));
      };
      const onUp = () => {
        dragStartRef.current = null;
        setDragging(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [position, panelSize, clampPanelPos]
  );

  const onResizeStart = useCallback(
    (corner: "nw" | "se") => (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      const pos = position ?? defaultPosition(panelSize);
      resizeStartRef.current = {
        px: e.clientX,
        py: e.clientY,
        x: pos.x,
        y: pos.y,
        w: panelSize.w,
        h: panelSize.h,
        corner,
      };
      setResizing(true);

      const onMove = (ev: PointerEvent) => {
        const start = resizeStartRef.current;
        if (!start) return;
        const dx = ev.clientX - start.px;
        const dy = ev.clientY - start.py;
        let nextW: number;
        let nextH: number;
        let nextX = start.x;
        let nextY = start.y;
        if (start.corner === "se") {
          nextW = start.w + dx;
          nextH = start.h + dy;
        } else {
          // NW: the pointer moves the top-left; the bottom-right stays put
          // until min/max size clamps. That's the grow-into-the-page direction
          // when the panel is docked bottom-right.
          nextW = start.w - dx;
          nextH = start.h - dy;
        }
        const clamped = clampSize(nextW, nextH);
        if (start.corner === "nw") {
          nextX = start.x + start.w - clamped.w;
          nextY = start.y + start.h - clamped.h;
        }
        setSize(clamped);
        setPosition(clampPanelPos(nextX, nextY, clamped.w));
      };
      const onUp = () => {
        resizeStartRef.current = null;
        setResizing(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [position, panelSize, clampPanelPos]
  );

  // Auto-scroll to bottom when content lands.
  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [turns, open, proposal, authorizeResponse]);

  const clearProposal = useCallback(() => {
    if (expiryRef.current) {
      clearTimeout(expiryRef.current);
      expiryRef.current = null;
    }
    setProposal(null);
    setProposalExpired(false);
    setAuthorizeError(null);
  }, []);

  const clearPlan = useCallback(() => {
    if (planRunningRef.current) return;
    if (planExpiryRef.current) {
      clearTimeout(planExpiryRef.current);
      planExpiryRef.current = null;
    }
    setPlan(null);
    setPlanRun(null);
  }, []);

  // Cancel any in-flight stream when the panel closes or the component
  // unmounts, and reset control mode + drop any outstanding proposal.
  useEffect(() => {
    if (!open && abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
      setSending(false);
    }
    if (!open) {
      setMode("ask");
      clearProposal();
      clearPlan();
    }
  }, [open, clearProposal, clearPlan]);
  useEffect(() => {
    disposedRef.current = false;
    return () => {
      disposedRef.current = true;
      abortRef.current?.abort();
      if (expiryRef.current) clearTimeout(expiryRef.current);
      if (planExpiryRef.current) clearTimeout(planExpiryRef.current);
    };
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      setError(null);
      setRetryable(false);
      lastInputRef.current = trimmed;
      // A new turn supersedes any pending proposal / result banner — and an
      // un-run plan card (a running one stays; see planRunningRef).
      clearProposal();
      clearPlan();
      setAuthorizeResult(null);
      setAuthorizeResponse(null);

      const turnId = crypto.randomUUID();
      activeTurnIdRef.current = turnId;
      const nextTurns: ChatTurn[] = [
        ...turns,
        { id: crypto.randomUUID(), role: "user", text: trimmed, tools: [], mode, completion: "completed" },
        { id: turnId, role: "assistant", text: "", tools: [], mode, completion: "streaming" },
      ];
      setTurns(nextTurns);
      setInput("");
      setSending(true);

      const controller = new AbortController();
      abortRef.current = controller;
      terminatedRef.current = false;
      stoppedRef.current = false;
      // New question: drop the previous answer's buffer, and stop speaking the
      // previous answer — it is now stale.
      streamTextRef.current = "";
      spokeThisTurnRef.current = false;
      stopSpeaking();

      try {
        const res = await fetch("/api/assistant/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            mode,
            conversation_owner: owner,
            messages: nextTurns
              .filter((t) => t.role === "user" || (t.role === "assistant" && t.text))
              // Context is bounded separately from the downloadable history.
              .slice(-40)
              .map((t) => ({ role: t.role, content: t.text })),
          }),
        });

        if (!res.ok) {
          const detail = await res.text();
          throw new Error(`HTTP ${res.status}: ${detail.slice(0, 300)}`);
        }
        if (!res.body) throw new Error("response has no body");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (controller.signal.aborted || disposedRef.current) return;
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // SSE frames are separated by a blank line.
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const line = frame
              .split("\n")
              .find((l) => l.startsWith("data:"));
            if (!line) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;
            let event: { type: string; [k: string]: unknown };
            try {
              event = JSON.parse(payload);
            } catch {
              continue;
            }
            applyEvent(event);
          }
        }
        // The fetch stream ended (server closed the response). A completed
        // turn always ends with a "done"/"error" frame; reaching the end of
        // the stream without one means the connection was cut mid-turn —
        // the exact "assistant went quiet / non-responsive" case. Surface it
        // instead of leaving a frozen pill. Deliberate user aborts throw
        // AbortError and return earlier, so they never hit this.
        if (!terminatedRef.current) {
          setTurns((prev) => prev.map((turn) => turn.id === turnId ? { ...turn, completion: "interrupted" } : turn));
          setRetryable(true);
          setError(
            "Connection lost — the assistant stopped responding before it finished. Check the service, then try again."
          );
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          setTurns((prev) => prev.map((turn) => turn.id === turnId ? { ...turn, completion: "interrupted" } : turn));
          if (stoppedRef.current) {
            // Deliberate stop: say so on the turn, and drop any live pill.
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = { ...last, phase: null, stopped: true };
              }
              return next;
            });
          }
          return;
        }
        setError((e as Error).message);
        setTurns((prev) => prev.map((turn) => turn.id === turnId
          ? { ...turn, completion: "failed", error: (e as Error).message } : turn));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setSending(false);
      }
    },
    [turns, sending, mode, owner, clearProposal, clearPlan, stopSpeaking]
  );

  /** Stop the in-flight turn. Aborting the fetch closes the SSE response,
   *  which cancels the API's generator mid-round; the model's next token and
   *  any pending tool call are simply never consumed. Nothing actuates from a
   *  chat turn, so there is nothing to roll back. */
  const stopTurn = useCallback(() => {
    if (!abortRef.current) return;
    stoppedRef.current = true;
    abortRef.current.abort();
  }, []);

  const speakAnswer = useCallback(
    (markdown: string) => {
      if (!speakAloudRef.current) return;
      const text = speakableFromMarkdown(markdown);
      if (!text) return;
      // Never queue: a new answer replaces the old one rather than making
      // the operator wait out a stale utterance.
      stopSpeaking();

      const fallback = () => {
        if (typeof window === "undefined" || !window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
      };

      if (!serverTtsRef.current) {
        fallback();
        return;
      }
      void (async () => {
        try {
          const res = await fetch("/api/assistant/voice/speak", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const url = URL.createObjectURL(await res.blob());
          const audio = new Audio(url);
          speakAudioRef.current = audio;
          audio.onended = () => {
            URL.revokeObjectURL(url);
            if (speakAudioRef.current === audio) speakAudioRef.current = null;
          };
          await audio.play();
        } catch {
          fallback();
        }
      })();
    },
    [stopSpeaking]
  );

  modeRef.current = mode;
  sendMessageRef.current = sendMessage;

  const applyEvent = useCallback(
    (event: { type: string; [k: string]: unknown }) => {
      // Mirror the streaming answer outside the state updater below, so the
      // `done` frame can hand a complete answer to speech synthesis without
      // side-effecting inside setTurns. Cheap, and idempotent per frame.
      if (event.type === "text" && typeof event.delta === "string") {
        streamTextRef.current += event.delta;
        // First sentence complete? Speak it now instead of at `done`. The
        // boundary check is cheap and the guard ref makes it once per turn.
        if (!spokeThisTurnRef.current && speakAloudRef.current) {
          const m = streamTextRef.current.match(/^[\s\S]{10,}?[.!?](?=\s)/);
          if (m) {
            spokeThisTurnRef.current = true;
            speakAnswer(m[0]);
          }
        }
      } else if (event.type === "done") {
        // Already spoken mid-stream: the rest of the turn is for the eyes.
        if (!spokeThisTurnRef.current) speakAnswer(streamTextRef.current);
      }
      // Keep a display-only projection in history; the live card is separate
      // and is never reconstructed from storage or downloads.
      if (event.type === "proposal" && event.proposal && typeof event.proposal === "object") {
        const p = event.proposal as Proposal;
        setAuthorizeError(null);
        setAuthorizeResult(null);
        setAuthorizeResponse(null);
        setProposalExpired(false);
        setProposal({ ...p, turn_id: activeTurnIdRef.current });
        recordControl(activeTurnIdRef.current, "action_proposed", {
          equipment_id: p.equipment_id, action: p.action, args: p.args, reason: p.reason,
          device_state: p.device_state, deck_checks: p.deck_checks,
        });
        if (expiryRef.current) clearTimeout(expiryRef.current);
        const ttlMs = Math.max(5, Number(p.expires_in_s) || 120) * 1000;
        expiryRef.current = setTimeout(() => setProposalExpired(true), ttlMs);
        return;
      }
      // Step 1i: a plan is a proposal's multi-step sibling — one card, the
      // whole ordered list, approved by hash and then run from here.
      if (event.type === "plan" && event.plan && typeof event.plan === "object") {
        const p = event.plan as Plan;
        if (!Array.isArray(p.steps) || p.steps.length === 0) return;
        if (planRunningRef.current) return; // never displace a plan mid-run
        setPlan({ ...p, turn_id: activeTurnIdRef.current });
        recordControl(activeTurnIdRef.current, "sequence_proposed", {
          equipment_id: p.equipment_id, reason: p.reason,
          steps: p.steps.map(({ action, args }) => ({ action, args })),
          device_state: p.device_state, deck_checks: p.deck_checks,
        });
        setPlanRun({
          phase: "draft",
          outcomes: p.steps.map(() => "pending"),
          messages: p.steps.map(() => null),
          haltReason: null,
          error: null,
          expired: false,
        });
        if (planExpiryRef.current) clearTimeout(planExpiryRef.current);
        const ttlMs = Math.max(5, Number(p.expires_in_s) || 600) * 1000;
        planExpiryRef.current = setTimeout(
          () =>
            setPlanRun((r) => (r && r.phase === "draft" ? { ...r, expired: true } : r)),
          ttlMs
        );
        return;
      }
      setTurns((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.role !== "assistant") return prev;
        const updated: ChatTurn = { ...last };
        if (event.type === "status") {
          // Repeat frames for the same phase are heartbeats, not new phases:
          // keep the original start time so the counter keeps climbing
          // instead of resetting to zero every second.
          const phase = event.phase === "thinking" ? "thinking" : null;
          updated.phase = phase;
          updated.phaseLabel =
            phase && typeof event.label === "string" ? event.label : undefined;
          updated.phaseSince =
            phase && last.phase === phase
              ? last.phaseSince ?? Date.now()
              : Date.now();
        } else if (event.type === "text" && typeof event.delta === "string") {
          updated.text = (updated.text || "") + event.delta;
          // A visible token supersedes the phase pill — the answer itself is
          // now the progress indicator.
          updated.phase = null;
          updated.phaseLabel = undefined;
        } else if (event.type === "tool_use" && typeof event.name === "string") {
          updated.tools = [
            ...updated.tools,
            { name: event.name, ok: false, startedAt: Date.now() },
          ];
          updated.phase = null;
          updated.phaseLabel = undefined;
        } else if (event.type === "tool_result" && typeof event.name === "string") {
          // Mark the most recent matching tool as completed. The claude-cli
          // SSE bridge emits tool_result name "tool" (it does not have the
          // MCP name on that event); treat that as "the in-flight tool".
          const idx = [...updated.tools]
            .reverse()
            .findIndex(
              (t) =>
                !t.ok && (t.name === event.name || event.name === "tool")
            );
          if (idx >= 0) {
            const realIdx = updated.tools.length - 1 - idx;
            updated.tools = updated.tools.map((t, i) =>
              i === realIdx ? { ...t, ok: true } : t
            );
          }
        } else if (event.type === "image" && event.image && typeof event.image === "object") {
          // The tool result names the path `image_url`; the frame carries it
          // under `url` too. Accept either, so a payload shape drift on one
          // side can never silently drop the picture again.
          const raw = event.image as ChatImage & { image_url?: string };
          const url = typeof raw.url === "string" ? raw.url : raw.image_url;
          if (typeof url === "string" && url.startsWith("/")) {
            updated.images = [...(updated.images ?? []), { ...raw, url }];
          }
        } else if (
          event.type === "proposal_refused" &&
          event.refusal &&
          typeof event.refusal === "object"
        ) {
          // Step 1j: the proposal was attempted and the lab-control server
          // refused it. Without this chip the refusal is invisible — the
          // operator sees the request "understood" and no button.
          const r = event.refusal as { code?: unknown; message?: unknown };
          updated.refusal = {
            code: String(r.code ?? ""),
            message: String(r.message ?? ""),
          };
        } else if (
          event.type === "declined" &&
          event.declined &&
          typeof event.declined === "object"
        ) {
          // Step 1j: the model explicitly ended the turn without proposing.
          // Informational declines carry no information beyond "this was
          // Q&A" — the prose already answers — so they render nothing.
          const d = event.declined as {
            reason_code?: unknown;
            explanation?: unknown;
          };
          if (d.reason_code !== "informational") {
            updated.declined = {
              reason_code: String(d.reason_code ?? "other"),
              explanation: String(d.explanation ?? ""),
            };
          }
        } else if (event.type === "done") {
          // Natural end of a completed turn. Marks the run as terminated so
          // the stream-end check below knows it finished, not got cut.
          terminatedRef.current = true;
          if (updated.completion !== "failed") updated.completion = "completed";
          updated.phase = null;
          updated.phaseLabel = undefined;
        } else if (event.type === "error" && typeof event.message === "string") {
          terminatedRef.current = true;
          setError(event.message);
          updated.completion = "failed";
          updated.error = event.message;
        }
        return [...prev.slice(0, -1), updated];
      });
    },
    [speakAnswer, recordControl]
  );

  const authorizeProposal = useCallback(async () => {
    if (!proposal || authorizing || proposalExpired) return;
    setAuthorizing(true);
    setAuthorizeError(null);
    recordControl(proposal.turn_id, "action_submitted", { action: proposal.action });
    try {
      const response = await authorizeAssistantAction(
        proposal.equipment_id,
        proposal.passthrough_action,
        proposal.args
      );
      setAuthorizeResult(
        `Authorized ${proposal.action} on ${proposal.equipment_name}.`
      );
      recordControl(proposal.turn_id, "action_response", { action: proposal.action, outcome: "ok" });
      // Read/imaging responses are the useful output of operating a plate
      // reader. Keep them browser-side and visible; the assistant model's turn
      // has already ended, so scientific values are not fed back to it.
      setAuthorizeResponse(
        proposal.kind === "plate_reader" &&
          (proposal.action.startsWith("read.") ||
            proposal.action === "imaging.capture")
          ? response
          : null
      );
      clearProposal();
    } catch (e) {
      // The device's 412/423 (precondition / claim) is the real backstop and
      // arrives here as an ApiError; surface its structured detail.
      const msg =
        e instanceof ApiError
          ? `${e.status}: ${e.message}`
          : (e as Error).message;
      setAuthorizeError(msg);
      recordControl(proposal.turn_id, "action_response", { action: proposal.action, outcome: "failed", message: msg });
    } finally {
      setAuthorizing(false);
    }
  }, [proposal, authorizing, proposalExpired, clearProposal, recordControl]);

  // ---- Plan card: approve (by hash) → run (this browser, step by step) ----

  const approvePlan = useCallback(async () => {
    if (!plan || !planRun || planRun.phase !== "draft" || planRun.expired) return;
    setPlanRun((r) => r && { ...r, phase: "approving", error: null });
    try {
      // The hash of exactly the steps this card rendered — a plan that
      // changed, or a dashboard that restarted, is refused (409/404), never
      // silently re-approved.
      await approveAssistantPlan(plan.plan_id, plan.step_hash);
      setPlanRun((r) => r && { ...r, phase: "approved" });
      recordControl(plan.turn_id, "sequence_approved", {});
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.status}: ${e.message}` : (e as Error).message;
      setPlanRun((r) => r && { ...r, phase: "draft", error: msg });
    }
  }, [plan, planRun, recordControl]);

  const runPlan = useCallback(async () => {
    if (!plan || !planRun || planRun.phase !== "approved" || planRunningRef.current || disposedRef.current) return;
    planRunningRef.current = true;
    recordControl(plan.turn_id, "sequence_started", {});
    const outcomes: StepOutcome[] = plan.steps.map(() => "pending");
    const messages: (string | null)[] = plan.steps.map(() => null);
    const results: AssistantPlanStepResult[] = [];
    let haltReason: string | null = null;
    setPlanRun(
      (r) =>
        r && {
          ...r,
          phase: "running",
          outcomes: [...outcomes],
          messages: [...messages],
          haltReason: null,
          error: null,
        }
    );
    for (let i = 0; i < plan.steps.length; i++) {
      // Signing out or changing accounts must not launch the next command
      // under a new identity. A command already submitted can still finish.
      if (disposedRef.current) {
        haltReason = "Assistant session ended; remaining steps were not submitted.";
        for (let j = i; j < plan.steps.length; j++) {
          outcomes[j] = "skipped";
          results.push({ index: j + 1, outcome: "skipped" });
        }
        break;
      }
      const step = plan.steps[i];
      outcomes[i] = "running";
      setPlanRun((r) => r && { ...r, outcomes: [...outcomes] });
      recordControl(plan.turn_id, "sequence_step_submitted", { index: i + 1 });
      try {
        // The same passthrough a tile click or a single Authorize uses —
        // per-equipment authz, the device's own 412/423, and the audit row
        // all apply per step; the plan ref joins the row to the approval.
        await authorizeAssistantAction(
          plan.equipment_id,
          step.passthrough_action,
          step.args,
          { plan: `${plan.plan_id}#${i + 1}` }
        );
        outcomes[i] = "ok";
        results.push({ index: i + 1, outcome: "ok" });
      } catch (e) {
        const status = e instanceof ApiError ? e.status : null;
        const msg =
          e instanceof ApiError ? `${e.status}: ${e.message}` : (e as Error).message;
        outcomes[i] = "failed";
        messages[i] = msg;
        results.push({ index: i + 1, outcome: "failed", status_code: status, message: msg.slice(0, 500) });
        // Fail-fast, never continue-past-error: the later steps of a
        // sequence assume the earlier ones happened.
        for (let j = i + 1; j < plan.steps.length; j++) {
          outcomes[j] = "skipped";
          results.push({ index: j + 1, outcome: "skipped" });
        }
        haltReason = `step ${i + 1} (${step.action}) failed: ${msg}`;
      }
      setPlanRun((r) => r && { ...r, outcomes: [...outcomes], messages: [...messages] });
      recordControl(plan.turn_id, "sequence_step_response", {
        index: i + 1, outcome: outcomes[i], message: messages[i],
      });
      if (haltReason) break;
    }
    const phase: PlanPhase = haltReason ? "failed" : "executed";
    setPlanRun((r) => r && { ...r, phase, haltReason });
    recordControl(plan.turn_id, "sequence_finished", { status: phase, results, halt_reason: haltReason });
    planRunningRef.current = false;
    try {
      await finishAssistantPlan(plan.plan_id, {
        status: phase,
        results,
        halt_reason: haltReason?.slice(0, 500) ?? null,
      });
    } catch (e) {
      const message = `Completion report could not be saved: ${(e as Error).message}`;
      setPlanRun((r) => r && { ...r, error: message });
      recordControl(plan.turn_id, "completion_report_failed", { message });
    }
  }, [plan, planRun, recordControl]);

  const dismissPlan = useCallback(() => {
    if (!plan || !planRun || planRun.phase === "running") return;
    recordControl(plan.turn_id, "sequence_dismissed", { phase: planRun.phase });
    if (planRun.phase === "draft" || planRun.phase === "approved") {
      void finishAssistantPlan(plan.plan_id, { status: "aborted", results: [] }).catch(
        () => undefined
      );
    }
    clearPlan();
  }, [plan, planRun, clearPlan, recordControl]);

  const clearHistory = useCallback(() => {
    if (planRunningRef.current || authorizing) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setTurns([]);
    setError(null);
    clearProposal();
    clearPlan();
    setAuthorizeResult(null);
    setAuthorizeResponse(null);
  }, [clearProposal, clearPlan, authorizing]);

  // Suppress the launcher entirely if the dashboard host has no API key.
  // The MCP path (Claude Code) is the supported alternative; see the deploy
  // README for `claude mcp add`.
  if (configured !== true) return null;

  // Mode-driven accent. Purple must be unmistakable and panel-wide in Control
  // mode (UI_DESIGN §5.2), not a small badge.
  const launcherClass = controlMode
    ? "bg-purple-600 hover:bg-purple-700 focus-visible:ring-purple-400"
    : "bg-emerald-600 hover:bg-emerald-700 focus-visible:ring-emerald-400";
  const sendClass = controlMode
    ? "bg-purple-600 hover:bg-purple-700"
    : "bg-emerald-600 hover:bg-emerald-700";
  const focusClass = controlMode
    ? "focus:border-purple-500"
    : "focus:border-emerald-500";

  return (
    <>
      {/* Floating launcher */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close SDL Assistant" : "Open SDL Assistant"}
        className={`fixed bottom-5 right-5 z-50 flex h-12 w-12 items-center justify-center rounded-full text-white shadow-lg transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 ${launcherClass}`}
      >
        {open ? (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-5 w-5"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        ) : (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-5 w-5"
          >
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
        )}
      </button>

      {/* Panel */}
      {open && (
        <div
          role="dialog"
          aria-label="SDL Assistant"
          style={{
            left: panelPos.x,
            top: panelPos.y,
            width: panelSize.w,
            height: panelSize.h,
          }}
          className={`fixed z-50 flex flex-col overflow-hidden rounded-xl border bg-surface-raised shadow-2xl dark:bg-slate-900 ${
            controlMode
              ? "border-purple-300 dark:border-purple-800"
              : "border-slate-200 dark:border-slate-700"
          } ${resizing ? "select-none" : ""}`}
        >
          <ResizeHandle
            corner="nw"
            controlMode={controlMode}
            onPointerDown={onResizeStart("nw")}
          />
          <header
            onPointerDown={onDragStart}
            className={`flex items-center justify-between gap-2 border-b px-3 py-2 ${
              controlMode
                ? "border-purple-200 bg-purple-50/60 dark:border-purple-800 dark:bg-purple-950/30"
                : "border-slate-200 dark:border-slate-700"
            } ${dragging ? "cursor-grabbing" : "cursor-grab"} select-none touch-none`}
            title="Drag to move · corners resize"
          >
            <div className="flex flex-col pl-3">
              <span className="text-base font-semibold leading-tight text-ink dark:text-slate-100">
                SDL Assistant
              </span>
              <span className="text-xs text-ink-subtle dark:text-slate-400">
                {controlMode
                  ? "Control · proposes actions you authorize"
                  : "Read-only · history + journald"}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <ModeToggle
                mode={mode}
                eligible={controlEligible === true}
                onChange={(m) => {
                  setMode(m);
                  if (m === "ask") clearProposal();
                }}
              />
              {speechSupported && (
                <button
                  type="button"
                  onClick={() => setSpeakAloud((v) => !v)}
                  aria-pressed={speakAloud}
                  aria-label={
                    speakAloud ? "Turn off reading answers aloud" : "Read answers aloud"
                  }
                  title={
                    speakAloud
                      ? "Reading answers aloud — a short spoken summary of each answer. Esc stops it. Click to turn off."
                      : "Read answers aloud (a short spoken summary; the full answer stays on screen)"
                  }
                  className={`rounded border px-2 py-1 text-xs font-medium transition ${
                    speakAloud
                      ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300"
                      : "border-slate-300 text-ink-subtle hover:bg-slate-100 hover:text-ink dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                  }`}
                >
                  {speakAloud ? "🔊" : "🔇"}
                </button>
              )}
              <button
                type="button"
                onClick={clearHistory}
                disabled={turns.length === 0 || authorizing || planRun?.phase === "running"}
                title="Clear the conversation (proposals and authorized actions stay in the audit trail)"
                className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-ink-subtle transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Minimize to bubble"
                title="Minimize"
                className="rounded px-2 py-1 text-xs leading-none text-ink-subtle hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                {/* Minus glyph -- intentionally not ✕, since the conversation
                    persists when the panel collapses back to the bubble. */}
                &minus;
              </button>
            </div>
          </header>

          <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 px-3 py-2 text-xs text-ink-subtle dark:border-slate-700 dark:text-slate-400">
            <p className="flex-1">Temporary chat. Download before closing this tab. Proposals and control actions remain in the audit trail.</p>
            {(["md", "json"] as const).map((format) => (
              <button key={format} type="button" disabled={turns.length === 0}
                aria-label={`Download conversation as ${format === "md" ? "Markdown" : "JSON"}`}
                className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40 dark:border-slate-600"
                onClick={() => {
                  try { downloadConversation(turns, format); }
                  catch (e) { setError(`Download failed: ${(e as Error).message}`); }
                }}>
                {format === "md" ? "MD" : "JSON"}
              </button>
            ))}
          </div>

          <div
            ref={scrollerRef}
            className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3 text-base"
          >
            {turns.length === 0 && (
              <div className="text-xs text-ink-subtle dark:text-slate-400">
                {controlMode ? (
                  <>
                    Control mode. Ask me to operate a device — I&apos;ll propose
                    a single action for you to authorize. For example:
                    <ul className="mt-2 list-disc space-y-1 pl-4">
                      <li>Move the xArm to the plateloc-out node.</li>
                      <li>Press up on Waters Filtration.</li>
                      <li>Seal a plate on the PlateLoc at 170 °C for 3 seconds.</li>
                      <li>What can the xArm do right now?</li>
                    </ul>
                  </>
                ) : (
                  <>
                    Ask about lab equipment history — for example:
                    <ul className="mt-2 list-disc space-y-1 pl-4">
                      <li>Has the plateloc had any errors today?</li>
                      <li>Which devices are unreachable right now?</li>
                      <li>What does the API log look like over the past hour?</li>
                    </ul>
                  </>
                )}
              </div>
            )}
            {turns.map((turn, i) => (
              <Turn
                key={i}
                turn={turn}
                live={
                  sending &&
                  i === turns.length - 1 &&
                  turn.role === "assistant"
                }
                now={now}
              />
            ))}
            {proposal && (
              <ProposalCard
                proposal={proposal}
                expired={proposalExpired}
                authorizing={authorizing}
                error={authorizeError}
                onAuthorize={() => void authorizeProposal()}
                onDismiss={clearProposal}
              />
            )}
            {plan && planRun && (
              <PlanCard
                plan={plan}
                run={planRun}
                onApprove={() => void approvePlan()}
                onRun={() => void runPlan()}
                onDismiss={dismissPlan}
              />
            )}
            {authorizeResult && (
              <div className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200">
                {authorizeResult}
              </div>
            )}
            {authorizeResponse && (
              <div className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200">
                <div className="mb-1 font-medium">Device response</div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-emerald-100/70 p-1.5 font-mono text-xs leading-snug dark:bg-emerald-900/30">
                  {JSON.stringify(authorizeResponse, null, 2)}
                </pre>
              </div>
            )}
            {error && (
              <div className="flex items-center gap-2 rounded border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-800 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200">
                <span className="flex-1">{error}</span>
                {retryable && lastInputRef.current && (
                  <button
                    type="button"
                    disabled={sending}
                    onClick={() => void sendMessage(lastInputRef.current)}
                    className="shrink-0 rounded border border-rose-400 bg-white px-2 py-0.5 font-medium text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-rose-600 dark:bg-rose-900/60 dark:text-rose-200 dark:hover:bg-rose-800"
                  >
                    Retry
                  </button>
                )}
              </div>
            )}
          </div>

          <form
            className={`border-t px-3 py-2 ${
              controlMode
                ? "border-purple-200 dark:border-purple-800"
                : "border-slate-200 dark:border-slate-700"
            }`}
            onSubmit={(e) => {
              e.preventDefault();
              void sendMessage(input);
            }}
          >
            <div className="flex items-end gap-2">
              {voice.available && (
                <button
                  type="button"
                  onClick={() => void voice.start()}
                  disabled={sending || voice.state === "transcribing"}
                  aria-label={
                    voice.state === "recording"
                      ? "Stop recording"
                      : "Ask by voice"
                  }
                  title={
                    voice.state === "recording"
                      ? "Listening — click to stop (stops by itself when you pause)"
                      : voice.state === "transcribing"
                        ? "Transcribing…"
                        : controlMode
                          ? "Ask by voice (fills the box — review before sending in Control mode)"
                          : "Ask by voice (sends when you stop talking)"
                  }
                  className={`self-stretch rounded border px-2.5 text-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    voice.state === "recording"
                      ? "animate-pulse border-red-400 bg-red-50 text-red-600 dark:border-red-600 dark:bg-red-950/40 dark:text-red-400"
                      : "border-slate-300 text-ink-subtle hover:bg-slate-100 hover:text-ink dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  {voice.state === "recording" ? "◼" : voice.state === "transcribing" ? "…" : "🎤"}
                </button>
              )}
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void sendMessage(input);
                  }
                }}
                placeholder={
                  sending
                    ? "Working…"
                    : controlMode
                      ? "Ask me to operate a device…"
                      : "Ask about the lab…"
                }
                rows={2}
                disabled={sending}
                className={`flex-1 resize-none rounded border border-slate-300 bg-white px-2 py-1 text-[13px] text-ink shadow-inner focus:outline-none disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 ${focusClass}`}
              />
              {sending ? (
                <button
                  type="button"
                  onClick={stopTurn}
                  title="Stop this turn"
                  className="self-stretch rounded border border-red-400 bg-red-50 px-3 text-sm font-medium text-red-700 shadow-sm transition hover:bg-red-100 dark:border-red-600 dark:bg-red-950/40 dark:text-red-300 dark:hover:bg-red-950/70"
                >
                  Stop
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  className={`self-stretch rounded px-3 text-sm font-medium text-white shadow-sm transition disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700 ${sendClass}`}
                >
                  Send
                </button>
              )}
            </div>
            {voice.error && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{voice.error}</p>
            )}
            {backendInfo?.model && (
              <p className="mt-1 text-center text-xs text-ink-subtle dark:text-slate-400">
                Hermes agents: {backendInfo.model}
              </p>
            )}
          </form>
          <ResizeHandle
            corner="se"
            controlMode={controlMode}
            onPointerDown={onResizeStart("se")}
          />
        </div>
      )}
    </>
  );
}

function ResizeHandle({
  corner,
  controlMode,
  onPointerDown,
}: {
  corner: "nw" | "se";
  controlMode: boolean;
  onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
}) {
  const pos =
    corner === "se"
      ? "bottom-0 right-0 cursor-se-resize"
      : "top-0 left-0 cursor-nw-resize";
  const label =
    corner === "se"
      ? "Resize assistant panel"
      : "Resize assistant panel from top left";
  const stroke = controlMode ? "stroke-purple-400" : "stroke-slate-400";
  return (
    <div
      data-resize={corner}
      role="separator"
      aria-label={label}
      aria-orientation="horizontal"
      title="Drag to resize"
      onPointerDown={onPointerDown}
      className={`absolute z-10 flex h-4 w-4 touch-none items-center justify-center ${pos}`}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 12 12"
        className={`h-3 w-3 ${stroke} ${corner === "nw" ? "rotate-180" : ""}`}
        fill="none"
        aria-hidden="true"
      >
        <path d="M8 12 L12 8" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M4 12 L12 4" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </div>
  );
}

function ModeToggle({
  mode,
  eligible,
  onChange,
}: {
  mode: Mode;
  eligible: boolean;
  onChange: (m: Mode) => void;
}) {
  const disabled = !eligible;
  return (
    <div
      // Sized to match the Clear chip beside it (text-xs, py-1) so the
      // header buttons read as one control family at equal height.
      className="mr-1 flex overflow-hidden rounded border border-slate-300 text-xs font-medium dark:border-slate-600"
      title={
        disabled
          ? "Control mode requires an operator role on at least one device"
          : "Switch between read-only and propose-only control"
      }
    >
      <button
        type="button"
        onClick={() => onChange("ask")}
        className={`px-2 py-1 ${
          mode === "ask"
            ? "bg-emerald-600 text-white"
            : "text-ink-subtle hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
        }`}
      >
        Ask
      </button>
      <button
        type="button"
        onClick={() => !disabled && onChange("control")}
        disabled={disabled}
        aria-disabled={disabled}
        className={`px-2 py-1 ${
          mode === "control"
            ? "bg-purple-600 text-white"
            : disabled
              ? "cursor-not-allowed text-slate-300 dark:text-slate-600"
              : "text-ink-subtle hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
        }`}
      >
        Control
      </button>
    </div>
  );
}

/** Render one proposal argument inline for the confirm card.
 *
 * `String(v)` turns every object and array into `[object Object]`, which is
 * worse than terse — an unreadable card is a rubber stamp rather than a gate,
 * and record-edit proposals (`plate.load` wells, `deck.declare` slots) carry
 * exactly those shapes. JSON keeps them checkable. Only used when the args are
 * compact (see `argsNeedBlock`); large sets get the full pretty-printed block.
 */
function formatArg(v: unknown): string {
  if (v === null || typeof v !== "object") return String(v);
  return JSON.stringify(v);
}

/** Large argument sets (`setup` labware lists, multi-well `plate.load`) can't
 * be truncated — a card the operator can't fully read is a rubber stamp, and
 * the args ARE the payload the Authorize click POSTs. Past this size the card
 * switches from the inline `k=v` row to a scrollable pretty-printed block. */
function argsNeedBlock(args: Record<string, unknown>): boolean {
  return JSON.stringify(args).length > 200;
}

/** `deck.declare` with an empty `slots` map wipes the whole declaration.
 *
 * It is the one proposal whose argument table reads as a no-op while doing
 * something destructive (`slots={}`), so the card says so in words.
 */
function isDeckClear(proposal: Proposal): boolean {
  if (proposal.action !== "deck.declare") return false;
  const slots = (proposal.args ?? {})["slots"];
  return (
    typeof slots === "object" &&
    slots !== null &&
    Object.keys(slots as Record<string, unknown>).length === 0
  );
}

/** "slot=2 (OT-2 HTE · slot 2)" — the place a slot argument resolved to, in
 *  the registry's words. The operator reads the shelf, not only the key. */
function formatLocations(items: ResolvedLocation[]): string {
  return items
    .map((r) => `${r.field}=${r.value} (${r.label ?? r.location ?? "unlabelled place"})`)
    .join("; ");
}

/** "4: agilent_96_2ml_deep_square · 11: opentrons_96_tiprack_1000ul (12 tips) · 2*: empty" —
 *  the deck as the gateway sees it, touched slots starred, empty touched
 *  slots said out loud. Slots sort numerically. */
function formatDeck(check: DeckCheck): string {
  const slots = new Set([...Object.keys(check.slots), ...check.touched_slots]);
  const order = (a: string, b: string) => {
    const na = Number(a), nb = Number(b);
    return Number.isNaN(na) || Number.isNaN(nb) ? a.localeCompare(b) : na - nb;
  };
  return [...slots]
    .sort(order)
    .map((slot) => {
      const info = check.slots[slot];
      const mark = check.touched_slots.includes(slot) ? "*" : "";
      const what = info?.labware ?? (info?.tips_available !== undefined ? "tip rack" : "empty");
      const tips = info?.tips_available !== undefined ? ` (${info.tips_available} tips)` : "";
      return `${slot}${mark}: ${what}${tips}`;
    })
    .join(" · ");
}

/** The deck-check block shared by the proposal and plan cards: one "Deck now"
 *  row per device plus the request to check the physical deck. An OT-2 that
 *  could not be read is said so, not hidden — the check is then by eye. */
function DeckCheckRows({ checks }: { checks: DeckCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <>
      <dl className="mt-1 space-y-1 text-[13px] text-ink dark:text-slate-100">
        {checks.map((c) =>
          c.unreachable ? (
            <Row
              key={c.equipment_id}
              label="Deck now"
              value={`${c.equipment_id}: could not be read (${c.unreachable})`}
            />
          ) : (
            <Row key={c.equipment_id} label="Deck now" value={`${c.equipment_id} · ${formatDeck(c)}`} />
          )
        )}
      </dl>
      <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-300">
        Check the physical deck matches this before authorizing. Slots marked * are touched by
        this action.
      </p>
    </>
  );
}

function ProposalCard({
  proposal,
  expired,
  authorizing,
  error,
  onAuthorize,
  onDismiss,
}: {
  proposal: Proposal;
  expired: boolean;
  authorizing: boolean;
  error: string | null;
  onAuthorize: () => void;
  onDismiss: () => void;
}) {
  const argEntries = Object.entries(proposal.args ?? {});
  const blockArgs = argEntries.length > 0 && argsNeedBlock(proposal.args ?? {});
  const clearsDeck = isDeckClear(proposal);
  return (
    <div className="rounded-lg border border-purple-300 bg-purple-50 p-2 text-[13px] dark:border-purple-700 dark:bg-purple-950/40">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[13px] font-semibold text-purple-900 dark:text-purple-100">
          Authorize action
        </span>
        <span className="text-xs text-purple-700 dark:text-purple-300">
          proposed to {proposal.actor}
        </span>
      </div>
      {/* Authoritative fields first; the model's reason is subordinate. */}
      <dl className="space-y-1 text-[13px] text-ink dark:text-slate-100">
        <Row label="Device" value={`${proposal.equipment_name} (${proposal.equipment_id})`} />
        <Row label="Action" value={proposal.action} />
        {argEntries.length > 0 && !blockArgs && (
          <Row
            label="Args"
            value={argEntries.map(([k, v]) => `${k}=${formatArg(v)}`).join(", ")}
          />
        )}
        {blockArgs && (
          <div className="flex gap-2">
            <dt className="w-20 shrink-0 text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-400">
              Args
            </dt>
            <dd className="min-w-0 flex-1">
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-purple-100/60 p-1.5 font-mono text-xs leading-snug dark:bg-purple-900/30">
                {JSON.stringify(proposal.args, null, 2)}
              </pre>
            </dd>
          </div>
        )}
        {(proposal.resolved_locations?.length ?? 0) > 0 && (
          <Row label="Place" value={formatLocations(proposal.resolved_locations ?? [])} />
        )}
        <Row
          label="Device state"
          value={`${proposal.device_state.equipment_status} · ${proposal.device_state.activity}`}
        />
      </dl>
      <DeckCheckRows checks={proposal.deck_checks ?? []} />
      {clearsDeck && (
        <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-300">
          Clears the entire deck declaration — every slot is unset.
        </p>
      )}
      {proposal.reason && (
        <p className="mt-1 text-xs italic text-purple-800 dark:text-purple-300">
          {proposal.reason}
        </p>
      )}
      {error && (
        <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{error}</p>
      )}
      {expired && !error && (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          This proposal expired. Ask again to get a fresh one.
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onAuthorize}
          disabled={authorizing || expired}
          className="rounded bg-purple-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700"
        >
          {authorizing ? "Authorizing…" : "Authorize"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={authorizing}
          className="rounded px-2 py-1 text-xs text-ink-subtle hover:bg-purple-100 disabled:opacity-60 dark:text-slate-300 dark:hover:bg-purple-900/40"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

const STEP_GLYPH: Record<StepOutcome, string> = {
  pending: "◦",
  running: "↻",
  ok: "✓",
  failed: "×",
  skipped: "–",
};

const STEP_TONE: Record<StepOutcome, string> = {
  pending: "text-ink dark:text-slate-200",
  running: "text-purple-800 dark:text-purple-300 animate-pulse",
  ok: "text-emerald-700 dark:text-emerald-400",
  failed: "text-rose-700 dark:text-rose-400",
  skipped: "text-slate-400 line-through dark:text-slate-400",
};

/** The multi-step sibling of ProposalCard (UI_DESIGN §5 Step 1i). Same
 *  authoritative-fields-first layout; the whole ordered list is reviewed at
 *  once and approved by hash, then run step by step from this browser. Two
 *  clicks — Approve, then Run — not one: approving records *what was
 *  reviewed*, which is what makes the audit row meaningful, and it keeps the
 *  last action before hardware moves from being a single click on a screen
 *  nobody read (same reasoning as the OT-2 gateway's plan panel). */
function PlanCard({
  plan,
  run,
  onApprove,
  onRun,
  onDismiss,
}: {
  plan: Plan;
  run: PlanRun;
  onApprove: () => void;
  onRun: () => void;
  onDismiss: () => void;
}) {
  const busy = run.phase === "approving" || run.phase === "running";
  const settled = run.phase === "executed" || run.phase === "failed";
  const okCount = run.outcomes.filter((o) => o === "ok").length;
  const title =
    run.phase === "executed"
      ? "Plan finished"
      : run.phase === "failed"
        ? "Plan halted"
        : run.phase === "running"
          ? "Running plan"
          : run.phase === "approved"
            ? "Plan approved"
            : "Authorize plan";
  return (
    <div className="rounded-lg border border-purple-300 bg-purple-50 p-2 text-[13px] dark:border-purple-700 dark:bg-purple-950/40">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[13px] font-semibold text-purple-900 dark:text-purple-100">
          {title} · {plan.steps.length} steps
        </span>
        <span className="text-xs text-purple-700 dark:text-purple-300">
          proposed to {plan.actor}
        </span>
      </div>
      <dl className="space-y-1 text-[13px] text-ink dark:text-slate-100">
        <Row label="Device" value={`${plan.equipment_name} (${plan.equipment_id})`} />
        <Row
          label="Device state"
          value={`${plan.device_state.equipment_status} · ${plan.device_state.activity}`}
        />
      </dl>
      <DeckCheckRows checks={plan.deck_checks ?? []} />
      <ol className="mt-1 flex flex-col gap-1" aria-label="plan steps">
        {plan.steps.map((s, i) => {
          const outcome = run.outcomes[i] ?? "pending";
          const entries = Object.entries(s.args ?? {});
          const block = entries.length > 0 && argsNeedBlock(s.args ?? {});
          return (
            <li key={i} className={`text-[13px] leading-snug ${STEP_TONE[outcome]}`}>
              {STEP_GLYPH[outcome]} {i + 1}. {s.action}
              {entries.length > 0 && !block && (
                <span className="text-ink-subtle dark:text-slate-300">
                  {" "}
                  {entries.map(([k, v]) => `${k}=${formatArg(v)}`).join(", ")}
                </span>
              )}
              {block && (
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-purple-100/60 p-1.5 font-mono text-xs leading-snug dark:bg-purple-900/30">
                  {JSON.stringify(s.args, null, 2)}
                </pre>
              )}
              {(() => {
                const places = (plan.resolved_locations ?? []).filter((r) => r.step === i + 1);
                return places.length > 0 ? (
                  <span className="ml-1 text-xs text-purple-800 dark:text-purple-300">
                    [{formatLocations(places)}]
                  </span>
                ) : null;
              })()}
              {run.messages[i] && (
                <span className="ml-1 text-rose-700 dark:text-rose-400">
                  {run.messages[i]}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      {plan.reason && (
        <p className="mt-1 text-xs italic text-purple-800 dark:text-purple-300">
          {plan.reason}
        </p>
      )}
      {run.error && (
        <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{run.error}</p>
      )}
      {run.haltReason && (
        <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">
          Halted: {run.haltReason}. Remaining steps were not sent.
        </p>
      )}
      {run.phase === "executed" && (
        <p className="mt-1 text-xs text-emerald-700 dark:text-emerald-300">
          All {okCount} steps ran.
        </p>
      )}
      {run.expired && run.phase === "draft" && !run.error && (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          This plan expired. Ask again to get a fresh one.
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        {run.phase === "draft" || run.phase === "approving" ? (
          <button
            type="button"
            onClick={onApprove}
            disabled={busy || run.expired}
            className="rounded bg-purple-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700"
          >
            {run.phase === "approving"
              ? "Approving…"
              : `Approve these ${plan.steps.length} steps`}
          </button>
        ) : null}
        {run.phase === "approved" || run.phase === "running" ? (
          <button
            type="button"
            onClick={onRun}
            disabled={busy}
            className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700"
          >
            {run.phase === "running" ? `Running ${okCount + 1}/${plan.steps.length}…` : "Run"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onDismiss}
          disabled={busy}
          className="rounded px-2 py-1 text-xs text-ink-subtle hover:bg-purple-100 disabled:opacity-60 dark:text-slate-300 dark:hover:bg-purple-900/40"
        >
          {settled ? "Close" : "Dismiss"}
        </button>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-400">
        {label}
      </dt>
      <dd className="break-words text-[13px]">{value}</dd>
    </div>
  );
}

/** Same pill family as the OT-2 panel assistant (`opentrons-server` ToolPills):
 *  running = pulsing purple, succeeded = emerald, so a finished tool call is
 *  the green chip operators already recognize on that surface. */
const TOOL_PILL_TONE = {
  running:
    "border-purple-300 bg-purple-50 text-purple-800 dark:border-purple-700 dark:bg-purple-950/50 dark:text-purple-300",
  succeeded:
    "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
  // A call that never reported back because the turn ended first (abort, or
  // a reload mid-flight). It is NOT running, so it must not keep pulsing as
  // though it were.
  stopped:
    "border-slate-300 bg-slate-100 text-slate-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300",
} as const;

/** Absolute http(s) URLs and the API's own paths, as a model writes them. */
// Backticks and trailing sentence punctuation are excluded: models wrap a
// path in `code` or end a sentence with it, and either would break the link.
const LINK_RE = /(https?:\/\/[^\s<>"'`)\]]+?(?=[.,;:!?]*(?:[\s<>"'`)\]]|$))|\/api\/[^\s<>"'`)\]]+?(?=[.,;:!?]*(?:[\s<>"'`)\]]|$)))/g;

/** Plain reply text with its URLs made clickable. The bubble renders answers
 *  as plain text (no markdown), so a `/api/assistant/snapshots/…` path or an
 *  http link the model writes would otherwise be dead text — and the
 *  snapshot link is the operator's fallback when a picture does not render. */
function linkify(text: string): ReactNode {
  const parts = text.split(LINK_RE);
  if (parts.length === 1) return text;
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <a
        key={i}
        href={part}
        target="_blank"
        rel="noreferrer"
        className="underline decoration-dotted underline-offset-2 hover:decoration-solid"
      >
        {part}
      </a>
    ) : (
      part
    )
  );
}

function toolLabel(name: string): string {
  return name.replaceAll("_", " ");
}

/** Seconds a pill has been in flight, shown only once the wait is long
 *  enough to be worth reporting. A sub-2 s pill flashing "1s" is noise; the
 *  counter exists for the 30 s+ reasoning rounds that prompted all this. */
function elapsedLabel(startedAt: number | undefined, now: number | undefined): string {
  if (!startedAt || now === undefined) return "";
  const s = Math.floor((now - startedAt) / 1000);
  return s >= 2 ? ` ${s}s` : "";
}

/** The turn's progress row: one pill per tool call, oldest first, plus a
 *  pill for the current no-visible-token phase when there is one. Names
 *  only — arguments and results stay out of the transcript deliberately. */
function ToolPills({
  tools,
  phase,
  phaseSince,
  phaseLabel,
  now,
  live = false,
  className = "",
}: {
  tools: ToolCall[];
  phase?: Phase | null;
  phaseSince?: number;
  /** Stage label under the current phase — shown on the pill so it reads
   *  "waiting…"/"reasoning…" instead of a static "thinking". */
  phaseLabel?: string;
  now?: number;
  /** Whether this turn is still streaming. Gates both the pulse and the
   *  elapsed counter — neither means anything once the turn has ended. */
  live?: boolean;
  className?: string;
}) {
  const items: {
    key: string;
    label: string;
    ok: boolean;
    startedAt?: number;
  }[] = tools.map((t, i) => ({
    key: `${t.name}-${i}`,
    label: toolLabel(t.name),
    ok: t.ok,
    startedAt: t.startedAt,
  }));
  // The phase pill trails the tools it precedes in time: a tool that has
  // already returned reads as history, and the live pill belongs at the end
  // of the row where the eye lands.
  if (phase === "thinking") {
    items.push({
      key: "phase-thinking",
      label: phaseLabel ?? "thinking",
      ok: false,
      startedAt: phaseSince,
    });
  }
  if (items.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {items.map((t) => {
        const running = !t.ok && live;
        const tone = t.ok
          ? TOOL_PILL_TONE.succeeded
          : running
            ? TOOL_PILL_TONE.running
            : TOOL_PILL_TONE.stopped;
        return (
          <span
            key={t.key}
            title={
              t.ok
                ? "Tool finished"
                : running
                  ? "Working…"
                  : "Did not finish — the turn ended first"
            }
            className={`rounded-full border px-2 py-0.5 text-xs font-medium ${tone} ${
              running ? "animate-pulse" : ""
            }`}
          >
            {t.ok ? "✓" : running ? "↻" : "◦"} {t.label}
            {running ? elapsedLabel(t.startedAt, now) : ""}
          </span>
        );
      })}
    </div>
  );
}

function Turn({
  turn,
  live = false,
  now,
}: {
  turn: ChatTurn;
  /** True only for the turn currently streaming. Gates the phase pill, so a
   *  phase left behind by an aborted or reloaded turn can never render as a
   *  pill that pulses forever. */
  live?: boolean;
  now?: number;
}) {
  const isUser = turn.role === "user";
  // While a pill is pulsing it already says "working"; the placeholder
  // ellipsis underneath would just be a second, worse spinner.
  const livePill =
    live && (turn.phase === "thinking" || turn.tools.some((t) => !t.ok));
  // Accent follows the mode the turn was SENT under, so flipping the toggle
  // never repaints history — a purple bubble is a durable "this was said in
  // Control mode" marker, in the same spirit as the audit trail.
  const userBg =
    turn.mode === "control"
      ? "bg-purple-600 text-white"
      : "bg-emerald-600 text-white";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] break-words rounded-lg px-3 py-2 text-[13px] leading-relaxed ${
          isUser
            ? userBg
            : "bg-slate-100 text-ink dark:bg-slate-800 dark:text-slate-100"
        }`}
      >
        {(turn.text || !livePill) && (
          <span className="whitespace-pre-wrap">
            {turn.text ? (
              isUser ? turn.text : linkify(turn.text)
            ) : (
              <span className="opacity-60">{isUser ? "" : "…"}</span>
            )}
          </span>
        )}
        {turn.images?.map((im, i) => (
          <figure key={`${im.url}-${i}`} className="mt-1.5">
            <a href={im.url} target="_blank" rel="noreferrer" title="Open full size">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={im.url}
                alt={`${im.camera_name ?? im.camera_id}${im.lens ? ` (${im.lens})` : ""} snapshot`}
                loading="lazy"
                className="max-h-64 w-auto max-w-full rounded border border-slate-300 dark:border-slate-600"
              />
            </a>
            <figcaption className="mt-0.5 text-[11px] text-ink-subtle dark:text-slate-400">
              {im.camera_name ?? im.camera_id}
              {im.lens ? ` · ${im.lens}` : ""}
              {im.taken_at ? ` · ${new Date(im.taken_at).toLocaleTimeString()}` : ""}
            </figcaption>
          </figure>
        ))}
        {turn.refusal && (
          <div className="mt-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-[12px] text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
            Proposal refused ({turn.refusal.code}): {turn.refusal.message}
          </div>
        )}
        {turn.declined && (
          <div className="mt-1.5 rounded border border-slate-300 bg-slate-50 px-2 py-1 text-[12px] text-slate-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-400">
            No action proposed — {turn.declined.explanation}
          </div>
        )}
        {turn.stopped && (
          <div className="mt-1.5 rounded border border-slate-300 bg-slate-50 px-2 py-1 text-[12px] text-slate-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-400">
            Stopped by you — nothing further came from this turn.
          </div>
        )}
        {/* Progress last, at the bottom of the bubble: the chat auto-scrolls
            to its end, so on a long reply the pills stay in view where the
            eye is instead of scrolling off with the top of the answer. */}
        <ToolPills
          tools={turn.tools}
          phase={live ? turn.phase : null}
          phaseSince={turn.phaseSince}
          phaseLabel={live ? turn.phaseLabel : undefined}
          now={now}
          live={live}
          className={turn.text || turn.images?.length ? "mt-1.5" : ""}
        />
      </div>
    </div>
  );
}
