"use client";

import { useEffect, useRef, useState } from "react";
import { useUserAuth } from "@/lib/user-auth";

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M13.5 4.5 6.5 11.5 2.5 7.5l1-1 3 3 6-6 1 1Z" />
    </svg>
  );
}

function CrossIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M12 4.7 11.3 4 8 7.3 4.7 4 4 4.7 7.3 8 4 11.3l.7.7L8 8.7l3.3 3.3.7-.7L8.7 8 12 4.7Z" />
    </svg>
  );
}

/**
 * Inline login widget that sits between the dashboard title and the page
 * tabs. Logged out, the dashboard is view-only; signing in here enables the
 * control tiles. Flow: pick an account → "Send code" (✓ when emailed) →
 * enter the code → "Log in" (✓ success / ✗ wrong code).
 */
export function LoginBar() {
  const {
    loading,
    authenticated,
    identity,
    users,
    usersLoading,
    requestCode,
    verifyCode,
    logout,
    registerLoginHint,
  } = useUserAuth();

  const [selectedId, setSelectedId] = useState("");
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [sent, setSent] = useState(false);
  const [sendErr, setSendErr] = useState<string | null>(null);
  const [result, setResult] = useState<"ok" | "bad" | null>(null);
  const [verifyErr, setVerifyErr] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);

  const barRef = useRef<HTMLDivElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  // Let the auth provider scroll us into view + flash when a control action
  // is attempted while logged out.
  useEffect(() => {
    registerLoginHint(() => {
      barRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      setFlash(true);
      window.setTimeout(() => setFlash(false), 1800);
    });
  }, [registerLoginHint]);

  // No default selection: the dropdown starts blank ("Select your name…") so
  // no account is pre-chosen and no identity is revealed until the user picks.

  // Focus the code input as soon as a code is sent.
  useEffect(() => {
    if (sent) {
      const id = window.setTimeout(() => codeRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
  }, [sent]);

  async function handleSend() {
    if (!selectedId || sending) return;
    setSending(true);
    setSendErr(null);
    setResult(null);
    setVerifyErr(null);
    const r = await requestCode(selectedId);
    setSending(false);
    if (r.ok) setSent(true);
    else setSendErr(r.error ?? "Could not send code");
  }

  async function handleVerify() {
    if (!selectedId || !code || verifying) return;
    setVerifying(true);
    setVerifyErr(null);
    setResult(null);
    const r = await verifyCode(selectedId, code);
    setVerifying(false);
    if (r.ok) {
      setResult("ok");
    } else {
      setResult("bad");
      setVerifyErr(r.error ?? "Incorrect code");
    }
  }

  function resetFlow() {
    setSent(false);
    setCode("");
    setSendErr(null);
    setVerifyErr(null);
    setResult(null);
  }

  // Flat row — the sticky bar wrapper (layout.tsx) provides the chrome.
  const baseClasses = [
    "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md px-1 py-0.5 text-sm transition-colors",
    flash ? "bg-sky-50 dark:bg-sky-950/40" : "",
  ].join(" ");

  // ---- Signed in ----------------------------------------------------------
  if (authenticated && identity) {
    return (
      <div ref={barRef} className={baseClasses}>
        <span className="inline-flex items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-300">
          <CheckIcon className="h-4 w-4 shrink-0" />
          Signed in
        </span>
        <span className="text-ink dark:text-slate-200">
          {identity.email}
          <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-ink-muted dark:bg-slate-800 dark:text-slate-300">
            {identity.role}
          </span>
        </span>
        <button
          type="button"
          onClick={() => {
            void logout();
            resetFlow();
          }}
          className="ml-auto rounded-md border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-ink-muted hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          Log out
        </button>
      </div>
    );
  }

  // ---- Logged out (view-only) --------------------------------------------
  return (
    <div ref={barRef} className={baseClasses}>
      <span className="font-medium text-ink-muted dark:text-slate-300">
        View-only · sign in to control equipment
      </span>

      <label className="sr-only" htmlFor="login-user">
        Account
      </label>
      <select
        id="login-user"
        value={selectedId}
        disabled={loading || usersLoading || sending || verifying}
        onChange={(e) => {
          setSelectedId(e.target.value);
          resetFlow();
        }}
        className="rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
      >
        <option value="">
          {usersLoading
            ? "Loading…"
            : users.length === 0
              ? "No accounts"
              : "Select your name…"}
        </option>
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.name}
          </option>
        ))}
      </select>

      {!sent ? (
        <button
          type="button"
          onClick={handleSend}
          disabled={!selectedId || sending || usersLoading}
          className="rounded-md border border-sky-400 bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-800 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-200 dark:hover:bg-sky-900/60"
        >
          {sending ? "Sending…" : "Send code"}
        </button>
      ) : (
        <>
          <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
            <CheckIcon className="h-3.5 w-3.5 shrink-0" />
            Code emailed
          </span>
          <label className="sr-only" htmlFor="login-code">
            Sign-in code
          </label>
          <input
            id="login-code"
            ref={codeRef}
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            placeholder="Code"
            disabled={verifying}
            onChange={(e) => {
              setCode(e.target.value);
              if (result) setResult(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleVerify();
            }}
            className="w-24 rounded-md border border-slate-200 bg-white px-2 py-1 text-sm text-ink shadow-inner outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
          <button
            type="button"
            onClick={handleVerify}
            disabled={!code || verifying}
            className="rounded-md border border-emerald-400 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200 dark:hover:bg-emerald-900/60"
          >
            {verifying ? "…" : "Log in"}
          </button>
          {result === "ok" && (
            <CheckIcon className="h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          )}
          {result === "bad" && (
            <CrossIcon className="h-5 w-5 shrink-0 text-rose-600 dark:text-rose-400" />
          )}
          <button
            type="button"
            onClick={resetFlow}
            className="text-xs font-medium text-ink-subtle underline hover:text-ink-muted dark:text-slate-400 dark:hover:text-slate-200"
          >
            Use a different account
          </button>
        </>
      )}

      {(sendErr || verifyErr) && (
        <span
          role="alert"
          className="inline-flex items-center gap-1 text-xs font-medium text-rose-700 dark:text-rose-300"
        >
          <CrossIcon className="h-3.5 w-3.5 shrink-0" />
          {sendErr ?? verifyErr}
        </span>
      )}
    </div>
  );
}
