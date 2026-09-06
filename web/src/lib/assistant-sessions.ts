/**
 * Plan mode — saved planning sessions (docs/ASSISTANT_PERSISTENCE.md step 2).
 *
 * Thin client for `/api/assistant/sessions/*` (api/app/assistant_sessions.py).
 * Everything here is a read or a metadata write; the only thing that talks to
 * a model is the turn stream, which the bubble opens itself with `fetch` so it
 * can share the SSE reader it already has for `/api/assistant/chat`.
 *
 * Plain `fetch` rather than `@/lib/api`'s `fetchJson`, deliberately: the
 * bubble's tests mock `@/lib/api` wholesale, and a saved-session request must
 * keep working under that mock.
 */

export interface SavedSession {
  id: string;
  owner: string;
  title: string;
  created_at: string;
  updated_at: string;
  revision: number;
  message_count: number;
  active_turn: boolean;
}

/** One display-only event the server projected out of a turn: a tool pill, an
 *  image link, an imported control-history entry, a refusal/decline chip.
 *  Never a live card — the store keeps no approval state (D-2). */
export interface SavedEvent {
  type: string;
  [k: string]: unknown;
}

export interface SavedMessage {
  id: string;
  seq: number;
  turn_id: string;
  role: "user" | "assistant";
  state: "completed" | "running" | "interrupted" | "failed";
  text: string;
  events: SavedEvent[];
  error: string | null;
  mode: string;
  imported: boolean;
  created_at: string;
  updated_at: string;
}

export interface SavedSessionDetail {
  session: SavedSession;
  messages: SavedMessage[];
  /** True when an admin is reading someone else's session: no turns, no edits. */
  read_only: boolean;
}

/** A message the owner chose to carry over from a temporary conversation
 *  into a new session (the "preview the content to save" step). */
export interface SeedMessage {
  role: "user" | "assistant";
  text: string;
  mode: "ask" | "control" | "plan";
  completion: "completed" | "interrupted" | "failed";
  error?: string;
  events: SavedEvent[];
}

export class SessionsApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new SessionsApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function listSessions(): Promise<SavedSession[]> {
  const data = await request<{ sessions: SavedSession[] }>("/api/assistant/sessions");
  return data.sessions;
}

export async function createSession(title: string, seed: SeedMessage[] = []): Promise<SavedSession> {
  return request<SavedSession>("/api/assistant/sessions", {
    method: "POST",
    body: JSON.stringify({ title, seed }),
  });
}

export async function getSession(id: string): Promise<SavedSessionDetail> {
  return request<SavedSessionDetail>(`/api/assistant/sessions/${encodeURIComponent(id)}`);
}

export async function renameSession(id: string, title: string, revision: number): Promise<SavedSession> {
  return request<SavedSession>(`/api/assistant/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ title, revision }),
  });
}

export async function deleteSession(id: string): Promise<void> {
  await request<void>(`/api/assistant/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** The server-side export (same non-executable notice as the temporary-chat
 *  download). Opened as a navigation so the session cookie rides along. */
export function sessionExportUrl(id: string, format: "md" | "json"): string {
  return `/api/assistant/sessions/${encodeURIComponent(id)}/export?format=${format}`;
}

export function sessionTurnsUrl(id: string): string {
  return `/api/assistant/sessions/${encodeURIComponent(id)}/turns`;
}
