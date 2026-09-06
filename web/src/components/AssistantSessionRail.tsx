"use client";

import { useEffect, useState, type ReactNode } from "react";

import type { SavedSession } from "@/lib/assistant-sessions";

/**
 * Plan mode's session rail (UI_DESIGN §5.10): the list of the signed-in
 * person's saved planning sessions, with new / rename / delete. Rendered
 * inside the assistant panel under the mode notice while the toggle is on
 * Plan. Purely presentational — every server call is the bubble's, so the
 * rail cannot open a session the bubble did not ask for.
 */
export function AssistantSessionRail({
  sessions,
  current,
  readOnly,
  busy,
  error,
  onOpen,
  onCreate,
  onRename,
  onDelete,
  onRefresh,
}: {
  /** null while the first list is loading. */
  sessions: SavedSession[] | null;
  current: SavedSession | null;
  /** The open session belongs to someone else (admin read). */
  readOnly: boolean;
  /** A turn is streaming: switching or deleting mid-answer is refused. */
  busy: boolean;
  error: string | null;
  onOpen: (id: string) => void;
  onCreate: (title: string) => void;
  onRename: (title: string) => void;
  onDelete: () => void;
  onRefresh: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [title, setTitle] = useState("");

  // A different session closes any half-finished edit on the previous one.
  useEffect(() => {
    setRenaming(false);
    setConfirmDelete(false);
  }, [current?.id]);

  const startCreate = () => {
    setTitle("");
    setRenaming(false);
    setCreating(true);
  };
  const startRename = () => {
    if (!current) return;
    setTitle(current.title);
    setCreating(false);
    setRenaming(true);
  };
  const submit = () => {
    const t = title.trim();
    if (!t) return;
    if (creating) onCreate(t);
    else if (renaming) onRename(t);
    setCreating(false);
    setRenaming(false);
    setTitle("");
  };

  return (
    <div
      className="shrink-0 border-b border-sky-200 bg-sky-50/60 px-3 py-2 text-xs dark:border-sky-900 dark:bg-sky-950/30"
      aria-label="Saved planning sessions"
    >
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor="ac-assistant-plan-session">
          Saved session
        </label>
        <select
          id="ac-assistant-plan-session"
          value={current?.id ?? ""}
          disabled={busy || sessions === null}
          onChange={(e) => {
            if (e.target.value) onOpen(e.target.value);
          }}
          className="min-w-0 flex-1 rounded border border-sky-300 bg-white px-2 py-1 text-xs text-ink dark:border-sky-800 dark:bg-slate-900 dark:text-slate-100"
        >
          <option value="">
            {sessions === null
              ? "Loading saved sessions…"
              : sessions.length === 0
                ? "No saved sessions yet"
                : "Choose a saved session…"}
          </option>
          {(sessions ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.title} · {s.message_count} msgs · {relativeTime(s.updated_at)}
            </option>
          ))}
          {current && !(sessions ?? []).some((s) => s.id === current.id) && (
            <option value={current.id}>
              {current.title} ({current.owner})
            </option>
          )}
        </select>
        <RailButton onClick={startCreate} disabled={busy} title="Start a new saved session">
          New
        </RailButton>
        <RailButton onClick={startRename} disabled={busy || !current || readOnly} title="Rename this session">
          Rename
        </RailButton>
        <RailButton
          onClick={() => setConfirmDelete(true)}
          disabled={busy || !current || readOnly}
          title="Delete this session (the audit trail is unaffected)"
        >
          Delete
        </RailButton>
        <RailButton onClick={onRefresh} disabled={busy} title="Reload the list (other tabs may have changed it)">
          ↻
        </RailButton>
      </div>
      {(creating || renaming) && (
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            autoFocus
            value={title}
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
            aria-label={creating ? "New session title" : "Session title"}
            placeholder={creating ? "Name the protocol you are planning…" : "Session title"}
            className="min-w-0 flex-1 rounded border border-sky-300 bg-white px-2 py-1 text-xs text-ink dark:border-sky-800 dark:bg-slate-900 dark:text-slate-100"
          />
          <button
            type="submit"
            disabled={!title.trim()}
            className="rounded bg-sky-600 px-2 py-1 font-medium text-white disabled:opacity-40"
          >
            {creating ? "Create" : "Save name"}
          </button>
          <RailButton
            onClick={() => {
              setCreating(false);
              setRenaming(false);
            }}
          >
            Cancel
          </RailButton>
        </form>
      )}
      {confirmDelete && current && (
        <div className="mt-2 flex items-center gap-2 text-sky-900 dark:text-sky-100">
          <span className="flex-1">
            Delete “{current.title}”? Its {current.message_count} messages are removed from the
            dashboard; proposals and control actions stay in the audit trail.
          </span>
          <button
            type="button"
            onClick={() => {
              setConfirmDelete(false);
              onDelete();
            }}
            className="rounded bg-rose-600 px-2 py-1 font-medium text-white"
          >
            Delete session
          </button>
          <RailButton onClick={() => setConfirmDelete(false)}>Keep</RailButton>
        </div>
      )}
      {current && (
        <p className="mt-1 truncate text-sky-900/80 dark:text-sky-200/80">
          {readOnly ? `Reading ${current.owner}'s session — ` : ""}
          {current.message_count} messages · updated {relativeTime(current.updated_at)}
          {current.active_turn ? " · a turn is running in another tab" : ""}
        </p>
      )}
      {error && <p className="mt-1 text-rose-700 dark:text-rose-300">{error}</p>}
    </div>
  );
}

