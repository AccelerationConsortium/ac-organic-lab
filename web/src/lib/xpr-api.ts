import { fetchJson, getEquipmentStatus } from "./api";
import type { EquipmentSnapshot } from "@/types/api";

export type XprAction = "startup" | "shutdown" | "weigh" | "tare" | "zero" | "door/open" | "door/close" | "cancel" | "dose/start" | "clear_error";
export interface XprResponse { ok: boolean; message?: string | null; details?: Record<string, unknown> | null }
const root = (id: string) => `/api/equipment/${encodeURIComponent(id)}/control`;
export const getXprAccess = (id: string) => fetchJson<{ allowed: boolean }>(`${root(id)}/access`, { cache: "no-store" });
export const postXprAction = (id: string, action: XprAction, body: Record<string, unknown> = {}) =>
  fetchJson<XprResponse>(`${root(id)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
// A live read of the device (the /status route polls the device itself, not
// the aggregator's cache). The tile calls it right after an action so the
// doors / weight it shows reflect the balance ~0.6 s after the action returns,
// instead of waiting out the aggregator's 2.5 s poll plus the list refetch.
export const getXprLiveStatus = (id: string): Promise<EquipmentSnapshot> => getEquipmentStatus(id);
