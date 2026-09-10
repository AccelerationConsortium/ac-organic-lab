"use client";

import { useEffect, useRef, useState } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { postEasyMaxAction, type EasyMaxCommand } from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { useActionError } from "@/lib/use-action-error";
import { LockButton } from "./ControlLock";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

type Fields = Record<string, unknown>;
const fields = (v: unknown): Fields =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Fields) : {};
const number = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const fmt = (v: unknown, decimals = 1) => number(v)?.toFixed(decimals) ?? "—";
const stateLabel = (v: unknown) => (typeof v === "string" ? v : "unknown");
// Touch-sized inputs; the device remains the authority for operating limits.
const inputClass =
  "h-11 w-full min-w-0 rounded-md border border-slate-300 bg-white px-2 text-sm tabular-nums disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100";

function Reading({
  label,
  value,
  unit,
}: {
  label: string;
  value: unknown;
  unit: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-ink dark:text-slate-100">
        {fmt(value, unit === "rpm" ? 0 : 1)}{" "}
        <span className="text-xs font-normal text-ink-subtle dark:text-slate-400">
          {unit}
        </span>
      </div>
    </div>
  );
}

function ZoneControls({
  reactor,
  zone,
  limits,
  disabled,
  can,
  propose,
}: {
  reactor: number;
  zone: Fields;
  limits: Fields;
  disabled: boolean;
  can: (action: EasyMaxCommand["action"]) => boolean;
  propose: (command: EasyMaxCommand, summary: string) => void;
}) {
  const thermo = fields(zone.thermostat);
  const stir = fields(zone.stirrer);
  // Drafts belong to this zone and are deliberately not overwritten by polling.
  const [target, setTarget] = useState(
    thermo.state === "ramp" ? "" : (number(thermo.end_value_c)?.toString() ?? ""),
  );
  const [mode, setMode] = useState<"Tr" | "Tj">(
    thermo.mode === "Tj" ? "Tj" : "Tr",
  );
  const [ramp, setRamp] = useState("300");
  const [rampMode, setRampMode] = useState<"duration" | "rate">("duration");
  const [operation, setOperation] = useState<"temp/reflux" | "temp/distill">("temp/reflux");
  const [jacketEnd, setJacketEnd] = useState("");
  const [offset, setOffset] = useState("5");
  // Older deployments retain the original conservative form bounds.
  const tempMin = number(limits.temperature_min_c) ?? -40;
  const tempMax = number(limits.temperature_max_c) ?? 180;
  const rpmMax = number(limits.stir_rate_max_rpm) ?? 800;
  const durationMax = number(limits.ramp_duration_max_s) ?? 86400;
  const rampMax = rampMode === "rate" ? (number(limits.ramp_rate_max_k_per_min) ?? 10) : durationMax;
  const [rpm, setRpm] = useState(number(stir.end_value_rpm)?.toString() ?? "");
  const [stirRamp, setStirRamp] = useState("15");
  const valid = (s: string, min: number, max: number) =>
    s.trim() !== "" &&
    Number.isFinite(Number(s)) &&
    Number(s) >= min &&
    Number(s) <= max;
  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (
            disabled ||
            !can("temp/set") ||
            !valid(target, tempMin, tempMax) ||
            !valid(ramp, 0.001, rampMax)
          )
            return;
          propose(
            {
              action: "temp/set",
              body: {
                reactor,
                mode,
                end_value_c: Number(target),
                ramp_mode: rampMode,
                rate_or_duration: Number(ramp),
              },
            },
            `Reactor ${reactor}: set ${mode} to ${target} °C ${rampMode === "rate" ? "at" : "over"} ${ramp} ${rampMode === "rate" ? "K/min" : "s"}`,
          );
        }}
      >
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium">Temperature</span>
          <TileButton
            variant="danger"
            disabled={disabled || !can("temp/stop")}
            onClick={() =>
              propose(
                { action: "temp/stop", body: { reactor } },
                `Reactor ${reactor}: stop temperature control`,
              )
            }
          >
            Stop temperature
          </TileButton>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs">
            Control
            <select
              aria-label="Temperature control mode"
              className={inputClass}
              value={mode}
              disabled={disabled || !can("temp/set")}
              onChange={(e) => setMode(e.target.value as "Tr" | "Tj")}
            >
              <option value="Tr">Reactor (Tr)</option>
              <option value="Tj">Jacket (Tj)</option>
            </select>
          </label>
          <label className="text-xs">
            Target °C
            <input
              aria-label="Target temperature"
              className={inputClass}
              type="number"
              step="any"
              min={tempMin}
              max={tempMax}
              value={target}
              disabled={disabled || !can("temp/set")}
              onChange={(e) => setTarget(e.target.value)}
              required
            />
          </label>
          <label className="text-xs">
            Ramp by
            <select aria-label="Temperature ramp mode" className={inputClass} value={rampMode}
              disabled={disabled || !can("temp/set")}
              onChange={(e) => { setRampMode(e.target.value as "duration" | "rate"); setRamp(e.target.value === "rate" ? "1" : "300"); }}>
              <option value="duration">Duration (s)</option>
              <option value="rate">Rate (K/min)</option>
            </select>
          </label>
          <label className="text-xs">
            {rampMode === "rate" ? "Ramp K/min" : "Ramp seconds"}
            <input
              aria-label={rampMode === "rate" ? "Temperature ramp rate" : "Temperature ramp seconds"}
              className={inputClass}
              type="number"
              step="any"
              min={0.001}
              max={rampMax}
              value={ramp}
              disabled={disabled || !can("temp/set")}
              onChange={(e) => setRamp(e.target.value)}
              required
            />
          </label>
        </div>
        <div className="mt-2">
          <TileButton
            type="submit"
            variant="primary"
            disabled={
              disabled ||
              !can("temp/set") ||
              !valid(target, tempMin, tempMax) ||
              !valid(ramp, 0.001, rampMax)
            }
          >
            Review temperature
          </TileButton>
        </div>
      </form>
      <form
        className="border-t border-slate-200 pt-3 dark:border-slate-700"
        onSubmit={(e) => {
          e.preventDefault();
          if (
            disabled ||
            !can("stir/start") ||
            !valid(rpm, 0, rpmMax) ||
            !valid(stirRamp, 0, durationMax)
          )
            return;
          propose(
            {
              action: "stir/start",
              body: {
                reactor,
                rate_rpm: Number(rpm),
                duration_s: Number(stirRamp),
              },
            },
            `Reactor ${reactor}: set stirring to ${rpm} rpm over ${stirRamp} s`,
          );
        }}
      >
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium">Stirring</span>
          <TileButton
            variant="danger"
            disabled={disabled || !can("stir/stop")}
            onClick={() =>
              propose(
                { action: "stir/stop", body: { reactor } },
                `Reactor ${reactor}: stop stirring`,
              )
            }
          >
            Stop stirring
          </TileButton>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs">
            Target rpm
            <input
              aria-label="Target stir speed"
              className={inputClass}
              type="number"
              step="any"
              min={0}
              max={rpmMax}
              value={rpm}
              disabled={disabled || !can("stir/start")}
              onChange={(e) => setRpm(e.target.value)}
              required
            />
          </label>
          <label className="text-xs">
            Ramp seconds
            <input
              aria-label="Stir ramp seconds"
              className={inputClass}
              type="number"
              step="any"
              min={0}
              max={durationMax}
              value={stirRamp}
              disabled={disabled || !can("stir/start")}
              onChange={(e) => setStirRamp(e.target.value)}
              required
            />
          </label>
        </div>
        <div className="mt-2">
          <TileButton
            type="submit"
            variant="primary"
            disabled={
              disabled ||
              !can("stir/start") ||
              !valid(rpm, 0, rpmMax) ||
              !valid(stirRamp, 0, durationMax)
            }
          >
            Review stirring
          </TileButton>
        </div>
      </form>
      <p className="text-xs text-ink-subtle">Switching temperature control off can chill the jacket and cause condensation. To park, set an appropriate room temperature and leave control on.</p>
      <details className="border-t border-slate-200 pt-3 dark:border-slate-700">
        <summary className="cursor-pointer py-2 text-sm font-medium">Reflux / Distillation</summary>
        <form className="space-y-2" onSubmit={(e) => {
          e.preventDefault();
          if (disabled || !can(operation) || !valid(jacketEnd, tempMin, tempMax) || !valid(offset, -200, 200)) return;
          propose({ action: operation, body: { reactor, tj_end_c: Number(jacketEnd), tj_minus_tr_k: Number(offset) } },
            `Reactor ${reactor}: ${operation === "temp/reflux" ? "reflux" : "distill"}, jacket end ${jacketEnd} °C, Tj − Tr ${offset} K`);
        }}>
          <label className="block text-xs">Operation
            <select aria-label="Thermal operation" className={inputClass} value={operation} disabled={disabled}
              onChange={(e) => setOperation(e.target.value as typeof operation)}>
              <option value="temp/reflux">Reflux</option><option value="temp/distill">Distillation</option>
            </select>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">Jacket end °C
              <input aria-label="Jacket end temperature" className={inputClass} type="number" step="any" min={tempMin} max={tempMax}
                value={jacketEnd} onChange={(e) => setJacketEnd(e.target.value)} disabled={disabled || !can(operation)} required />
            </label>
            <label className="text-xs">Tj − Tr (K)
              <input aria-label="Jacket minus reactor offset" className={inputClass} type="number" step="any" min={-200} max={200}
                value={offset} onChange={(e) => setOffset(e.target.value)} disabled={disabled || !can(operation)} required />
            </label>
          </div>
          <p className="text-xs">Command offset is jacket minus reactor, the opposite of the Tr − Tj readout.</p>
          <TileButton type="submit" disabled={disabled || !can(operation) || !valid(jacketEnd, tempMin, tempMax) || !valid(offset, -200, 200)}>
            Review thermal operation
          </TileButton>
        </form>
      </details>
    </div>
  );
}