function RailButton({
  children,
  onClick,
  disabled = false,
  title,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="rounded border border-sky-300 px-2 py-1 font-medium text-sky-900 transition hover:bg-sky-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-sky-800 dark:text-sky-100 dark:hover:bg-sky-900/40"
    >
      {children}
    </button>
  );
}

/**
 * The step between a temporary conversation and Plan: what would be saved,
 * shown before anything is. Three exits — save it into a new session, start
 * an empty session (the temporary conversation ends; the download buttons
 * above are still there), or stay where you are. Nothing is persisted until
 * the first button is clicked (ASSISTANT_PERSISTENCE.md §0).
 */
export function CarryOverDialog({
  count,
  controlCount,
  preview,
  busy,
  onSave,
  onStartEmpty,
  onCancel,
}: {
  count: number;
  /** How many of those messages were sent in Control mode. */
  controlCount: number;
  /** First lines of the conversation, oldest first. */
  preview: string[];
  busy: boolean;
  onSave: (title: string) => void;
  onStartEmpty: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  return (
    <div
      role="dialog"
      aria-label="Save this conversation into a Plan session?"
      className="rounded-lg border border-sky-300 bg-sky-50 p-2 text-[13px] dark:border-sky-800 dark:bg-sky-950/40"
    >
      <p className="font-semibold text-sky-900 dark:text-sky-100">
        Save this conversation into a Plan session?
      </p>
      <p className="mt-1 text-xs text-sky-900/80 dark:text-sky-200/80">
        Plan sessions are stored on the dashboard, private to you. Your current temporary chat
        ends either way — download it first if you need it as is.
        {controlCount > 0
          ? ` ${controlCount} of the ${count} messages were sent in Control mode; they are saved as history only and cannot be re-authorized from a saved session.`
          : ` ${count} messages would be saved as history.`}
      </p>
      <ul className="mt-1 max-h-24 list-disc overflow-y-auto pl-4 text-xs text-ink dark:text-slate-200">
        {preview.map((line, i) => (
          <li key={i} className="truncate">
            {line}
          </li>
        ))}
      </ul>
      <form
        className="mt-2 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) onSave(title.trim());
        }}
      >
        <input
          autoFocus
          value={title}
          maxLength={120}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="Title for the new session"
          placeholder="Title for the new session"
          className="min-w-0 flex-1 rounded border border-sky-300 bg-white px-2 py-1 text-xs text-ink dark:border-sky-800 dark:bg-slate-900 dark:text-slate-100"
        />
        <button
          type="submit"
          disabled={busy || !title.trim()}
          className="rounded bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Save into a new session
        </button>
      </form>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onStartEmpty}
          disabled={busy}
          className="rounded border border-sky-300 px-2 py-1 text-xs font-medium text-sky-900 hover:bg-sky-100 dark:border-sky-800 dark:text-sky-100 dark:hover:bg-sky-900/40"
        >
          Start Plan without saving this chat
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2 py-1 text-xs text-ink-subtle hover:bg-sky-100 dark:text-slate-300 dark:hover:bg-sky-900/40"
        >
          Stay here
        </button>
      </div>
    </div>
  );
}

function relativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}
