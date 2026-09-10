import { fetchJson } from "./api";

export type XprAction = "startup" | "shutdown" | "weigh" | "tare" | "zero" | "door/open" | "door/close" | "cancel" | "dose/start";
export interface XprResponse { ok: boolean; message?: string | null; details?: Record<string, unknown> | null }
const root = (id: string) => `/api/equipment/${encodeURIComponent(id)}/control`;
export const getXprAccess = (id: string) => fetchJson<{ allowed: boolean }>(`${root(id)}/access`, { cache: "no-store" });
export const postXprAction = (id: string, action: XprAction, body: Record<string, unknown> = {}) =>
  fetchJson<XprResponse>(`${root(id)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
