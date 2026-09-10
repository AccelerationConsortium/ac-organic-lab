"use client";

import { useEffect, useRef, useState } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { getXprAccess, postXprAction, type XprAction } from "@/lib/xpr-api";
import { useUserAuth } from "@/lib/user-auth";
import { useControlLock } from "@/lib/use-control-lock";
import { useActionError } from "@/lib/use-action-error";
import { TileShell } from "./TileShell";
import { TileButton } from "./TileButton";

const inputClass = "h-9 w-full min-w-0 rounded border px-2 text-xs dark:bg-slate-900 disabled:opacity-50";
export function XprBalanceTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
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
    }
  }
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
        {(["startup", "shutdown", "weigh", "tare", "zero", "cancel"] as XprAction[]).map(action => <TileButton key={action} disabled={disabled(action)} variant={action === "cancel" ? "danger" : "default"} onClick={() => void send(action)}>{({ startup: "Connect", shutdown: "Disconnect", weigh: "Weigh", tare: "Tare", zero: "Zero", cancel: "Cancel" } as Record<string, string>)[action]}</TileButton>)}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {(["left", "right"] as const).flatMap(door => (["open", "close"] as const).map(verb => <TileButton key={`${door}-${verb}`} disabled={disabled(`door/${verb}`)} onClick={() => void send(`door/${verb}`, { door })}>{verb === "open" ? "Open" : "Close"} {door} door</TileButton>))}
      </div>
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
      <a className="text-xs underline" href={`/api/equipment/${encodeURIComponent(snapshot.id)}/documentation/agent-docs`} target="_blank" rel="noreferrer">Agent API guide</a>
    </div>
  </TileShell>;
}
