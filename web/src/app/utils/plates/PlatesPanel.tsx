"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  getCustodyPlate,
  getCustodyPlates,
  getLocations,
  postCustodyMove,
  type CustodyPlate,
  type LocationEntry,
} from "@/lib/api";
import { useUserAuth } from "@/lib/user-auth";

/**
 * Plates — where every plate is, per the record layer's custody ledger
 * (docs/PLATE_TRACKING.md D5–D8), and the human front door for a bench-top
 * move.
 *
 * Nothing here is cached or inferred: the table is a read-through to
 * BitacoraDB (`GET /api/custody/plates`), the move form posts the SAME
 * `move` row the run executor writes (`POST /api/custody/move`) with the
 * signed-in user as the mover, and the place picker is the lab's registry
 * (`locations.yaml`), so a typo can never reach the ledger. An unreachable
 * record layer is shown as unreachable — never as an empty lab.
 */

const cardCls =
  "rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900";
const inputCls =
  "rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-ink dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const btnCls =
  "rounded-md bg-slate-900 px-3 py-1 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300";

const STATUS_CLS: Record<string, string> = {
  empty: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  in_use: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  dirty: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  retired: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
};

function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

export function PlatesPanel() {
  const queryClient = useQueryClient();
  const { authenticated, requestLogin } = useUserAuth();
  const plates = useQuery({ queryKey: ["custody", "plates"], queryFn: getCustodyPlates, refetchInterval: 10_000 });
  const locations = useQuery({ queryKey: ["locations"], queryFn: getLocations, staleTime: 60_000 });
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const places: LocationEntry[] = useMemo(
    () => (locations.data?.locations ?? []).filter((l) => l.active),
    [locations.data],
  );

  const rows: CustodyPlate[] = useMemo(() => {
    const all = plates.data?.plates ?? [];
    const q = filter.trim().toLowerCase();
    const hit = q
      ? all.filter((p) => [p.hid, p.location ?? "", p.model ?? "", p.equipment_id ?? ""].some((v) => v.toLowerCase().includes(q)))
      : all;
    return [...hit].sort((a, b) => (a.location ?? "~").localeCompare(b.location ?? "~") || a.hid.localeCompare(b.hid));
  }, [plates.data, filter]);

  const byPlace = useMemo(() => {
    const m = new Map<string, CustodyPlate[]>();
    for (const p of rows) {
      const k = p.location ?? "— never placed —";
      m.set(k, [...(m.get(k) ?? []), p]);
    }
    return m;
  }, [rows]);

  return (
    <div className="flex flex-col gap-6">
      <section className={cardCls}>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink dark:text-slate-100">Plates</h2>
            <p className="text-xs text-ink-muted dark:text-slate-300">
              Where every registered plate is, per the record layer&apos;s custody ledger. Robot moves are
              recorded by the run executor; bench-top moves are recorded here, by you.
            </p>
          </div>
          <input
            className={`${inputCls} w-56`}
            placeholder="filter by hid, place, model…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter plates"
          />
        </div>

        {plates.isPending && <p className="mt-3 text-sm text-ink-muted dark:text-slate-300">Loading plates…</p>}
        {plates.error && (
          <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
            Could not read the custody ledger: {errorText(plates.error)}. This is not the same as “no plates”.
          </p>
        )}
        {plates.data && rows.length === 0 && (
          <p className="mt-3 text-sm text-ink-muted dark:text-slate-300">
            {filter ? "No plate matches the filter." : "No plates are registered yet — register one from bitácora (register_plate) or POST /containers."}
          </p>
        )}
        {rows.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-300">
                  <th className="py-1 pr-3">Place</th>
                  <th className="py-1 pr-3">Plate (hid)</th>
                  <th className="py-1 pr-3">Model</th>
                  <th className="py-1 pr-3">Status</th>
                  <th className="py-1 pr-3">Equipment</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(byPlace.entries()).map(([place, ps]) =>
                  ps.map((p, i) => (
                    <tr
                      key={p.hid}
                      className={`border-t border-slate-100 dark:border-slate-800 ${selected === p.hid ? "bg-slate-50 dark:bg-slate-800/60" : ""}`}
                    >
                      <td className="py-1 pr-3 font-mono text-xs text-ink-muted dark:text-slate-300">
                        {i === 0 ? place : ""}
                      </td>
                      <td className="py-1 pr-3">
                        <button
                          type="button"
                          className="font-mono text-ink underline-offset-2 hover:underline dark:text-slate-100"
                          onClick={() => setSelected(selected === p.hid ? null : p.hid)}
                          title="Show this plate's history"
                        >
                          {p.hid}
                        </button>
                      </td>
                      <td className="py-1 pr-3 font-mono text-xs">{p.model ?? "—"}</td>
                      <td className="py-1 pr-3">
                        <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_CLS[p.status ?? ""] ?? ""}`}>
                          {p.status ?? "—"}
                        </span>
                      </td>
                      <td className="py-1 pr-3 font-mono text-xs">{p.equipment_id ?? "—"}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && <PlateHistory hid={selected} />}

      <MoveForm
        places={places}
        knownHids={(plates.data?.plates ?? []).map((p) => p.hid)}
        authenticated={authenticated}
        requestLogin={requestLogin}
        defaultHid={selected ?? ""}
        onMoved={() => {
          queryClient.invalidateQueries({ queryKey: ["custody"] });
        }}
      />
    </div>
  );
}

function PlateHistory({ hid }: { hid: string }) {
  const q = useQuery({ queryKey: ["custody", "plate", hid], queryFn: () => getCustodyPlate(hid) });
  return (
    <section className={cardCls}>
      <h3 className="text-sm font-semibold text-ink dark:text-slate-100">
        History — <span className="font-mono">{hid}</span>
      </h3>
      {q.isPending && <p className="mt-2 text-sm text-ink-muted dark:text-slate-300">Loading…</p>}
      {q.error && <p className="mt-2 text-sm text-rose-700 dark:text-rose-300">Could not load history: {errorText(q.error)}</p>}
      {q.data && (
        <ul className="mt-2 space-y-1 text-sm">
          {q.data.history.length === 0 && <li className="text-ink-muted dark:text-slate-300">No ledger rows yet.</li>}
          {[...q.data.history].reverse().map((h) => (
            <li key={h.action_id} className="flex flex-wrap gap-x-3 font-mono text-xs">
              <span className="text-ink-muted dark:text-slate-300">{new Date(h.performed_at).toLocaleString()}</span>
              <span>{h.action_type}</span>
              {h.to_location_id && <span>→ {h.to_location_id.slice(0, 8)}…</span>}
              <span className="text-ink-muted dark:text-slate-300">by {h.performed_by}</span>
              {h.step_id && <span className="text-ink-muted dark:text-slate-300">step {h.step_id}</span>}
              {typeof h.params?.reason === "string" && <span className="text-ink-muted dark:text-slate-300">({String(h.params.reason)})</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function MoveForm({
  places,
  knownHids,
  authenticated,
  requestLogin,
  defaultHid = "",
  onMoved,
}: {
  places: LocationEntry[];
  knownHids: string[];
  authenticated: boolean;
  requestLogin: () => void;
  defaultHid?: string;
  onMoved: () => void;
}) {
  const [hid, setHid] = useState(defaultHid);
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const move = useMutation({
    mutationFn: () => postCustodyMove({ hid: hid.trim(), to, note: note.trim() || undefined }),
    onSuccess: () => {
      setNote("");
      onMoved();
    },
  });
  // Keep the form following the selected row, but let the user retype.
  const [lastDefault, setLastDefault] = useState(defaultHid);
  if (defaultHid !== lastDefault) {
    setLastDefault(defaultHid);
    setHid(defaultHid);
  }
  const ready = hid.trim().length > 0 && to.length > 0;

  return (
    <section className={cardCls}>
      <h3 className="text-sm font-semibold text-ink dark:text-slate-100">Record a bench-top move</h3>
      <p className="mt-1 text-xs text-ink-muted dark:text-slate-300">
        You moved a plate by hand — say where it is now. This writes one append-only <code>move</code> row in
        the custody ledger, attributed to you; the robot&apos;s moves are recorded by the run executor the
        same way. Only registered places (the lab&apos;s <code>locations.yaml</code>) are offered.
      </p>
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!authenticated) {
            requestLogin();
            return;
          }
          move.mutate();
        }}
      >
        <label className="flex flex-col gap-0.5 text-xs">
          <span className="text-ink-subtle dark:text-slate-300">Plate (hid)</span>
          <input
            className={`${inputCls} w-44 font-mono`}
            list="custody-hids"
            value={hid}
            onChange={(e) => setHid(e.target.value)}
            placeholder="PLT-0042"
            aria-label="Plate hid"
          />
          <datalist id="custody-hids">
            {knownHids.map((h) => (
              <option key={h} value={h} />
            ))}
          </datalist>
        </label>
        <label className="flex flex-col gap-0.5 text-xs">
          <span className="text-ink-subtle dark:text-slate-300">Now at</span>
          <select className={`${inputCls} w-64`} value={to} onChange={(e) => setTo(e.target.value)} aria-label="Destination place">
            <option value="">— choose a place —</option>
            {places.map((l) => (
              <option key={l.name} value={l.name}>
                {l.name}
                {l.label ? ` — ${l.label}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-xs">
          <span className="text-ink-subtle dark:text-slate-300">Note (optional)</span>
          <input className={`${inputCls} w-56`} value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
        </label>
        <button type="submit" className={btnCls} disabled={!ready || move.isPending}>
          {move.isPending ? "Recording…" : authenticated ? "Record move" : "Sign in to record"}
        </button>
      </form>
      {move.error && (
        <p className="mt-2 text-sm text-rose-700 dark:text-rose-300">Not recorded: {errorText(move.error)}</p>
      )}
      {move.data && (
        <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-300">
          Recorded: <span className="font-mono">{move.data.hid}</span> → <span className="font-mono">{move.data.to}</span>
        </p>
      )}
    </section>
  );
}
