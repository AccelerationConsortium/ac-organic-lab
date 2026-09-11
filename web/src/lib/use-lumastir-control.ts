"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { lumastirCommand } from "./lumastir-api";
import { randomId } from "./random-id";

type Lease = { claim_token: string; heartbeat_interval_s: number };

/** Tokens stay in memory. No automatic command retries or background lease owner. */
export function useLumastirControl(id: string, locked: boolean, reportError: (error: unknown) => void, onAcknowledged?: () => Promise<void>) {
  const token = useRef<string>();
  const generation = useRef(0);
  const inFlight = useRef(false);
  const operation = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval>>();
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState(false);
  const release = useCallback(() => {
    clearInterval(timer.current);
    const value = token.current;
    token.current = undefined;
    if (value) void lumastirCommand(id, "release", {}, value, true).then(() => onAcknowledged?.()).catch(() => {
      // Device expiry stops outputs even when page teardown cannot deliver release.
    });
  }, [id, onAcknowledged]);

  useEffect(() => {
    generation.current += 1;
    const leaving = () => { generation.current += 1; release(); setActive(false); setBusy(false); };
    window.addEventListener("pagehide", leaving);
    return () => { leaving(); window.removeEventListener("pagehide", leaving); };
  }, [release]);
  useEffect(() => {
    if (locked) { generation.current += 1; release(); setActive(false); }
  }, [locked, release]);

  const command = useCallback(async (action: "motor/set" | "led/set" | "stop", body: Record<string, unknown> = {}) => {
    if (locked || (inFlight.current && action !== "stop")) return;
    const op = ++operation.current;
    inFlight.current = true; setBusy(true);
    const current = generation.current;
    try {
      if (action === "stop") {
        generation.current += 1;
        await lumastirCommand(id, "stop");
        release(); setActive(false);
        await onAcknowledged?.();
        return;
      }
      if (!token.current) {
        const lease = await lumastirCommand<Lease>(id, "claim", { session_id: randomId() });
        if (!lease.claim_token || !Number.isFinite(lease.heartbeat_interval_s) || lease.heartbeat_interval_s <= 0) {
          throw new Error("Invalid claim response; outputs will expire within 30 seconds.");
        }
        if (generation.current !== current) {
          await lumastirCommand(id, "release", {}, lease.claim_token, true);
          return;
        }
        token.current = lease.claim_token;
        setActive(true);
        let beating = false;
        timer.current = setInterval(() => {
          if (beating || !token.current) return;
          beating = true;
          const held = token.current;
          void lumastirCommand(id, "heartbeat", {}, held).catch(error => {
            if (token.current !== held) return;
            release(); setActive(false); reportError(error);
          }).finally(() => { beating = false; });
        }, Math.min(5000, lease.heartbeat_interval_s * 500));
      }
      if (generation.current !== current) return;
      await lumastirCommand(id, action, body, token.current);
      if (op === operation.current) await onAcknowledged?.();
    } catch (error) {
      if (op === operation.current) { release(); setActive(false); reportError(error); }
    } finally {
      if (op === operation.current) { inFlight.current = false; setBusy(false); }
    }
  }, [id, locked, release, reportError, onAcknowledged]);
  return { command, busy, active };
}
