"use client";

import { useEffect, useRef, useState } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { getXprAccess, getXprLiveStatus, postXprAction, type XprAction } from "@/lib/xpr-api";
import { useUserAuth } from "@/lib/user-auth";
import { useControlLock } from "@/lib/use-control-lock";
import { useActionError } from "@/lib/use-action-error";
import { TileShell } from "./TileShell";
import { TileButton } from "./TileButton";

const inputClass = "h-9 w-full min-w-0 rounded border px-2 text-xs dark:bg-slate-900 disabled:opacity-50";
// weigh / tare / zero default to waiting for a stable reading, which the
// balance's web service blocks on until the pan settles — up to the device's
// 60 s request timeout. The operator sees nothing move in that time, so say so.
const STABILITY_ACTIONS = new Set<XprAction>(["weigh", "tare", "zero"]);
function pendingCopy(action: XprAction, stable: boolean): string {
  if (STABILITY_ACTIONS.has(action)) {
    const verb = { weigh: "Weighing", tare: "Taring", zero: "Zeroing" }[action as "weigh" | "tare" | "zero"];
    return stable ? `${verb} — waiting for a stable reading (up to 60 s). Keep the draft shield closed and the pan still.` : `${verb} (immediate reading)…`;
  }
  if (action === "door/open" || action === "door/close") return "Moving the draft shield…";
  if (action === "startup") return "Opening the session with the balance…";
  if (action === "shutdown") return "Closing the session…";
  if (action === "dose/start") return "Submitting the dose…";
  return "Working…";
}
const DOORS = ["left", "right"] as const;
type DoorState = "open" | "partial" | "closed" | "unknown";
function doorState(s: EquipmentSnapshot, door: (typeof DOORS)[number]): DoorState {
  const state = s.status.components?.[`door_${door}`]?.state;
  return state === "open" || state === "partial" || state === "closed" ? state : "unknown";
}
const newer = (a: EquipmentSnapshot, b: EquipmentSnapshot) => Date.parse(a.fetched_at ?? "") > Date.parse(b.fetched_at ?? "");

