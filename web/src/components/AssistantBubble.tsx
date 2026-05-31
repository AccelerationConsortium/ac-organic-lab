"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Floating bottom-right chat bubble that talks to the dashboard's
 * `/api/assistant/chat` endpoint. The endpoint streams Server-Sent Events:
 *
 *   data: {"type":"text","delta":"..."}
 *   data: {"type":"tool_use","name":"...","input":{...}}
 *   data: {"type":"tool_result","name":"..."}
 *   data: {"type":"done"}
 *   data: {"type":"error","message":"..."}
 *
 * The bubble is read-only -- the backend tools can only query the history DB
 * and tail whitelisted systemd units, never actuate hardware. The UI mirrors
 * that by not exposing any "do X" verbs.
 */

type Role = "user" | "assistant";

interface ChatTurn {
  role: Role;
  /** Plain text content. For assistants, accumulates as `text` deltas arrive. */
  text: string;
  /** Tool calls observed during this assistant turn, oldest first. */
  tools: { name: string; ok: boolean }[];
}

const STORAGE_KEY = "ac-assistant-history-v1";
const POSITION_KEY = "ac-assistant-position-v1";
const MAX_STORED_TURNS = 20;
const PANEL_W = 380;
const PANEL_H = 520;
// How much of the header must stay on screen so the user can always grab it
// to drag the panel back into view.
const MIN_VISIBLE = 80;

/**
 * Default placement: tucked into the bottom-right corner just above the
 * launcher bubble, mirroring the prior fixed `bottom-20 right-5` Tailwind
 * classes. Computed lazily because `window` isn't available during SSR.
 */
function defaultPosition(): { x: number; y: number } {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  return {
    x: Math.max(0, window.innerWidth - PANEL_W - 20),
    y: Math.max(0, window.innerHeight - PANEL_H - 80),
  };
}

