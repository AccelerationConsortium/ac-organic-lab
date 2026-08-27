import { fetchJson } from "./api";

/**
 * Client for the admin-only SSH console (`api/app/ssh_console.py`).
 *
 * The identity check happens here, over plain HTTP, because
 * `web/src/middleware.ts` runs on a normal request and injects the verified
 * `X-Auth-User` / `X-Auth-Role` — it cannot do that for a WebSocket upgrade
 * (and Caddy's `forward_auth` answers an upgrade with a bare 403; see the
 * `/xarm5/ws` and `/hermes/api/ws` exemptions in the edge config). So
 * {@link openSshSession} trades the session for a single-use, 30 s ticket and
 * the socket presents that instead.
 */

/** One way to open a session on a host — plain shell, tmux attach, WSL.
 *  Only ids and labels reach the browser; the remote command each id maps to
 *  is a server-side whitelist (`ssh_console.py`), so the page can never send
 *  a command string. */
export interface SshProfile {
  id: string;
  label: string;
  description: string;
}

/** Banner facts for one SSH-reachable host. No secrets: the key file and the
 *  per-host ssh options live in the dashboard host's `~/.ssh/config`. */
export interface SshHost {
  id: string;
  label: string;
  kind: string;
  hostname: string;
  user: string;
  target: string;
  shell: string;
  note: string;
  /** Session types this host offers; the first is the default shell. */
  profiles: SshProfile[];
  /** What an operator would type on the dashboard host. */
  ssh_command: string;
  /** The same, without relying on a `~/.ssh/config` alias. */
  ssh_command_explicit: string;
}

export interface SshTicket {
  ticket: string;
  expires_in_s: number;
  host: SshHost;
  profile: SshProfile;
}

export function fetchSshHosts(): Promise<{ hosts: SshHost[] }> {
  return fetchJson<{ hosts: SshHost[] }>("/api/ssh/hosts");
}

/** Mint a single-use ticket for `hostId`. Redeem it immediately — it expires
 *  in ~30 s and dies on first use, so it is minted per connection attempt.
 *  `profileId` picks the session type (omit for the host's default shell). */
export function openSshSession(
  hostId: string,
  profileId?: string,
): Promise<SshTicket> {
  return fetchJson<SshTicket>("/api/ssh/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ host_id: hostId, profile: profileId ?? null }),
  });
}

/** Absolute ws(s):// URL for the console socket, on the page's own origin so
 *  the edge routes it through the same Caddy site as the dashboard. */
export function sshSocketUrl(
  ticket: string,
  cols: number,
  rows: number,
): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({
    ticket,
    cols: String(cols),
    rows: String(rows),
  });
  return `${proto}//${window.location.host}/api/ssh/ws?${params.toString()}`;
}

/** Frames the server sends. `o` output, `e` error, `x` session ended. */
export type SshServerFrame =
  | { t: "o"; d: string }
  | { t: "e"; d: string }
  | { t: "x"; code: number | null; outcome: string; duration_s: number };
