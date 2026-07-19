"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDeckDeclare,
  getLabwareList,
  postDeckDeclare,
  postOt2Pause,
  postOt2Shutdown,
  postOt2Startup,
  postSetLights,
} from "@/lib/api";
import { useEquipmentStatus } from "@/lib/use-equipment";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import {
  buildSlotView,
  claimedByFromStatus,
  declaredMapFromDeck,
  deviceDeckFromStatus,
  mountedTipsFromStatus,
  nextDeclaration,
  pairModuleSlots,
  pipetteLabel,
  robotInfoFromStatus,
  robotModulesFromStatus,
  tipRacksFromStatus,
} from "@/lib/ot2-deck";
import {
  catalogEntryForDeclare,
  catalogEntryFromLabware,
  groupedCatalog,
  type CatalogEntry,
} from "@/lib/ot2-catalog";
import type { EquipmentSnapshot } from "@/types/api";

import { ActionErrorBadge } from "./ActionErrorBadge";
import { DeckPanel, ModuleReadout } from "./DeckPanel";
import { FetchErrorBand } from "./FetchErrorBand";
import { LastErrorBadge } from "./LastErrorBadge";
import { LockButton } from "./ControlLock";
import { StalenessIndicator } from "./StalenessIndicator";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {title}
      </h3>
      {children}
    </section>
  );
}

