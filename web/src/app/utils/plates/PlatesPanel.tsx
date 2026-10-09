"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  getCustodyMap,
  getCustodyPlate,
  getLocations,
  postCustodyMove,
  type CustodyAction,
  type CustodyMap,
  type CustodyNode,
  type CustodyPlace,
  type CustodySection,
  type LocationEntry,
} from "@/lib/api";
import { useUserAuth } from "@/lib/user-auth";

/**
 * Lab map — where every container is, per the record layer's custody ledger
 * (docs/PLATE_TRACKING.md D5–D8, §11.4), and the human front door for a
 * bench-top move.
 *
 * Location-first: every registry place (`locations.yaml`), grouped by the
 * platform its equipment belongs to (`platforms.yaml`), with the containers
 * the ledger resolves to it. A rack or block renders as a grid of its sites
 * with the occupants seated in them; a loose vial sits at its own place; a
 * site with two recorded occupants is flagged, never hidden (D2). Nothing
 * here is cached or inferred: the map is a read-through to BitacoraDB
 * (`GET /api/custody/map`), the move form posts the SAME `move` row the run
 * executor writes (`POST /api/custody/move`) with the signed-in user as the
 * mover, and the place picker is the registry, so a typo can never reach the
 * ledger. An unreachable record layer is shown as unreachable — never as an
 * empty lab. What the map shows is exactly what the record layer returned
 * for the signed-in viewer: a carrier the viewer may not read is masked,
 * and its occupant is filed at the place "inside something unseen".
 */

const cardCls =
  "rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-900";
const inputCls =
  "rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-ink dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const btnCls =
  "rounded-md bg-slate-900 px-3 py-1 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300";

const STATUS_CLS: Record<string, string> = {
  empty: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300",
  in_use: "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-900/30 dark:text-emerald-200",
  dirty: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/30 dark:text-amber-200",
  retired: "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/30 dark:text-rose-200",
};
/** Same colour as a device contradiction (D7): a recorded double occupancy. */
const CONFLICT_CLS = "ring-2 ring-rose-500 dark:ring-rose-400";

function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

/** A container is an adapter when it offers seats: a manifest, or something already seated in it. */
function offersSeats(n: CustodyNode): boolean {
  return (n.sites?.length ?? 0) > 0 || n.occupants.length > 0 || n.container_type === "rack" || n.container_type === "adapter";
}

function nodeMatches(n: CustodyNode, q: string): boolean {
  if (!q) return true;
  const own = [n.hid, n.model ?? "", n.container_type ?? ""].some((v) => v.toLowerCase().includes(q));
  return own || n.occupants.some((o) => nodeMatches(o, q));
}

function placeMatches(p: CustodyPlace, q: string): boolean {
  if (!q) return true;
  return [p.name, p.label ?? "", p.equipment_id ?? ""].some((v) => v.toLowerCase().includes(q)) || p.containers.some((c) => nodeMatches(c, q));
}

function filterMap(map: CustodyMap, q: string): { sections: CustodySection[]; unplaced: CustodyNode[] } {
  if (!q) return { sections: map.sections, unplaced: map.unplaced };
  const sections = map.sections
    .map((s) => ({ ...s, places: s.places.filter((p) => placeMatches(p, q)) }))
    .filter((s) => s.places.length > 0);
  return { sections, unplaced: map.unplaced.filter((n) => nodeMatches(n, q)) };
}