export function AssistantBubble() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // null = still checking; once resolved, we know whether to render at all.
  // The bubble only renders if the backend's health endpoint reports it can
  // spawn `claude` (the Claude Code CLI subprocess backend). If the CLI
  // isn't installed on the dashboard host, the lab tooling is still
  // reachable directly via the `lab-history` MCP server in a terminal
  // session of Claude Code.
  const [configured, setConfigured] = useState<boolean | null>(null);
  // Pixel coords of the panel's top-left corner. Null while the user hasn't
  // dragged yet -- we compute a default on first open from the viewport
  // size. Persisted to sessionStorage so the panel stays where the user
  // last left it within one tab.
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Captured at pointerdown; (px,py) = pointer at start, (bx,by) = panel
  // top-left at start. Cleared on pointerup.
  const dragStartRef = useRef<
    null | { px: number; py: number; bx: number; by: number }
  >(null);

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
        if (!cancelled) setConfigured(Boolean(body?.configured));
      } catch {
        if (!cancelled) setConfigured(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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

  // Pointer drag handlers. Attaches global pointermove/pointerup on
  // pointerdown so the drag keeps tracking even if the pointer leaves the
  // header element.
  const onDragStart = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Let header buttons (minimize / clear) handle their own clicks.
      if ((e.target as HTMLElement).closest("button")) return;
      e.preventDefault();
      const base = position ?? defaultPosition();
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
        const maxX = Math.max(0, window.innerWidth - MIN_VISIBLE);
        const maxY = Math.max(0, window.innerHeight - 40);
        const minX = -(PANEL_W - MIN_VISIBLE);
        setPosition({
          x: Math.max(minX, Math.min(maxX, start.bx + dx)),
          y: Math.max(0, Math.min(maxY, start.by + dy)),
        });
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
    [position]
  );

  // Auto-scroll to bottom when content lands.
  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [turns, open]);

  // Cancel any in-flight stream when the panel closes or the component unmounts.
  useEffect(() => {
    if (!open && abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
      setSending(false);
    }
  }, [open]);
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      setError(null);

      const nextTurns: ChatTurn[] = [
        ...turns,
        { role: "user", text: trimmed, tools: [] },
        { role: "assistant", text: "", tools: [] },
      ];
      setTurns(nextTurns);
      setInput("");
      setSending(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await fetch("/api/assistant/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
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
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError((e as Error).message);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setSending(false);
      }
    },
    [turns, sending]
  );

  const applyEvent = useCallback(
    (event: { type: string; [k: string]: unknown }) => {
      setTurns((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.role !== "assistant") return prev;
        const updated: ChatTurn = { ...last };
        if (event.type === "text" && typeof event.delta === "string") {
          updated.text = (updated.text || "") + event.delta;
        } else if (event.type === "tool_use" && typeof event.name === "string") {
          updated.tools = [...updated.tools, { name: event.name, ok: false }];
        } else if (event.type === "tool_result" && typeof event.name === "string") {
          // Mark the most recent matching tool as completed.
          const idx = [...updated.tools]
            .reverse()
            .findIndex((t) => t.name === event.name && !t.ok);
          if (idx >= 0) {
            const realIdx = updated.tools.length - 1 - idx;
            updated.tools = updated.tools.map((t, i) =>
              i === realIdx ? { ...t, ok: true } : t
            );
          }
        } else if (event.type === "error" && typeof event.message === "string") {
          setError(event.message);
        }
        return [...prev.slice(0, -1), updated];
      });
    },
    []
  );

  const clearHistory = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTurns([]);
    setError(null);
    sessionStorage.removeItem(STORAGE_KEY);
  }, []);

  // Suppress the launcher entirely if the dashboard host has no API key.
  // The MCP path (Claude Code) is the supported alternative; see the deploy
  // README for `claude mcp add`.
  if (configured !== true) return null;

  return (
    <>
      {/* Floating launcher */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close lab assistant" : "Open lab assistant"}
        className="fixed bottom-5 right-5 z-50 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-600 text-white shadow-lg transition hover:bg-emerald-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2"
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
            left: (position ?? defaultPosition()).x,
            top: (position ?? defaultPosition()).y,
          }}
          className="fixed z-50 flex h-[520px] w-[380px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-xl border border-slate-200 bg-surface-raised shadow-2xl dark:border-slate-700 dark:bg-slate-900"
        >
          <header
            onPointerDown={onDragStart}
            className={`flex items-center justify-between gap-2 border-b border-slate-200 px-3 py-2 dark:border-slate-700 ${
              dragging ? "cursor-grabbing" : "cursor-grab"
            } select-none touch-none`}
            title="Drag to move"
          >
            <div className="flex flex-col">
              <span className="text-sm font-semibold text-ink dark:text-slate-100">
                Lab Assistant
              </span>
              <span className="text-[10px] text-ink-subtle dark:text-slate-500">
                Read-only · history + journald
              </span>
            </div>
            <div className="flex items-center gap-1">
              {turns.length > 0 && (
                <button
                  type="button"
                  onClick={clearHistory}
                  className="rounded px-2 py-1 text-[11px] text-ink-subtle hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                >
                  Clear
                </button>
              )}
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
            className="flex-1 space-y-3 overflow-y-auto px-3 py-3 text-sm"
          >
            {turns.length === 0 && (
              <div className="text-xs text-ink-subtle dark:text-slate-500">
                Ask about lab equipment history — for example:
                <ul className="mt-2 list-disc space-y-1 pl-4">
                  <li>Has the plateloc had any errors today?</li>
                  <li>Which devices are unreachable right now?</li>
                  <li>What does the API log look like over the past hour?</li>
                </ul>
              </div>
            )}
            {turns.map((turn, i) => (
              <Turn key={i} turn={turn} />
            ))}
            {error && (
              <div className="rounded border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-800 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200">
                {error}
              </div>
            )}
          </div>

          <form
            className="border-t border-slate-200 px-3 py-2 dark:border-slate-700"
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
                placeholder={sending ? "Working…" : "Ask about the lab…"}
                rows={2}
                disabled={sending}
                className="flex-1 resize-none rounded border border-slate-300 bg-white px-2 py-1 text-sm text-ink shadow-inner focus:border-emerald-500 focus:outline-none disabled:opacity-60 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
              />
              <button
                type="submit"
                disabled={sending || !input.trim()}
                className="self-stretch rounded bg-emerald-600 px-3 text-sm font-medium text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700"
              >
                Send
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}

function Turn({ turn }: { turn: ChatTurn }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-[13px] leading-relaxed ${
          isUser
            ? "bg-emerald-600 text-white"
            : "bg-slate-100 text-ink dark:bg-slate-800 dark:text-slate-100"
        }`}
      >
        {turn.tools.length > 0 && (
          <div className="mb-1 space-y-0.5">
            {turn.tools.map((t, i) => (
              <div
                key={i}
                className="text-[10px] font-mono opacity-70"
                title={t.ok ? "Tool finished" : "Tool running"}
              >
                {t.ok ? "✓" : "•"} {t.name}
              </div>
            ))}
          </div>
        )}
        {turn.text || (
          <span className="opacity-60">{isUser ? "" : "…"}</span>
        )}
      </div>
    </div>
  );
}
