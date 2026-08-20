"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, authorizeAssistantAction } from "@/lib/api";
import { useUserAuth } from "@/lib/user-auth";

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

interface ChatTurn {
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
}

/** A validated, propose-only action from the lab-control MCP server. */
interface Proposal {
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
}

type Mode = "ask" | "control";

const STORAGE_KEY = "ac-assistant-history-v1";
const POSITION_KEY = "ac-assistant-position-v1";
const SIZE_KEY = "ac-assistant-size-v1";
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
  return <AssistantBubbleInner />;
}

function AssistantBubbleInner() {
  const { authenticated, identity } = useUserAuth();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
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
  // Ask (read-only) vs Control (propose-only). Deliberately NOT persisted:
  // resets to Ask on reload / panel close (UI_DESIGN §5.2). The server decides
  // the real toolset from the verified identity regardless of this value.
  const [mode, setMode] = useState<Mode>("ask");
  // null = not yet resolved. True when the signed-in user holds a role on at
  // least one equipment (operator+), from /api/auth/mine + identity role.
  const [controlEligible, setControlEligible] = useState<boolean | null>(null);
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
  const expiryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  // Restore prior conversation on mount (per-tab; sessionStorage clears on close).
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) setTurns(parsed);
      }
    } catch {
      /* ignore corrupt cache */
    }
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(turns.slice(-MAX_STORED_TURNS))
      );
    } catch {
      /* quota / private mode */
    }
  }, [turns]);

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
    }
  }, [open, clearProposal]);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (expiryRef.current) clearTimeout(expiryRef.current);
    };
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      setError(null);
      // A new turn supersedes any pending proposal / result banner.
      clearProposal();
      setAuthorizeResult(null);
      setAuthorizeResponse(null);

      const nextTurns: ChatTurn[] = [
        ...turns,
        { role: "user", text: trimmed, tools: [], mode },
        { role: "assistant", text: "", tools: [], mode },
      ];
      setTurns(nextTurns);
      setInput("");
      setSending(true);

      const controller = new AbortController();
      abortRef.current = controller;
      terminatedRef.current = false;

      try {
        const res = await fetch("/api/assistant/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            mode,
            messages: nextTurns
              .filter((t) => t.role === "user" || (t.role === "assistant" && t.text))
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
          setError(
            "Connection lost — the assistant stopped responding before it finished. Check the service, then try again."
          );
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError((e as Error).message);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setSending(false);
      }
    },
    [turns, sending, mode, clearProposal]
  );

  const applyEvent = useCallback(
    (event: { type: string; [k: string]: unknown }) => {
      // A control proposal is not part of an assistant text turn — surface it
      // as a confirm card rather than folding it into the transcript.
      if (event.type === "proposal" && event.proposal && typeof event.proposal === "object") {
        const p = event.proposal as Proposal;
        setAuthorizeError(null);
        setAuthorizeResult(null);
        setAuthorizeResponse(null);
        setProposalExpired(false);
        setProposal(p);
        if (expiryRef.current) clearTimeout(expiryRef.current);
        const ttlMs = Math.max(5, Number(p.expires_in_s) || 120) * 1000;
        expiryRef.current = setTimeout(() => setProposalExpired(true), ttlMs);
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
        } else if (event.type === "done") {
          // Natural end of a completed turn. Marks the run as terminated so
          // the stream-end check below knows it finished, not got cut.
          terminatedRef.current = true;
          updated.phase = null;
          updated.phaseLabel = undefined;
        } else if (event.type === "error" && typeof event.message === "string") {
          terminatedRef.current = true;
          setError(event.message);
        }
        return [...prev.slice(0, -1), updated];
      });
    },
    []
  );

  const authorizeProposal = useCallback(async () => {
    if (!proposal || authorizing || proposalExpired) return;
    setAuthorizing(true);
    setAuthorizeError(null);
    try {
      const response = await authorizeAssistantAction(
        proposal.equipment_id,
        proposal.passthrough_action,
        proposal.args
      );
      setAuthorizeResult(
        `Authorized ${proposal.action} on ${proposal.equipment_name}.`
      );
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
    } finally {
      setAuthorizing(false);
    }
  }, [proposal, authorizing, proposalExpired, clearProposal]);

  const clearHistory = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTurns([]);
    setError(null);
    clearProposal();
    setAuthorizeResult(null);
  }, [clearProposal]);

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
        aria-label={open ? "Close lab assistant" : "Open lab assistant"}
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
          aria-label="Lab assistant"
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
              <span className="text-sm font-semibold text-ink dark:text-slate-100">
                Lab Assistant
              </span>
              <span className="text-[10px] text-ink-subtle dark:text-slate-500">
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
              <button
                type="button"
                onClick={clearHistory}
                disabled={turns.length === 0}
                title="Clear the conversation (proposals and authorized actions stay in the audit trail)"
                className="rounded border border-slate-300 px-2 py-1 text-[11px] font-medium text-ink-subtle transition hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Minimize to bubble"
                title="Minimize"
                className="rounded px-2 py-1 text-[14px] leading-none text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
              >
                {/* Minus glyph -- intentionally not ✕, since the conversation
                    persists when the panel collapses back to the bubble. */}
                &minus;
              </button>
            </div>
          </header>

          <div
            ref={scrollerRef}
            className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3 text-base"
          >
            {turns.length === 0 && (
              <div className="text-xs text-ink-subtle dark:text-slate-500">
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
            {authorizeResult && (
              <div className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200">
                {authorizeResult}
              </div>
            )}
            {authorizeResponse && (
              <div className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200">
                <div className="mb-1 font-medium">Device response</div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-emerald-100/70 p-1.5 font-mono text-[10px] leading-snug dark:bg-emerald-900/30">
                  {JSON.stringify(authorizeResponse, null, 2)}
                </pre>
              </div>
            )}
            {error && (
              <div className="rounded border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-800 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200">
                {error}
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
                className={`flex-1 resize-none rounded border border-slate-300 bg-white px-2 py-1 text-base text-ink shadow-inner focus:outline-none disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 ${focusClass}`}
              />
              <button
                type="submit"
                disabled={sending || !input.trim()}
                className={`self-stretch rounded px-3 text-sm font-medium text-white shadow-sm transition disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700 ${sendClass}`}
              >
                Send
              </button>
            </div>
            {backendInfo?.model && (
              <p className="mt-1 text-center text-sm text-ink-subtle dark:text-slate-500">
                model: {backendInfo.model} · Hermes agents
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
      // Sized to match the Clear chip beside it (text-[11px], py-1) so the
      // header buttons read as one control family at equal height.
      className="mr-1 flex overflow-hidden rounded border border-slate-300 text-[11px] font-medium dark:border-slate-600"
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
            : "text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
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
              : "text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
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
    <div className="rounded-lg border border-purple-300 bg-purple-50 p-2 text-[12px] dark:border-purple-700 dark:bg-purple-950/40">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold text-purple-900 dark:text-purple-100">
          Authorize action
        </span>
        <span className="text-[10px] text-purple-700 dark:text-purple-300">
          proposed to {proposal.actor}
        </span>
      </div>
      {/* Authoritative fields first; the model's reason is subordinate. */}
      <dl className="space-y-0.5 text-ink dark:text-slate-100">
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
            <dt className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-ink-subtle dark:text-slate-500">
              Args
            </dt>
            <dd className="min-w-0 flex-1">
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-purple-100/60 p-1.5 font-mono text-[10px] leading-snug dark:bg-purple-900/30">
                {JSON.stringify(proposal.args, null, 2)}
              </pre>
            </dd>
          </div>
        )}
        <Row
          label="Device state"
          value={`${proposal.device_state.equipment_status} · ${proposal.device_state.activity}`}
        />
      </dl>
      {clearsDeck && (
        <p className="mt-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
          Clears the entire deck declaration — every slot is unset.
        </p>
      )}
      {proposal.reason && (
        <p className="mt-1 text-[11px] italic text-purple-800 dark:text-purple-300">
          {proposal.reason}
        </p>
      )}
      {error && (
        <p className="mt-1 text-[11px] text-rose-700 dark:text-rose-300">{error}</p>
      )}
      {expired && !error && (
        <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
          This proposal expired. Ask again to get a fresh one.
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onAuthorize}
          disabled={authorizing || expired}
          className="rounded bg-purple-600 px-3 py-1 text-[11px] font-medium text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700"
        >
          {authorizing ? "Authorizing…" : "Authorize"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={authorizing}
          className="rounded px-2 py-1 text-[11px] text-ink-subtle hover:bg-purple-100 disabled:opacity-60 dark:text-slate-400 dark:hover:bg-purple-900/40"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-ink-subtle dark:text-slate-500">
        {label}
      </dt>
      <dd className="break-words font-mono text-[11px]">{value}</dd>
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
    "border-slate-300 bg-slate-100 text-slate-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-400",
} as const;

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
            className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${tone} ${
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
        <ToolPills
          tools={turn.tools}
          phase={live ? turn.phase : null}
          phaseSince={turn.phaseSince}
          phaseLabel={live ? turn.phaseLabel : undefined}
          now={now}
          live={live}
          className="mb-1.5"
        />
        {(turn.text || !livePill) && (
          <span className="whitespace-pre-wrap">
            {turn.text || (
              <span className="opacity-60">{isUser ? "" : "…"}</span>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