export function PlatesPanel() {
  const queryClient = useQueryClient();
  const { authenticated, requestLogin } = useUserAuth();
  const map = useQuery({ queryKey: ["custody", "map"], queryFn: getCustodyMap, refetchInterval: 10_000 });
  const locations = useQuery({ queryKey: ["locations"], queryFn: getLocations, staleTime: 60_000 });
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [showEmpty, setShowEmpty] = useState(false);

  const places: LocationEntry[] = useMemo(
    () => (locations.data?.locations ?? []).filter((l) => l.active),
    [locations.data],
  );
  const q = filter.trim().toLowerCase();
  const view = useMemo(() => (map.data ? filterMap(map.data, q) : null), [map.data, q]);
  const knownHids = useMemo(() => {
    const out: string[] = [];
    const walk = (n: CustodyNode) => {
      out.push(n.hid);
      n.occupants.forEach(walk);
    };
    map.data?.sections.forEach((s) => s.places.forEach((p) => p.containers.forEach(walk)));
    map.data?.unplaced.forEach(walk);
    return out;
  }, [map.data]);

  return (
    <div className="flex flex-col gap-6">
      <section className={cardCls}>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink dark:text-slate-100">Lab map</h2>
            <p className="text-xs text-ink-muted dark:text-slate-300">
              Every place in the lab, grouped by platform, with what the record layer&apos;s custody ledger says is
              there. A rack shows its sites and what sits in them. Robot moves are recorded by the run executor;
              bench-top moves are recorded here, by you.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1 text-xs text-ink-muted dark:text-slate-300">
              <input type="checkbox" checked={showEmpty} onChange={(e) => setShowEmpty(e.target.checked)} />
              show empty places
            </label>
            <input
              className={`${inputCls} w-64`}
              placeholder="find a plate, vial, rack or place…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              aria-label="Find a container or place"
            />
          </div>
        </div>

        {map.isPending && <p className="mt-3 text-sm text-ink-muted dark:text-slate-300">Loading the lab map…</p>}
        {map.error && (
          <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
            Could not read the custody ledger: {errorText(map.error)}. This is not the same as “an empty lab”.
          </p>
        )}
        {map.data && map.data.counts.containers === 0 && (
          <p className="mt-3 text-sm text-ink-muted dark:text-slate-300">
            No containers are registered yet — register one from bitácora (propose_plate_registration) or POST /containers.
          </p>
        )}
        {view && q && view.sections.length === 0 && view.unplaced.length === 0 && (
          <p className="mt-3 text-sm text-ink-muted dark:text-slate-300">Nothing matches “{filter.trim()}”.</p>
        )}
        {view && (
          <div className="mt-4 flex flex-col gap-5">
            {view.sections.map((s) => (
              <SectionView key={s.id} section={s} showEmpty={showEmpty || q.length > 0} selected={selected} onSelect={setSelected} />
            ))}
            {view.unplaced.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-ink dark:text-slate-100">Never placed</h3>
                <p className="text-xs text-ink-muted dark:text-slate-300">Registered, but no ledger row says where they are.</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {view.unplaced.map((n) => (
                    <NodeView key={n.container_id} node={n} selected={selected} onSelect={setSelected} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {selected && <PlateHistory hid={selected} />}

      <MoveForm
        places={places}
        knownHids={knownHids}
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

function SectionView({
  section,
  showEmpty,
  selected,
  onSelect,
}: {
  section: CustodySection;
  showEmpty: boolean;
  selected: string | null;
  onSelect: (hid: string | null) => void;
}) {
  const places = showEmpty ? section.places : section.places.filter((p) => p.containers.length > 0);
  const hidden = section.places.length - places.length;
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink dark:text-slate-100">{section.title}</h3>
      {places.length === 0 && (
        <p className="text-xs text-ink-muted dark:text-slate-300">
          Nothing recorded at its {section.places.length} place{section.places.length === 1 ? "" : "s"}.
        </p>
      )}
      <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {places.map((p) => (
          <PlaceView key={p.name} place={p} selected={selected} onSelect={onSelect} />
        ))}
      </div>
      {hidden > 0 && places.length > 0 && (
        <p className="mt-1 text-xs text-ink-subtle dark:text-slate-400">+ {hidden} empty place{hidden === 1 ? "" : "s"} hidden</p>
      )}
    </div>
  );
}

function PlaceView({ place, selected, onSelect }: { place: CustodyPlace; selected: string | null; onSelect: (hid: string | null) => void }) {
  const over = place.capacity != null && place.containers.length > place.capacity;
  return (
    <div
      className={`rounded-md border p-2 ${place.registered ? "border-slate-200 dark:border-slate-700" : "border-dashed border-amber-400"}`}
      data-testid={`place-${place.name}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-xs text-ink dark:text-slate-100">{place.name}</span>
        {place.label && <span className="truncate text-xs text-ink-subtle dark:text-slate-400">{place.label}</span>}
      </div>
      {!place.registered && (
        <p className="text-xs text-amber-700 dark:text-amber-300">not in locations.yaml any more — containers are still recorded here</p>
      )}
      {over && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          {place.containers.length} containers recorded at a place for {place.capacity}
        </p>
      )}
      <div className="mt-1 flex flex-wrap gap-1.5">
        {place.containers.length === 0 && <span className="text-xs text-ink-subtle dark:text-slate-400">empty</span>}
        {place.containers.map((n) => (
          <NodeView key={n.container_id} node={n} selected={selected} onSelect={onSelect} />
        ))}
      </div>
    </div>
  );
}

function Chip({ node, selected, onSelect, extra }: { node: CustodyNode; selected: string | null; onSelect: (hid: string | null) => void; extra?: string }) {
  const title = [node.container_type, node.model, node.status, node.chain_masked ? "inside a container you cannot see" : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <button
      type="button"
      className={`rounded border px-1.5 py-0.5 font-mono text-xs underline-offset-2 hover:underline ${STATUS_CLS[node.status ?? ""] ?? STATUS_CLS.empty} ${node.seat_conflict ? CONFLICT_CLS : ""} ${selected === node.hid ? "font-semibold" : ""}`}
      onClick={() => onSelect(selected === node.hid ? null : node.hid)}
      title={title}
      aria-label={`${node.hid}${node.seat_conflict ? " (seat conflict)" : ""}`}
    >
      {node.hid}
      {extra && <span className="ml-1 text-ink-subtle dark:text-slate-400">{extra}</span>}
      {node.chain_masked && <span className="ml-1 text-ink-subtle dark:text-slate-400">· inside ?</span>}
    </button>
  );
}

/** A container at a place: a chip, or — for anything that offers seats — a grid of its sites. */
function NodeView({ node, selected, onSelect }: { node: CustodyNode; selected: string | null; onSelect: (hid: string | null) => void }) {
  if (!offersSeats(node)) return <Chip node={node} selected={selected} onSelect={onSelect} />;
  const bySite = new Map<string, CustodyNode[]>();
  for (const o of node.occupants) {
    const k = o.seat?.site ?? "?";
    bySite.set(k, [...(bySite.get(k) ?? []), o]);
  }
  const declared = node.sites ?? [];
  const sites = [...declared, ...Array.from(bySite.keys()).filter((s) => !declared.includes(s)).sort()];
  return (
    <div className="w-full rounded-md border border-slate-300 p-1.5 dark:border-slate-600" data-testid={`adapter-${node.hid}`}>
      <div className="flex items-center justify-between gap-2">
        <Chip node={node} selected={selected} onSelect={onSelect} />
        <span className="text-xs text-ink-subtle dark:text-slate-400">
          {node.occupants.length}/{declared.length || "?"} sites
          {!node.sites && " · no manifest"}
        </span>
      </div>
      <div className="mt-1 grid grid-cols-6 gap-1 sm:grid-cols-8">
        {sites.map((site) => {
          const occ = bySite.get(site) ?? [];
          const conflict = occ.length > 1;
          return (
            <div
              key={site}
              className={`min-h-[2rem] rounded border px-1 py-0.5 text-[10px] ${occ.length ? "border-slate-300 dark:border-slate-600" : "border-dashed border-slate-200 dark:border-slate-700"} ${conflict ? CONFLICT_CLS : ""}`}
              title={`${node.hid} ${site}${conflict ? " — two containers recorded here" : ""}`}
              data-testid={`site-${node.hid}-${site}`}
            >
              <div className="text-ink-subtle dark:text-slate-400">{site}</div>
              {occ.map((o) => (
                <Chip key={o.container_id} node={o} selected={selected} onSelect={onSelect} />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function describeRow(h: CustodyAction, ownId: string): string {
  const carried = h.target_container_id !== ownId && h.source_container_id !== ownId;
  const dest = h.to_site
    ? `→ seat ${h.to_site} of ${(h.to_container_id ?? "?").slice(0, 8)}…`
    : h.to_location_id
      ? `→ ${h.to_location_id.slice(0, 8)}…`
      : "";
  return `${carried ? "moved with its carrier: " : ""}${h.action_type} ${dest}`.trim();
}

function PlateHistory({ hid }: { hid: string }) {
  const q = useQuery({ queryKey: ["custody", "plate", hid], queryFn: () => getCustodyPlate(hid) });
  return (
    <section className={cardCls}>
      <h3 className="text-sm font-semibold text-ink dark:text-slate-100">
        History — <span className="font-mono">{hid}</span>
      </h3>
      {q.data?.seat && (
        <p className="mt-1 text-xs text-ink-muted dark:text-slate-300">
          Seated {q.data.seat.site ? `at ${q.data.seat.site} of ` : "in "}
          <span className="font-mono">{q.data.seat.hid ?? "a container you cannot see"}</span>
          {q.data.location ? <> · resolved place <span className="font-mono">{q.data.location}</span></> : null}
          {q.data.seat_conflict ? " · another container is recorded at the same seat" : null}
        </p>
      )}
      {q.isPending && <p className="mt-2 text-sm text-ink-muted dark:text-slate-300">Loading…</p>}
      {q.error && <p className="mt-2 text-sm text-rose-700 dark:text-rose-300">Could not load history: {errorText(q.error)}</p>}
      {q.data && (
        <ul className="mt-2 space-y-1 text-sm">
          {q.data.history.length === 0 && <li className="text-ink-muted dark:text-slate-300">No ledger rows yet.</li>}
          {[...q.data.history].reverse().map((h) => (
            <li key={h.action_id} className="flex flex-wrap gap-x-3 font-mono text-xs">
              <span className="text-ink-muted dark:text-slate-300">{new Date(h.performed_at).toLocaleString()}</span>
              <span>{describeRow(h, q.data.container_id)}</span>
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
        You moved a container by hand — say where it is now. This writes one append-only <code>move</code> row in
        the custody ledger, attributed to you; the robot&apos;s moves are recorded by the run executor the
        same way. Only registered places (the lab&apos;s <code>locations.yaml</code>) are offered; moving a vial
        out of a rack to a place unseats it. (Seating a container <em>into</em> a rack slot from here is the next step.)
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
          <span className="text-ink-subtle dark:text-slate-300">Container (hid)</span>
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
