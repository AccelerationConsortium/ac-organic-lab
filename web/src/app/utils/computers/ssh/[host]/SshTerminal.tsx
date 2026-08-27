"use client";

import { useCallback, useEffect, useRef, useState } from "react";
// Type-only: erased at compile time, so this costs no runtime import (the
// value import happens lazily in the effect below).
import type { FitAddon } from "@xterm/addon-fit";
import type { Terminal } from "@xterm/xterm";

// Side-effect import: the bundler extracts this at build time, so it costs no
// runtime `document` access (unlike the xterm module itself, which is loaded
// lazily below for exactly that reason).
import "@xterm/xterm/css/xterm.css";

import { ApiError } from "@/lib/api";
import {
  openSshSession,
  sshSocketUrl,
  type SshProfile,
  type SshServerFrame,
} from "@/lib/ssh-api";

type Phase = "idle" | "connecting" | "open" | "closed" | "error";

const PHASE_LABEL: Record<Phase, string> = {
  idle: "Not connected",
  connecting: "Connecting…",
  open: "Connected",
  closed: "Session ended",
  error: "Failed",
};

const PHASE_DOT: Record<Phase, string> = {
  idle: "bg-slate-400",
  connecting: "bg-amber-400",
  open: "bg-emerald-500",
  closed: "bg-slate-400",
  error: "bg-rose-500",
};

/**
 * A live shell on one lab host, drawn by xterm.js and fed by
 * `/api/ssh/ws`.
 *
 * Two things are worth knowing about the wiring:
 *
 * 1. **xterm loads lazily, in an effect.** Its module touches `document` at
 *    import time, so a top-level import would break the server render. The
 *    dynamic `import()` also keeps ~300 kB out of every other page's bundle.
 * 2. **Connecting is explicit.** Opening the page does not open a shell —
 *    the operator clicks Connect. Each click mints its own single-use ticket
 *    (they expire in ~30 s), so a page left open overnight can still connect
 *    without holding a credential the whole time.
 *
 * Frames are JSON both ways so a session end can be reported in-band:
 * `{t:"i"|"r"}` up, `{t:"o"|"e"|"x"}` down (see lib/ssh-api.ts).
 *
 * `profiles` (from the host banner) renders as a segmented picker next to
 * Connect — plain shell, tmux attach-or-create, WSL on the Windows PCs. The
 * picker only ever chooses a server-side profile *id*; the command each id
 * runs lives in `api/app/ssh_console.py`'s whitelist. Locked while a session
 * is up: switching means disconnect, pick, reconnect.
 */
export function SshTerminal({
  hostId,
  hostLabel,
  profiles,
}: {
  hostId: string;
  hostLabel: string;
  profiles: SshProfile[];
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [profileId, setProfileId] = useState<string | undefined>(profiles[0]?.id);

  // -- terminal instance (once) --------------------------------------------
  useEffect(() => {
    let disposed = false;
    let term: Terminal | null = null;
    let observer: ResizeObserver | null = null;

    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed || !mountRef.current) return;

      term = new Terminal({
        convertEol: false,
        cursorBlink: true,
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
        fontSize: 13,
        scrollback: 5000,
        theme: { background: "#0b1120", foreground: "#e2e8f0", cursor: "#38bdf8" },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(mountRef.current);
      fit.fit();
      termRef.current = term;
      fitRef.current = fit;

      term.onData((data) => {
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ t: "i", d: data }));
        }
      });
      term.onResize(({ cols, rows }) => {
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ t: "r", cols, rows }));
        }
      });

      observer = new ResizeObserver(() => {
        try {
          fit.fit();
        } catch {
          /* the container can be measured mid-layout; the next tick refits */
        }
      });
      observer.observe(mountRef.current);
      term.writeln("\x1b[2mReady. Press Connect to open a session.\x1b[0m");
    })();

    return () => {
      disposed = true;
      observer?.disconnect();
      socketRef.current?.close();
      socketRef.current = null;
      term?.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  const disconnect = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
  }, []);

  const connect = useCallback(async () => {
    const term = termRef.current;
    if (!term || socketRef.current) return;
    setPhase("connecting");
    setMessage(null);

    let ticket: string;
    try {
      fitRef.current?.fit();
      const session = await openSshSession(hostId, profileId);
      ticket = session.ticket;
    } catch (err) {
      const detail =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      setPhase("error");
      setMessage(detail);
      term.writeln(`\r\n\x1b[31m${detail}\x1b[0m`);
      return;
    }

    const socket = new WebSocket(sshSocketUrl(ticket, term.cols, term.rows));
    socketRef.current = socket;

    socket.onopen = () => {
      setPhase("open");
      socket.send(JSON.stringify({ t: "r", cols: term.cols, rows: term.rows }));
      term.focus();
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      let frame: SshServerFrame;
      try {
        frame = JSON.parse(event.data) as SshServerFrame;
      } catch {
        return;
      }
      if (frame.t === "o") {
        term.write(frame.d);
      } else if (frame.t === "e") {
        setPhase("error");
        setMessage(frame.d);
        term.writeln(`\r\n\x1b[31m${frame.d}\x1b[0m`);
      } else if (frame.t === "x") {
        const how = frame.outcome === "idle_timeout" ? "idle timeout" : frame.outcome;
        term.writeln(
          `\r\n\x1b[2m· session ended (${how}, exit ${frame.code ?? "n/a"}, ${frame.duration_s}s)\x1b[0m`,
        );
      }
    };
    socket.onclose = () => {
      socketRef.current = null;
      setPhase((current) => (current === "error" ? current : "closed"));
    };
    socket.onerror = () => {
      // `onclose` always follows, and it carries no useful reason for a
      // failed handshake — so say the one thing that is actually knowable.
      setMessage((current) => current ?? "WebSocket connection failed.");
    };
  }, [hostId, profileId]);

  const connected = phase === "open" || phase === "connecting";

  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs text-ink-subtle dark:text-slate-400">
          <span className={`h-2 w-2 rounded-full ${PHASE_DOT[phase]}`} aria-hidden />
          {PHASE_LABEL[phase]}
        </span>
        {profiles.length > 1 && (
          <div
            className="inline-flex overflow-hidden rounded-md border border-slate-300 dark:border-slate-700"
            role="radiogroup"
            aria-label="Session type"
          >
            {profiles.map((profile) => {
              const active = profile.id === profileId;
              return (
                <button
                  key={profile.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  title={profile.description}
                  disabled={connected}
                  onClick={() => setProfileId(profile.id)}
                  className={`px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                    active
                      ? "bg-sky-100 text-sky-900 dark:bg-sky-900/60 dark:text-sky-100"
                      : "bg-white text-ink hover:bg-surface-subtle dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  }`}
                >
                  {profile.label}
                </button>
              );
            })}
          </div>
        )}
        <button
          type="button"
          onClick={connected ? disconnect : connect}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:border-slate-400 hover:bg-surface-subtle dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500 dark:hover:bg-slate-800"
        >
          {connected ? "Disconnect" : "Connect"}
        </button>
        <span className="text-xs text-ink-subtle dark:text-slate-400">
          {hostLabel} · audited to the lab history DB
        </span>
      </div>
      {message && (
        <p
          role="status"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200"
        >
          {message}
        </p>
      )}
      <div
        ref={mountRef}
        className="h-[60vh] min-h-[320px] w-full overflow-hidden rounded-xl border border-slate-800 bg-[#0b1120] p-2"
      />
    </section>
  );
}
