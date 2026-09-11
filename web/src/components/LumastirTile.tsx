"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { getEquipmentStatus } from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { useActionError } from "@/lib/use-action-error";
import { useLumastirControl } from "@/lib/use-lumastir-control";
import { TileShell } from "./TileShell";
import { TileButton } from "./TileButton";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { FetchErrorBand } from "./FetchErrorBand";

type Output = { id: string; index: number; commanded_percent: number | null };
function outputs(value: unknown): Output[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is Output => !!v && typeof v === "object" &&
    typeof v.id === "string" && Number.isInteger(v.index) && v.index >= 0 &&
    (v.commanded_percent === null || (typeof v.commanded_percent === "number" && Number.isFinite(v.commanded_percent))));
}

export function LumastirTile({ snapshot: polledSnapshot }: { snapshot: EquipmentSnapshot }) {
  const [live, setLive] = useState<EquipmentSnapshot | null>(null);
  const mounted = useRef(false);
  const readSequence = useRef(0);
  const id = polledSnapshot.id;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; readSequence.current += 1; };
  }, [id]);
  // Both timestamps originate at the dashboard API. Keep a direct read until
  // the whole-lab cache catches up; never flash an older pre-command state.
  const snapshot = live?.id === id &&
    Date.parse(live.fetched_at) > Date.parse(polledSnapshot.fetched_at)
      ? live : polledSnapshot;
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, reportError, clearError } = useActionError();
  const refresh = useCallback(async () => {
    if (!mounted.current) return;
    const sequence = ++readSequence.current;
    try {
      const fresh = await getEquipmentStatus(id);
      if (mounted.current && sequence === readSequence.current) setLive(fresh);
    } catch (error) {
      // The control already succeeded. A failed read must not be retried as a
      // command or release an otherwise healthy control claim.
      if (mounted.current && sequence === readSequence.current) {
        reportError(new Error(`Command acknowledged, but status refresh failed: ${error instanceof Error ? error.message : String(error)}`));
      }
    }
  }, [id, reportError]);
  const { command, busy, active } = useLumastirControl(snapshot.id, locked, reportError, refresh);
  const [power, setPower] = useState(["50", "50", "50"]);
  const details = snapshot.status.details ?? {};
  const motors = outputs(details.motors), leds = outputs(details.leds);
  const maximum = typeof details.max_power === "number" && Number.isFinite(details.max_power) ? details.max_power : 100;
  const age = Date.now() - Date.parse(snapshot.fetched_at ?? "");
  const stale = !Number.isFinite(age) || age > 10000;
  const unavailable = locked || busy || !!snapshot.fetch_error || stale || snapshot.enabled === false || !!snapshot.maintenance;
  const allowed = snapshot.status.allowed_actions ?? [];
  const send = (action: "motor/set" | "led/set" | "stop", body = {}) => { clearError(); void command(action, body); };
  return <TileShell snapshot={snapshot} actionError={actionError}
    headerRight={<><LockButton locked={locked} countdown={countdown} onToggle={toggle} noun="Lumastir" /><StatusPill state={snapshot.status.equipment_status} /></>}
    footerLeft="PWM setpoints · no motion or light measurement">
    <div className="grid grid-cols-3 gap-2">
      {[0, 1, 2].map(vial => {
        const motor = motors.find(o => o.id === `motor_${[0, 4, 8][vial]}`);
        const led = leds.find(o => o.id === `led_${[17, 18, 27][vial]}`);
        const speed = motor?.commanded_percent, brightness = led?.commanded_percent;
        const motorKnown = typeof speed === "number", ledKnown = typeof brightness === "number";
        const motorOn = motorKnown && speed > 0, ledOn = ledKnown && brightness > 0;
        const value = Number(power[vial]);
        const valid = power[vial].trim() !== "" && Number.isFinite(value) && value > 0 && value <= maximum;
        const motorDisabled = unavailable || !motorKnown || !allowed.includes("lumastir.motor.set");
        const ledDisabled = unavailable || !ledKnown || !allowed.includes("lumastir.led.set");
        return <section key={vial} aria-label={`Vial ${vial + 1}`} className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-2 dark:border-slate-700 dark:bg-slate-800/40">
          <h3 className="mb-2 text-center text-xs font-semibold">Vial {vial + 1}</h3>
          <div className="flex flex-col gap-2">
            <TileButton ariaLabel={`Vial ${vial + 1} motor ${motorOn ? "off" : "on"}`} disabled={motorDisabled || (!motorOn && !valid)} variant={motorOn ? "primary" : "default"}
              onClick={() => motor && send("motor/set", { index: motor.index, speed: motorOn ? 0 : value })}>
              Motor {motorKnown ? motorOn ? "ON" : "OFF" : "?"}
            </TileButton>
            <label className="text-[10px] text-slate-600 dark:text-slate-300">Stir power %
              <input aria-label={`Vial ${vial + 1} stir percentage`} type="number" min="1" max={maximum} step="any" value={power[vial]} disabled={locked || busy}
                onChange={e => setPower(p => p.map((v, i) => i === vial ? e.target.value : v))}
                className="mt-1 w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100" />
            </label>
            <TileButton ariaLabel={`Apply vial ${vial + 1} stir percentage`} size="small" disabled={motorDisabled || !motorOn || !valid}
              onClick={() => motor && send("motor/set", { index: motor.index, speed: value })}>Set</TileButton>
            <span className="text-center text-[10px] text-slate-500">{motorKnown && !snapshot.fetch_error ? `${speed}% commanded` : "State unknown"}</span>
            <TileButton ariaLabel={`Vial ${vial + 1} LED ${ledOn ? "off" : "on"}`} disabled={ledDisabled} variant={ledOn ? "primary" : "default"}
              onClick={() => led && send("led/set", { index: led.index, brightness: ledOn ? 0 : maximum })}>
              LED {ledKnown ? ledOn ? "ON" : "OFF" : "?"}
            </TileButton>
          </div>
        </section>;
      })}
    </div>
    <div className="mt-2 flex items-center justify-between gap-2">
      <p className="text-[10px] text-slate-500">{active ? "Controls active. Leaving this page turns all outputs off." : "Keep this page open while outputs are on."}</p>
      <TileButton variant="danger" ariaLabel="Stop all Lumastir motors and LEDs" disabled={locked} onClick={() => send("stop")}>All off</TileButton>
    </div>
    {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
  </TileShell>;
}