export function EasyMaxTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, reportError, clearError } = useActionError();
  const [selected, setSelected] = useState<"1" | "2">("1");
  const [pending, setPending] = useState(false);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const sending = useRef(false);
  const [review, setReview] = useState<{
    command: EasyMaxCommand;
    summary: string;
  } | null>(null);
  const [accepted, setAccepted] = useState<string | null>(null);
  const details = fields(snapshot.status.details);
  const zones = fields(details.zones);
  const keys = Object.keys(zones).filter((k) => k === "1" || k === "2");
  const active = keys.includes(selected) ? selected : keys[0];
  const zone = fields(zones[active]);
  const thermo = fields(zone.thermostat);
  const stir = fields(zone.stirrer);
  const progress = fields(fields(zone.progress).thermostat);
  const claim = fields(details.claimed_by);
  const offline =
    !!snapshot.fetch_error || snapshot.status.equipment_status === "unknown";
  const timestamp = Date.parse(snapshot.fetched_at);
  const stale =
    !Number.isFinite(timestamp) ||
    now - timestamp > 30_000 ||
    (number(details.readback_age_s) ?? 0) > 30;
  const unavailable = offline || stale;
  const can = (action: EasyMaxCommand["action"]) =>
    !unavailable &&
    (snapshot.status.allowed_actions ?? []).includes(
      action.replaceAll("/", "."),
    );
  const disabled =
    locked || pending || unavailable || Object.keys(claim).length > 0;
  function propose(command: EasyMaxCommand, summary: string) {
    if (disabled || !can(command.action)) return;
    clearError();
    setAccepted(null);
    setReview({ command, summary });
  }
  async function execute() {
    if (!review || disabled || sending.current || !can(review.command.action))
      return;
    sending.current = true;
    setPending(true);
    clearError();
    const request = review;
    setReview(null);
    try {
      await postEasyMaxAction(snapshot.id, request.command);
      setAccepted(
        `${request.summary} — accepted. Live readings show progress.`,
      );
    } catch (err) {
      reportError(err, request.command.action);
    } finally {
      sending.current = false;
      setPending(false);
    }
  }
  return (
    <TileShell
      snapshot={snapshot}
      displayStatus={unavailable ? "unknown" : snapshot.status.equipment_status}
      actionError={actionError}
      headerRight={
        <LockButton
          locked={locked}
          countdown={countdown}
          onToggle={toggle}
          noun="EasyMax"
        />
      }
    >
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto text-ink dark:text-slate-200">
        <div className="flex gap-2" role="group" aria-label="Reactor zone">
          {keys.map((key) => (
            <button
              type="button"
              key={key}
              aria-pressed={active === key}
              disabled={pending}
              onClick={() => {
                setSelected(key);
                setReview(null);
                setAccepted(null);
              }}
              className={`flex-1 rounded-lg border px-3 py-2 text-left ${active === key ? "border-sky-400 bg-sky-50 dark:border-sky-600 dark:bg-sky-950/40" : "border-slate-200 dark:border-slate-700"}`}
            >
              <span className="text-sm font-semibold">Reactor {key}</span>
              <span className="ml-2 text-xs text-ink-subtle dark:text-slate-400">
                {key === "1" ? "Left" : "Right"} ·{" "}
                {unavailable
                  ? "—"
                  : `${fmt(fields(fields(zones[key]).thermostat).tr_c)} °C`}
              </span>
            </button>
          ))}
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
          <div className="grid grid-cols-2 gap-3">
            <Reading
              label="Reactor · Tr"
              value={unavailable ? null : thermo.tr_c}
              unit="°C"
            />
            <Reading
              label="Jacket · Tj"
              value={unavailable ? null : thermo.tj_c}
              unit="°C"
            />
            <Reading label="Tr − Tj" value={unavailable ? null : thermo.tr_minus_tj_c} unit="K" />
            <Reading
              label="Stir speed"
              value={unavailable ? null : stir.rate_rpm}
              unit="rpm"
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-200 pt-2 text-xs dark:border-slate-700">
            <span>
              Temperature: {unavailable ? "unknown" : stateLabel(thermo.state)}
              {!unavailable && progress.stable === true ? " · stable" : ""}
            </span>
            <span>
              Stirrer: {unavailable ? "unknown" : stateLabel(stir.state)}
            </span>
            {!unavailable && number(thermo.end_value_c) !== null && (
              <span>
                Setpoint: {fmt(thermo.end_value_c)} °C ({stateLabel(thermo.mode)})
              </span>
            )}
            {!unavailable &&
              thermo.state === "ramp" &&
              number(thermo.remaining_s) !== null && (
                <span>{fmt(thermo.remaining_s, 0)} s remaining</span>
              )}
          </div>
        </div>
        {Array.isArray(details.dosing_units) && details.dosing_units.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer py-2">Dosing units · read only</summary>
            {details.dosing_units.map((item, index) => {
              const channel = fields(item);
              return <p key={index}>Unit {String(channel.unit ?? "—")} · channel {String(channel.channel ?? "—")}: {unavailable ? "unknown" : stateLabel(channel.state)} · {unavailable ? "—" : fmt(channel.dosed_ml)} mL dosed</p>;
            })}
          </details>
        )}
        {unavailable && (
          <p
            role="status"
            className="text-xs text-amber-700 dark:text-amber-300"
          >
            Live readings unavailable or stale. Controls are disabled.
          </p>
        )}
        {Object.keys(claim).length > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            Controlled by{" "}
            {typeof claim.owner === "string" ? claim.owner : "another session"}.
          </p>
        )}
        {active ? (
          <ZoneControls
            key={`${snapshot.id}-${active}`}
            reactor={Number(active)}
            zone={zone}
            limits={fields(details.limits)}
            disabled={disabled}
            can={can}
            propose={propose}
          />
        ) : (
          <p className="text-sm">Waiting for reactor zone readback.</p>
        )}
        {can("startup") && (
          <TileButton
            disabled={disabled}
            onClick={() =>
              propose(
                { action: "startup", body: {} },
                "Connect the EasyMax session",
              )
            }
          >
            Connect session
          </TileButton>
        )}
        {review && (
          <div
            role="region"
            aria-label="Review EasyMax action"
            className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs dark:border-amber-700 dark:bg-amber-950/30"
          >
            <p className="font-semibold">{review.summary}</p>
            <p>
              Confirm the bench is available and automated LLE is not
              controlling this reactor. An active iControl experiment can refuse commands; check the touchpad before proceeding.
            </p>
            <div className="flex gap-2">
              <TileButton
                variant="primary"
                disabled={disabled || !can(review.command.action)}
                onClick={() => void execute()}
              >
                Confirm action
              </TileButton>
              <TileButton onClick={() => setReview(null)}>Cancel</TileButton>
            </div>
          </div>
        )}
        {pending && (
          <p role="status" className="text-xs">
            Sending command…
          </p>
        )}
        {accepted && (
          <p
            role="status"
            className="text-xs text-emerald-700 dark:text-emerald-300"
          >
            {accepted}
          </p>
        )}
      </div>
    </TileShell>
  );
}