function KV({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-ink-subtle dark:text-slate-500">{k}</span>
      <span
        className={[
          "min-w-0 truncate text-right text-ink dark:text-slate-200",
          mono ? "font-mono" : "",
        ].join(" ")}
      >
        {v}
      </span>
    </div>
  );
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${
        ok ? "bg-emerald-400" : "bg-slate-400 dark:bg-slate-500"
      }`}
      aria-hidden
    />
  );
}

// ---------------------------------------------------------------------------
// Declare-intent picker (searchable, grouped, exact load names)
// ---------------------------------------------------------------------------

export function DeclarePicker({
  selectedSlot,
  currentDeclare,
  locked,
  noAccess,
  onDeclare,
  customEntries = [],
}: {
  selectedSlot: number | null;
  /** The declare string currently held by the selected slot (or null). */
  currentDeclare: string | null;
  locked: boolean;
  noAccess: boolean;
  onDeclare: (entry: CatalogEntry | null) => void;
  /** Lab-store custom definitions (GET /api/labware), merged as a "Custom" group. */
  customEntries?: CatalogEntry[];
}) {
  const [query, setQuery] = useState("");
  const [freeText, setFreeText] = useState("");
  const groups = useMemo(
    () => groupedCatalog(query, customEntries),
    [query, customEntries],
  );
  const disabled = locked || selectedSlot == null;
  const currentEntry = catalogEntryForDeclare(currentDeclare, customEntries);
  // The gateway parses a bare declare string as a load_name only when it
  // contains "_" — anything else would be misread as a legacy kind.
  const freeTextValid = freeText.includes("_") && /^[a-z0-9._]+$/.test(freeText);

  function declareFreeText() {
    if (disabled || !freeTextValid) return;
    onDeclare({
      key: `freetext-${freeText}`,
      label: freeText,
      category: "custom",
      declare: freeText,
    });
    setFreeText("");
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search plates, tip racks, modules…"
          aria-label="Search the labware catalog"
          className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink placeholder:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        />
        {selectedSlot != null ? (
          <span className="text-xs text-ink-subtle dark:text-slate-400">
            → slot {selectedSlot}
            {currentDeclare && (
              <>
                {" "}
                · currently <span className="font-mono">{currentDeclare}</span>
              </>
            )}
          </span>
        ) : (
          <span className="text-xs text-ink-subtle dark:text-slate-500">Select a deck slot first</span>
        )}
      </div>

      {locked && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          {noAccess
            ? "No access — only authorized users of this device can change its declared deck."
            : "Sign in to declare deck intent."}
        </p>
      )}

      <div className="max-h-64 overflow-y-auto rounded-md border border-slate-200 dark:border-slate-800">
        {groups.length === 0 && (
          <p className="p-3 text-xs text-ink-subtle dark:text-slate-500">No catalog match for “{query}”.</p>
        )}
        {groups.map((g) => (
          <div key={g.category}>
            <div className="sticky top-0 bg-slate-50 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:bg-slate-800 dark:text-slate-400">
              {g.label}
            </div>
            <ul>
              {g.entries.map((e) => {
                const active = currentEntry?.key === e.key;
                return (
                  <li key={e.key}>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => onDeclare(e)}
                      title={`Declares ${e.declare}${e.compat ? ` — ${e.compat}` : ""}`}
                      className={[
                        "flex w-full items-baseline justify-between gap-3 px-2 py-1.5 text-left text-xs transition-colors",
                        active
                          ? "bg-sky-50 text-sky-900 dark:bg-sky-950/40 dark:text-sky-200"
                          : "text-ink hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800/60",
                        disabled ? "cursor-not-allowed opacity-50" : "",
                      ].join(" ")}
                    >
                      <span className="min-w-0 truncate">{e.label}</span>
                      <span className="shrink-0 font-mono text-[10px] text-ink-subtle dark:text-slate-500">
                        {e.declare}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      {/* Free-text declare: any exact Opentrons load_name — including one not
          (yet) in any catalog. Must contain "_" or the gateway would parse it
          as a legacy kind string. */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={freeText}
          onChange={(e) => setFreeText(e.target.value.trim())}
          onKeyDown={(e) => {
            if (e.key === "Enter") declareFreeText();
          }}
          disabled={disabled}
          placeholder="…or type an exact load_name (e.g. matterlab_54_vialplate_2ml)"
          aria-label="Declare a custom load name"
          className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-ink placeholder:font-sans placeholder:text-slate-400 disabled:bg-slate-50 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:disabled:bg-slate-900"
        />
        <button
          type="button"
          disabled={disabled || !freeTextValid}
          onClick={declareFreeText}
          title={
            freeText && !freeTextValid
              ? "Load names are lowercase letters/digits/dot/underscore and must contain an underscore"
              : "Declare this exact load name on the selected slot"
          }
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-ink hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500"
        >
          Declare custom
        </button>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={disabled || currentDeclare == null}
          onClick={() => onDeclare(null)}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-ink hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500"
        >
          Clear slot
        </button>
        <p className="text-[10px] leading-tight text-ink-subtle dark:text-slate-500">
          Declaring records operator intent only — it does not load labware on the robot or run
          protocol setup. Need a new definition?{" "}
          <Link href="/utils/labware_builder" className="text-sky-700 underline-offset-2 hover:underline dark:text-sky-400">
            Labware builder ↗
          </Link>
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The full-page OT-2 interface
// ---------------------------------------------------------------------------

export function Ot2ControlPanel({ equipmentId }: { equipmentId: string }) {
  const { data: snapshot, error, isPending } = useEquipmentStatus(equipmentId);
  const { locked, noAccess, countdown, toggle } = useControlLock(equipmentId);
  const { actionError, setActionError, reportError } = useActionError();
  const queryClient = useQueryClient();
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [declaring, setDeclaring] = useState(false);
  const [pending, setPending] = useState(false);

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading equipment status…</p>;
  }
  if (error || !snapshot) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load <span className="font-mono">{equipmentId}</span>
        {error ? `: ${error.message}` : "."}{" "}
        Check that the id exists in <span className="font-mono">equipment.yaml</span>.
      </p>
    );
  }

  return <Ot2Loaded snapshot={snapshot} {...{ locked, noAccess, countdown, toggle, actionError, setActionError, reportError, queryClient, selectedSlot, setSelectedSlot, declaring, setDeclaring, pending, setPending }} />;
}

// Split so all status-derived hooksless computation happens after the
// loading/error early-returns above (keeps hook order stable).
function Ot2Loaded({
  snapshot,
  locked,
  noAccess,
  countdown,
  toggle,
  actionError,
  setActionError,
  reportError,
  queryClient,
  selectedSlot,
  setSelectedSlot,
  declaring,
  setDeclaring,
  pending,
  setPending,
}: {
  snapshot: EquipmentSnapshot;
  locked: boolean;
  noAccess: boolean;
  countdown: number;
  toggle: () => Promise<void>;
  actionError: ReturnType<typeof useActionError>["actionError"];
  setActionError: ReturnType<typeof useActionError>["setActionError"];
  reportError: ReturnType<typeof useActionError>["reportError"];
  queryClient: ReturnType<typeof useQueryClient>;
  selectedSlot: number | null;
  setSelectedSlot: (s: number | null) => void;
  declaring: boolean;
  setDeclaring: (b: boolean) => void;
  pending: boolean;
  setPending: (b: boolean) => void;
}) {
  const { status } = snapshot;
  const isLiquidHandler = snapshot.kind === "liquid_handler";

  // Lab-store custom definitions → the picker's "Custom" group.
  const { data: labwareStore } = useQuery({
    queryKey: ["labware"],
    queryFn: getLabwareList,
    refetchInterval: 30000,
    enabled: isLiquidHandler,
  });
  const customEntries = useMemo(
    () => (labwareStore?.definitions ?? []).map(catalogEntryFromLabware),
    [labwareStore],
  );

  const deviceDeck = deviceDeckFromStatus(status);
  const robotModules = robotModulesFromStatus(status);
  const moduleSlots = pairModuleSlots(deviceDeck, robotModules);
  const declaredMap = deviceDeck ? declaredMapFromDeck(deviceDeck) : {};
  const tipRacks = tipRacksFromStatus(status);
  const mountedTips = mountedTipsFromStatus(status);
  const claimedBy = claimedByFromStatus(status);
  const robot = robotInfoFromStatus(status);

  const components = status.components ?? {};
  const pipLeft = components["pipette_left"];
  const pipRight = components["pipette_right"];
  const ssh = components["ssh"];
  const protocol = components["protocol"];

  const lightsRaw = components["lights"]?.state;
  const lightsOn = lightsRaw === "on";
  const lightsKnown = lightsRaw === "on" || lightsRaw === "off";
  // Gateway session state for the CONNECTED toggle: anything but
  // requires_init / unknown counts as connected (ready/busy while up).
  const deviceOn =
    status.equipment_status !== "requires_init" && status.equipment_status !== "unknown";

  const selectedView =
    selectedSlot != null ? buildSlotView(selectedSlot, deviceDeck, {}) : null;
  const selectedDeclare = selectedSlot != null ? declaredMap[String(selectedSlot)] ?? null : null;

  const mismatchSlots = deviceDeck
    ? Object.entries(deviceDeck.slots)
        .filter(([, s]) => s.slot_state === "mismatch")
        .map(([slot]) => Number(slot))
        .sort((a, b) => a - b)
    : [];

  function declare(entry: CatalogEntry | null) {
    if (locked || selectedSlot == null || declaring) return;
    setActionError(null);
    setDeclaring(true);
    // Full-layout replace: re-send every currently-declared slot (exact
    // load_names preserved by declaredMapFromDeck) with this slot updated.
    const next = nextDeclaration(declaredMap, selectedSlot, entry?.declare ?? null);
    postDeckDeclare(snapshot.id, next)
      .then(() => {
        queryClient.invalidateQueries({ queryKey: ["equipment"] });
        queryClient.invalidateQueries({ queryKey: ["equipment", snapshot.id] });
      })
      .catch((e: unknown) => reportError(e, "deck.declare"))
      .finally(() => setDeclaring(false));
  }

  function runControl(name: string, fn: () => Promise<unknown>) {
    if (locked || pending) return;
    setActionError(null);
    setPending(true);
    fn()
      .then(() => {
        queryClient.invalidateQueries({ queryKey: ["equipment"] });
        queryClient.invalidateQueries({ queryKey: ["equipment", snapshot.id] });
      })
      .catch((e: unknown) => reportError(e, name))
      .finally(() => setPending(false));
  }

  function clearAll() {
    if (locked || declaring) return;
    setActionError(null);
    setDeclaring(true);
    deleteDeckDeclare(snapshot.id)
      .then(() => {
        queryClient.invalidateQueries({ queryKey: ["equipment"] });
        queryClient.invalidateQueries({ queryKey: ["equipment", snapshot.id] });
      })
      .catch((e: unknown) => reportError(e, "deck.declare"))
      .finally(() => setDeclaring(false));
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Header strip */}
      <header className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-lg font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h2>
          <p className="truncate text-xs text-ink-subtle dark:text-slate-500">
            <span className="uppercase">{snapshot.kind}</span> ·{" "}
            <span className="font-mono">{snapshot.id}</span>
            {robot?.robot_name && (
              <>
                {" "}
                · robot <span className="font-mono">{robot.robot_name}</span>
              </>
            )}
            {robot?.api_version && (
              <>
                {" "}
                · API <span className="font-mono">{robot.api_version}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <ActionErrorBadge error={actionError} />
          <LastErrorBadge error={status.last_error} />
          <LockButton locked={locked} countdown={countdown} onToggle={toggle} noun="liquid handler" />
          <StatusPill state={status.equipment_status} />
        </div>
      </header>

      {/* Session controls — moved here from the (now read-only) tile.
          Same semantics: the toggle connects/disconnects the GATEWAY control
          session (NOT robot power); PAUSE pauses a running protocol (not an
          e-stop); Light is convenience-class (sign-in only). */}
      {isLiquidHandler && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <TileButton
            onClick={() =>
              deviceOn
                ? runControl("shutdown", () => postOt2Shutdown(snapshot.id))
                : runControl("startup", () => postOt2Startup(snapshot.id))
            }
            disabled={locked || pending}
            variant={deviceOn ? "primary" : "default"}
            title={
              locked
                ? noAccess
                  ? "No access"
                  : "Sign in to control"
                : deviceOn
                  ? "Gateway session connected — click to disconnect (does NOT power off the robot)"
                  : "Click to connect & initialize the gateway session"
            }
          >
            <span
              className={[
                "mr-1 inline-block h-2 w-2 rounded-full",
                deviceOn ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]" : "bg-slate-400",
              ].join(" ")}
              aria-hidden
            />
            {deviceOn ? "CONNECTED" : "DISCONNECTED"}
          </TileButton>
          <TileButton
            onClick={() => runControl("pause", () => postOt2Pause(snapshot.id))}
            disabled={locked || pending}
            variant="danger"
            title={
              locked
                ? noAccess
                  ? "No access"
                  : "Sign in to control"
                : "Pause a running protocol — not an emergency stop (use the robot's physical e-stop); does not disconnect"
            }
          >
            PAUSE
          </TileButton>
          <TileButton
            onClick={() => runControl("lights.set", () => postSetLights(snapshot.id, !lightsOn))}
            disabled={locked || pending}
            title={
              locked
                ? noAccess
                  ? "No access to this equipment"
                  : "Sign in to control"
                : lightsKnown
                  ? lightsOn
                    ? "Lights on — click to turn off"
                    : "Lights off — click to turn on"
                  : "Lights state not reported — click to turn on"
            }
          >
            <span
              className={[
                "mr-1.5 inline-block h-2.5 w-2.5 rounded-full",
                lightsOn
                  ? "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]"
                  : "bg-slate-900 dark:bg-black",
              ].join(" ")}
              aria-hidden
            />
            Light
          </TileButton>
        </div>
      )}

      {!isLiquidHandler && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
          <span className="font-mono">{snapshot.id}</span> is not a liquid handler — this page is the
          OT-2 interface. Its status is shown below, but the deck tools are hidden.
        </p>
      )}

      {claimedBy && (
        <p className="rounded-md border border-sky-200 bg-sky-50 px-4 py-2 text-xs text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200">
          Controlled by <span className="font-semibold">{claimedBy.owner}</span>
          {claimedBy.expires_at && (
            <>
              {" "}
              — claim expires <span className="font-mono">{claimedBy.expires_at}</span>
            </>
          )}
          . Dashboard writes will be refused (423) while the claim is held.
        </p>
      )}

      {mismatchSlots.length > 0 && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          Declared intent disagrees with the observed deck at slot
          {mismatchSlots.length > 1 ? "s" : ""} {mismatchSlots.join(", ")} — click the flagged slot
          for details.
        </p>
      )}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}

      {isLiquidHandler && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          {/* Left column: deck + declare */}
          <div className="flex flex-col gap-4">
            <Section title="Deck — declared intent vs observed hardware">
              <DeckPanel
                deviceDeck={deviceDeck}
                robotModules={robotModules}
                selectedSlot={selectedSlot}
                onSelectSlot={setSelectedSlot}
                variant="page"
              />
              {!deviceDeck && (
                <p className="mt-2 text-xs text-ink-subtle dark:text-slate-500">
                  This gateway doesn&apos;t publish a normalized deck on /status yet — deck view
                  unavailable.
                </p>
              )}
            </Section>

            {selectedView && selectedSlot != null && (
              <Section title={`Slot ${selectedSlot} detail`}>
                <div className="flex flex-col gap-1">
                  <KV k="State" v={selectedView.state} />
                  {selectedView.moduleName && <KV k="Module" v={selectedView.moduleName} />}
                  {selectedView.label && <KV k="Labware" v={selectedView.label} />}
                  {selectedView.loadName && <KV k="Load name (observed)" v={selectedView.loadName} mono />}
                  {selectedDeclare && <KV k="Declared as" v={selectedDeclare} mono />}
                  {selectedView.state === "mismatch" && selectedView.declared && (
                    <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-[11px] text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                      Mismatch: declared{" "}
                      <span className="font-mono">
                        {selectedView.declared.load_name || selectedView.declared.kind}
                      </span>{" "}
                      but observed{" "}
                      <span className="font-mono">{selectedView.loadName || selectedView.kind || "?"}</span>.
                    </p>
                  )}
                </div>
              </Section>
            )}

            <Section title="Declare deck intent">
              <DeclarePicker
                selectedSlot={selectedSlot}
                currentDeclare={selectedDeclare}
                locked={locked}
                noAccess={noAccess}
                onDeclare={declare}
                customEntries={customEntries}
              />
              <div className="mt-2 border-t border-slate-100 pt-2 dark:border-slate-800">
                <button
                  type="button"
                  disabled={locked || declaring || Object.keys(declaredMap).length === 0}
                  onClick={clearAll}
                  className="rounded-md border border-rose-300 px-2 py-1 text-xs text-rose-700 hover:border-rose-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-900 dark:text-rose-300"
                  title="Clears every operator-declared slot (observed hardware is unaffected)"
                >
                  Clear all declared intent
                </button>
              </div>
            </Section>
          </div>

          {/* Right column: robot / pipettes / modules / tips / claim */}
          <div className="flex flex-col gap-4">
            <Section title="Robot">
              <div className="flex flex-col gap-1">
                <KV k="Robot" v={robot?.robot_name ?? "—"} mono />
                <KV k="API version" v={robot?.api_version ?? "—"} mono />
                <KV
                  k="Run active"
                  v={robot?.run_active == null ? "—" : robot.run_active ? "yes" : "no"}
                />
                <div className="mt-1 flex items-center gap-3">
                  <span className="flex items-center gap-1.5 text-xs text-ink-subtle dark:text-slate-400">
                    <Dot ok={ssh?.state === "connected" || ssh?.state === "ready"} /> SSH{" "}
                    <span className="font-mono">{ssh?.state ?? "—"}</span>
                  </span>
                  <span className="flex items-center gap-1.5 text-xs text-ink-subtle dark:text-slate-400">
                    <Dot ok={protocol?.state === "connected" || protocol?.state === "ready"} />{" "}
                    Protocol <span className="font-mono">{protocol?.state ?? "—"}</span>
                  </span>
                </div>
              </div>
            </Section>

            <Section title="Pipettes">
              <div className="flex flex-col gap-1">
                <KV k="Left mount" v={pipetteLabel(pipLeft?.state)} />
                <KV k="Right mount" v={pipetteLabel(pipRight?.state)} />
              </div>
            </Section>

            <Section title="Modules (live telemetry)">
              {moduleSlots.size === 0 && robotModules.length === 0 ? (
                <p className="text-xs text-ink-subtle dark:text-slate-500">
                  No modules on the deck or attached.
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {Array.from(moduleSlots.entries())
                    .sort(([a], [b]) => a - b)
                    .map(([slot, m]) => (
                      <li
                        key={slot}
                        className="flex items-center justify-between gap-2 rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-800"
                      >
                        <span className="min-w-0 truncate text-xs text-ink dark:text-slate-200">
                          <span className="font-semibold">Slot {slot}</span> · {m.name}
                        </span>
                        <ModuleReadout live={m.live} compact />
                      </li>
                    ))}
                </ul>
              )}
            </Section>

            <Section title="Tip racks">
              {tipRacks.length === 0 ? (
                <p className="text-xs text-ink-subtle dark:text-slate-500">
                  No tracked tip racks (register via a workflow&apos;s{" "}
                  <span className="font-mono">tips.reset</span>).
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {tipRacks.map((r) => (
                    <li key={r.nickname} className="rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-800">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="min-w-0 truncate font-mono text-xs text-ink dark:text-slate-200">
                          {r.nickname}
                        </span>
                        <span className="shrink-0 text-xs tabular-nums text-ink-subtle dark:text-slate-400">
                          {r.available}/{r.total} available
                        </span>
                      </div>
                      {(r.empty > 0 || r.touched > 0) && (
                        <p className="mt-0.5 text-[10px] text-ink-subtle dark:text-slate-500">
                          {r.empty} used · {r.touched} touched
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Mounted tips">
              {mountedTips.length === 0 ? (
                <p className="text-xs text-ink-subtle dark:text-slate-500">No tip currently mounted.</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {mountedTips.map((t) => (
                    <li key={t.pipette} className="text-xs text-ink dark:text-slate-200">
                      <span className="font-semibold">{t.pipette}</span>:{" "}
                      <span className="font-mono">
                        {t.rack ?? "?"} {t.well ?? ""}
                      </span>
                      {t.last_sample && (
                        <span className="text-ink-subtle dark:text-slate-400">
                          {" "}
                          · last sample <span className="font-mono">{t.last_sample}</span>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Claim">
              {claimedBy ? (
                <div className="flex flex-col gap-1">
                  <KV k="Holder" v={claimedBy.owner} mono />
                  <KV k="Session" v={claimedBy.session_id || "—"} mono />
                  <KV k="Expires" v={claimedBy.expires_at || "—"} mono />
                </div>
              ) : (
                <p className="text-xs text-ink-subtle dark:text-slate-500">
                  No claim held — dashboard writes acquire a short per-request claim automatically.
                </p>
              )}
            </Section>
          </div>
        </div>
      )}

      {/* Footer strip */}
      <footer className="flex items-end justify-between gap-2 border-t border-slate-100 pt-2 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-400">
        <div className="min-w-0 flex-1 space-y-0.5">
          {status.message && (
            <div className="truncate" title={status.message}>
              {status.message}
            </div>
          )}
          {(status.required_actions?.length ?? 0) > 0 && (
            <div className="truncate">
              <span className="font-semibold text-amber-700 dark:text-amber-400">Action needed:</span>{" "}
              <span className="font-mono">{status.required_actions?.join(", ")}</span>
            </div>
          )}
          <Link
            href="/"
            className="text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
          >
            ← Back to overview
          </Link>
        </div>
        <div className="flex shrink-0 items-center gap-2 tabular-nums">
          {snapshot.latency_ms != null && <span>{snapshot.latency_ms} ms</span>}
          <StalenessIndicator fetchedAt={snapshot.fetched_at} />
        </div>
      </footer>
    </div>
  );
}