export function XprBalanceTile({ snapshot: polled }: { snapshot: EquipmentSnapshot }) {
  // The polled snapshot arrives via the aggregator (2.5 s poll) and the list
  // refetch (2.5 s), so a door that just moved can stay stale for up to ~5 s.
  // After each action we read the device live and show that until the polled
  // snapshot is newer.
  const [live, setLive] = useState<EquipmentSnapshot | null>(null);
  const snapshot = live && live.id === polled.id && newer(live, polled) ? live : polled;
  const refreshLive = async (id: string) => {
    try { setLive(await getXprLiveStatus(id)); } catch { /* the next poll will catch up */ }
  };
  const { identity } = useUserAuth();
  const { locked, unlock } = useControlLock(snapshot.id);
  const { actionError, reportError, clearError } = useActionError();
  const accessKey = `${snapshot.id}:${identity?.email ?? ""}`;
  const [access, setAccess] = useState<{ key: string; allowed: boolean; message: string } | null>(null);
  const [pending, setPending] = useState<XprAction | null>(null);
  const inFlight = useRef(false);
  const cancelling = useRef(false);
  const [message, setMessage] = useState("");
  const [substance, setSubstance] = useState("");
  const [amount, setAmount] = useState("");
  const [review, setReview] = useState<{ substance: string; amount: number } | null>(null);
  const [stable, setStable] = useState(true);
  useEffect(() => {
    if (locked) return;
    let active = true;
    const check = async () => {
      try {
        const result = await getXprAccess(snapshot.id);
        if (active) setAccess({ key: accessKey, allowed: result.allowed === true, message: "Control is restricted to the configured owner." });
      } catch (error) {
        if (active) setAccess({ key: accessKey, allowed: false, message: error instanceof Error ? error.message : "Control access unavailable" });
      }
    };
    void check();
    const timer = setInterval(() => void check(), 30_000);
    return () => { active = false; clearInterval(timer); };
  }, [accessKey, locked, snapshot.id]);
  const authorized = !locked && access?.key === accessKey && access.allowed;
  const can = (action: XprAction) => Boolean(authorized && !snapshot.fetch_error && snapshot.status.allowed_actions?.includes(action.replaceAll("/", ".")));
  const disabled = (action: XprAction) => !can(action) || (pending !== null && action !== "cancel");
  async function send(action: XprAction, body: Record<string, unknown> = {}) {
    if (!can(action) || (action === "cancel" ? cancelling.current : inFlight.current)) return;
    if (action === "cancel") cancelling.current = true;
    else { inFlight.current = true; setPending(action); }
    clearError();
    setMessage("");
    try {
      const result = await postXprAction(snapshot.id, action, body);
      setMessage(action === "dose/start" ? `Dose accepted (${String(result.details?.job_id ?? "job")}); waiting for the balance result.` : result.message ?? "Request completed");
    } catch (error) {
      reportError(error, action.replaceAll("/", "."));
    } finally {
      if (action === "cancel") cancelling.current = false;
      else { inFlight.current = false; setPending(null); }
      // Success or refusal, the balance may have moved: show what it says now.
      void refreshLive(snapshot.id);
    }
  }
  // A door that is already where the button would put it: nothing to do, so
  // don't offer it. The device deliberately keeps advertising door.open /
  // door.close (a redundant move is a harmless no-op there); gating on the
  // reported position is presentation.
  const doorDisabled = (door: (typeof DOORS)[number], verb: "open" | "close") => {
    const state = doorState(snapshot, door);
    return disabled(`door/${verb}`) || (verb === "open" ? state === "open" : state === "closed");
  };
  const weight = snapshot.status.metrics?.weight;
  const details = snapshot.status.details ?? {};
  const lastDose = details.last_dose as { ok?: boolean; dosed_mg?: number | null; message?: string; finished_at?: string } | undefined;
  return <TileShell snapshot={snapshot} actionError={actionError} headerRight={
    <TileButton onClick={() => void unlock()} disabled={!locked}>{authorized ? "Owner access" : "Controls locked"}</TileButton>
  }>
    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
      <div className="text-lg font-semibold tabular-nums">{typeof weight?.value === "number" ? weight.value.toFixed(4) : "—"} <span className="text-sm">g</span></div>
      {typeof details.readback_age_s === "number" && <p className="text-xs">Readback age: {details.readback_age_s.toFixed(1)} s</p>}
      {!authorized && <p className="text-xs">{locked ? "Sign in with the permitted owner account to control this balance." : access?.key === accessKey ? access.message : "Checking control access…"}</p>}
      <div className="flex flex-wrap gap-2">
        {(["startup", "shutdown", "weigh", "tare", "zero", "cancel"] as XprAction[]).map(action => <TileButton key={action} disabled={disabled(action)} variant={action === "cancel" ? "danger" : "default"} onClick={() => void send(action, STABILITY_ACTIONS.has(action) ? { immediately: !stable } : {})}>{({ startup: "Connect", shutdown: "Disconnect", weigh: "Weigh", tare: "Tare", zero: "Zero", cancel: "Cancel" } as Record<string, string>)[action]}</TileButton>)}
        {snapshot.status.last_error && <TileButton disabled={disabled("clear_error")} onClick={() => void send("clear_error")}>Clear error</TileButton>}
      </div>
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={stable} disabled={!authorized || pending !== null} onChange={event => setStable(event.target.checked)} />
        Wait for a stable reading (up to 60 s)
      </label>
      {pending && <p role="status" className="text-xs">{pendingCopy(pending, stable)}</p>}
      <div className="grid grid-cols-2 gap-2">
        {DOORS.flatMap(door => (["open", "close"] as const).map(verb => <TileButton key={`${door}-${verb}`} disabled={doorDisabled(door, verb)} onClick={() => void send(`door/${verb}`, { door })}>{verb === "open" ? "Open" : "Close"} {door} door</TileButton>))}
      </div>
      <p className="text-xs">Doors: left {doorState(snapshot, "left")} · right {doorState(snapshot, "right")}</p>
      <form className="grid grid-cols-2 gap-2" onSubmit={event => { event.preventDefault(); if (!disabled("dose/start") && substance.trim() && Number(amount) > 0 && Number(amount) <= 5000) setReview({ substance: substance.trim(), amount: Number(amount) }); }}>
        <input aria-label="Substance" className={inputClass} placeholder="Substance" maxLength={80} required value={substance} disabled={disabled("dose/start")} onChange={event => { setSubstance(event.target.value); setReview(null); }} />
        <input aria-label="Dose amount (mg)" className={inputClass} placeholder="mg" type="number" min={0.001} max={5000} step="any" required value={amount} disabled={disabled("dose/start")} onChange={event => { setAmount(event.target.value); setReview(null); }} />
        <TileButton type="submit" disabled={disabled("dose/start")}>Review dose</TileButton>
      </form>
      {review && <div role="group" aria-label="Confirm dosing" className="space-y-2 rounded border border-amber-300 p-2 text-sm">
        <p>Dispense {review.amount} mg of {review.substance}? Default tolerance: ±2%. Confirm the receiving vessel is in place.</p>
        <TileButton variant="primary" disabled={disabled("dose/start")} onClick={() => { const dose = review; setReview(null); void send("dose/start", { substance_name: dose.substance, dose_amount_mg: dose.amount }); }}>Confirm dose</TileButton>
        <TileButton onClick={() => setReview(null)}>Dismiss</TileButton>
      </div>}
      {message && <p role="status" className="text-xs">{message}</p>}
      {lastDose && <p className="text-xs">Last dose ({lastDose.finished_at ?? "time unknown"}): {lastDose.ok ? `${lastDose.dosed_mg ?? "—"} mg` : lastDose.message ?? "Failed or cancelled"}. This is the latest result, not a job history.</p>}
    </div>
  </TileShell>;
}
