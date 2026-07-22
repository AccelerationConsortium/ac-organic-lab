"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteLabware,
  getLabwareDefinition,
  getLabwareList,
  getStandardLabwareDefinition,
  getStandardLabwareList,
  postLabware,
  ApiError,
  type LabwareSummary,
} from "@/lib/api";
import {
  buildDefinition,
  defaultSpec,
  specFromDefinition,
  validateSpec,
  type DisplayCategory,
  type LabwareSpec,
  type ValidationIssue,
} from "@/lib/labware-schema";
import { useUserAuth } from "@/lib/user-auth";

/**
 * Custom labware builder + the central definition library.
 *
 * Builds Opentrons schema-2 labware definition JSON from a parametric form,
 * with live top-down preview and the OT-2 slot-envelope validation. Anyone
 * signed out can build + download; saving to the shared lab store
 * (POST /api/labware) is admin-only, and repo-committed definitions are
 * immutable here (change via PR). See docs/UI_DESIGN.md §1.
 */

function Field({
  label,
  issue,
  children,
}: {
  label: string;
  issue?: ValidationIssue;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-0.5 text-xs">
      <span className="text-ink-subtle dark:text-slate-400">{label}</span>
      {children}
      {issue && <span className="text-rose-600 dark:text-rose-400">{issue.message}</span>}
    </label>
  );
}

const inputCls =
  "rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

function NumberInput({
  value,
  onChange,
  step = 0.01,
}: {
  value: number | undefined;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <input
      type="number"
      step={step}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.valueAsNumber)}
      className={inputCls}
    />
  );
}

// Top-down SVG preview in real proportions (row A at the top = back edge).
function Preview({ spec }: { spec: LabwareSpec }) {
  const wells = [];
  const halfX =
    spec.wellShape === "circular" ? (spec.wellDiameter ?? 0) / 2 : (spec.wellXDimension ?? 0) / 2;
  const halfY =
    spec.wellShape === "circular" ? (spec.wellDiameter ?? 0) / 2 : (spec.wellYDimension ?? 0) / 2;
  for (let row = 0; row < Math.min(spec.rows, 40); row++) {
    for (let col = 0; col < Math.min(spec.columns, 40); col++) {
      const cx = spec.offsetA1X + col * spec.spacingX;
      const cy = spec.offsetA1Y + row * spec.spacingY; // SVG y-down == back→front
      wells.push(
        spec.wellShape === "circular" ? (
          <circle
            key={`${row}-${col}`}
            cx={cx}
            cy={cy}
            r={halfX}
            className="fill-sky-200 stroke-sky-600 dark:fill-sky-900 dark:stroke-sky-400"
            strokeWidth={0.4}
          />
        ) : (
          <rect
            key={`${row}-${col}`}
            x={cx - halfX}
            y={cy - halfY}
            width={halfX * 2}
            height={halfY * 2}
            className="fill-sky-200 stroke-sky-600 dark:fill-sky-900 dark:stroke-sky-400"
            strokeWidth={0.4}
          />
        ),
      );
    }
  }
  return (
    <svg
      viewBox={`-2 -2 ${spec.footprintX + 4} ${spec.footprintY + 4}`}
      className="w-full rounded border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
      role="img"
      aria-label="Top-down labware preview"
    >
      <rect
        x={0}
        y={0}
        width={spec.footprintX}
        height={spec.footprintY}
        className="fill-slate-50 stroke-slate-400 dark:fill-slate-800 dark:stroke-slate-500"
        strokeWidth={0.6}
        rx={2}
      />
      {wells}
    </svg>
  );
}

// Cross-section (front elevation): body height = footprintZ, well cavities cut
// from the top down `wellDepth` at each column's x position. The bottom-shape
// hint is drawn on the cavity floor (flat / rounded / conical).
function SideView({ spec }: { spec: LabwareSpec }) {
  const halfX =
    spec.wellShape === "circular" ? (spec.wellDiameter ?? 0) / 2 : (spec.wellXDimension ?? 0) / 2;
  const depth = Math.min(spec.wellDepth, spec.footprintZ);
  const cavities = [];
  for (let col = 0; col < Math.min(spec.columns, 40); col++) {
    const cx = spec.offsetA1X + col * spec.spacingX;
    const x0 = cx - halfX;
    const w = halfX * 2;
    const floorY = depth;
    let floor = null;
    if (spec.wellBottomShape === "v") {
      floor = (
        <polygon
          points={`${x0},${floorY} ${cx},${Math.min(floorY + w / 2, spec.footprintZ)} ${x0 + w},${floorY}`}
          className="fill-white dark:fill-slate-900"
        />
      );
    } else if (spec.wellBottomShape === "u") {
      floor = (
        <ellipse
          cx={cx}
          cy={floorY}
          rx={halfX}
          ry={Math.min(halfX, spec.footprintZ - floorY + halfX)}
          className="fill-white dark:fill-slate-900"
        />
      );
    }
    cavities.push(
      <g key={col}>
        <rect x={x0} y={0} width={w} height={floorY} className="fill-white dark:fill-slate-900" />
        {floor}
      </g>,
    );
  }
  return (
    <svg
      viewBox={`-2 -2 ${spec.footprintX + 4} ${spec.footprintZ + 4}`}
      className="w-full rounded border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
      role="img"
      aria-label="Side-view labware cross-section"
    >
      <rect
        x={0}
        y={0}
        width={spec.footprintX}
        height={spec.footprintZ}
        className="fill-slate-200 stroke-slate-400 dark:fill-slate-700 dark:stroke-slate-500"
        strokeWidth={0.6}
      />
      {cavities}
      <rect
        x={0}
        y={0}
        width={spec.footprintX}
        height={spec.footprintZ}
        className="fill-none stroke-slate-400 dark:stroke-slate-500"
        strokeWidth={0.6}
      />
    </svg>
  );
}

/** What the form is currently editing (loaded from the store) — or null for a
 *  from-scratch definition. */
interface EditingSource {
  loadName: string;
  source: "repo" | "uploaded" | "standard";
  warnings: string[];
}

// How many standard-library rows to render before asking for a narrower
// search (the shared-data package ships ~140 definitions).
const STANDARD_LIST_CAP = 30;

export default function LabwareBuilderPage() {
  const { identity } = useUserAuth();
  const isAdmin = identity?.role === "admin";
  const queryClient = useQueryClient();
  const [spec, setSpec] = useState<LabwareSpec>(defaultSpec());
  const [showJson, setShowJson] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<EditingSource | null>(null);

  const { data: library } = useQuery({
    queryKey: ["labware"],
    queryFn: getLabwareList,
    refetchInterval: 30000,
  });
  // The full official Opentrons library (static per deploy — no refetch).
  const { data: standardLibrary } = useQuery({
    queryKey: ["labware-standard"],
    queryFn: getStandardLabwareList,
    staleTime: Infinity,
  });
  const [standardQuery, setStandardQuery] = useState("");

  const issues = useMemo(() => validateSpec(spec), [spec]);
  const issueFor = (field: ValidationIssue["field"]) => issues.find((i) => i.field === field);
  const valid = issues.length === 0;
  const definition = useMemo(
    () => (valid ? buildDefinition(spec) : null),
    [valid, spec],
  );

  function set<K extends keyof LabwareSpec>(key: K, value: LabwareSpec[K]) {
    setSaveMsg(null);
    setSpec((s) => ({ ...s, [key]: value }));
  }

  function download() {
    if (!definition) return;
    const blob = new Blob([JSON.stringify(definition, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${spec.loadName}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function saveToStore() {
    if (!definition || busy) return;
    setBusy(true);
    setSaveMsg(null);
    postLabware(definition)
      .then((summary) => {
        setSaveMsg({ ok: true, text: `Saved ${summary.load_name} to the lab store.` });
        setEditing({ loadName: summary.load_name, source: "uploaded", warnings: [] });
        queryClient.invalidateQueries({ queryKey: ["labware"] });
      })
      .catch((e: unknown) => {
        const detail =
          e instanceof ApiError && e.body && typeof e.body === "object"
            ? JSON.stringify((e.body as { detail?: unknown }).detail)
            : e instanceof Error
              ? e.message
              : String(e);
        setSaveMsg({ ok: false, text: `Save failed: ${detail}` });
      })
      .finally(() => setBusy(false));
  }

  function loadForEditing(summary: LabwareSummary) {
    setSaveMsg(null);
    const fetchDefn =
      summary.source === "standard"
        ? getStandardLabwareDefinition(summary.load_name)
        : getLabwareDefinition(summary.load_name);
    fetchDefn
      .then(({ definition }) => {
        const { spec: loaded, warnings } = specFromDefinition(definition);
        setSpec(loaded);
        setEditing({ loadName: summary.load_name, source: summary.source, warnings });
      })
      .catch((e: unknown) =>
        setSaveMsg({
          ok: false,
          text: `Load failed: ${e instanceof Error ? e.message : String(e)}`,
        }),
      );
  }

  function startBlank() {
    setSpec(defaultSpec());
    setEditing(null);
    setSaveMsg(null);
  }

  // Repo-committed and standard load names can't be uploaded over (the API
  // 409s) — saving a variant requires a new load name.
  const protectedNameCollision =
    editing != null && editing.source !== "uploaded" && spec.loadName === editing.loadName;

  const standardMatches = (standardLibrary?.definitions ?? []).filter((d) => {
    const q = standardQuery.trim().toLowerCase();
    return (
      !q || d.load_name.toLowerCase().includes(q) || d.display_name.toLowerCase().includes(q)
    );
  });

  function removeUploaded(loadName: string) {
    deleteLabware(loadName)
      .then(() => queryClient.invalidateQueries({ queryKey: ["labware"] }))
      .catch((e: unknown) =>
        setSaveMsg({
          ok: false,
          text: `Delete failed: ${e instanceof Error ? e.message : String(e)}`,
        }),
      );
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h2 className="text-lg font-semibold text-ink dark:text-slate-100">
          Custom labware builder
        </h2>
        <p className="text-xs text-ink-subtle dark:text-slate-500">
          Builds an Opentrons <span className="font-mono">schema-2</span> definition JSON.
          Anyone can download the file; saving to the shared lab store is{" "}
          <span className="font-semibold">admin-only</span>, and repo-committed definitions
          change via PR. Building a definition does not load anything on a robot.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {/* Form */}
        <section className="rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          {editing && (
            <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-sky-200 bg-sky-50 px-2 py-1.5 text-xs text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200">
              <span>
                Editing <span className="font-mono">{editing.loadName}</span> —{" "}
                {editing.source === "uploaded" ? (
                  <span className="font-semibold">custom (uploaded)</span>
                ) : editing.source === "repo" ? (
                  <span className="font-semibold">repo-committed</span>
                ) : (
                  <span className="font-semibold">standard (Opentrons library)</span>
                )}
                {editing.source === "repo" &&
                  " · changes to the original go via PR; pick a new load name to save a variant"}
                {editing.source === "standard" &&
                  " · built into the robot; pick a new load name to save a variant"}
              </span>
              <button
                type="button"
                onClick={startBlank}
                className="ml-auto rounded border border-sky-300 px-1.5 py-0.5 text-[10px] font-semibold hover:bg-sky-100 dark:border-sky-800 dark:hover:bg-sky-900/40"
              >
                Start blank
              </button>
              {editing.warnings.map((w) => (
                <p key={w} className="w-full text-[10px] text-amber-700 dark:text-amber-400">
                  ⚠ {w}
                </p>
              ))}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Field label="Load name (exact)" issue={issueFor("loadName")}>
              <input
                type="text"
                value={spec.loadName}
                onChange={(e) => set("loadName", e.target.value)}
                placeholder="matterlab_24_vialplate_2ml"
                className={`${inputCls} font-mono`}
              />
            </Field>
            <Field label="Display name" issue={issueFor("displayName")}>
              <input
                type="text"
                value={spec.displayName}
                onChange={(e) => set("displayName", e.target.value)}
                placeholder="MatterLab 24 vial plate 2 mL"
                className={inputCls}
              />
            </Field>
            <Field label="Brand" issue={issueFor("brand")}>
              <input
                type="text"
                value={spec.brand}
                onChange={(e) => set("brand", e.target.value)}
                placeholder="MatterLab"
                className={inputCls}
              />
            </Field>
            <Field label="Category">
              <select
                value={spec.displayCategory}
                onChange={(e) => set("displayCategory", e.target.value as DisplayCategory)}
                className={inputCls}
              >
                <option value="wellPlate">Well plate</option>
                <option value="reservoir">Reservoir</option>
                <option value="tipRack">Tip rack</option>
                <option value="tubeRack">Tube rack</option>
              </select>
            </Field>
            <Field label="Rows" issue={issueFor("rows")}>
              <NumberInput value={spec.rows} step={1} onChange={(v) => set("rows", v)} />
            </Field>
            <Field label="Columns" issue={issueFor("columns")}>
              <NumberInput value={spec.columns} step={1} onChange={(v) => set("columns", v)} />
            </Field>
            <Field label="Footprint X (mm, ≤127)" issue={issueFor("footprintX")}>
              <NumberInput value={spec.footprintX} onChange={(v) => set("footprintX", v)} />
            </Field>
            <Field label="Footprint Y (mm, ≤85.5)" issue={issueFor("footprintY")}>
              <NumberInput value={spec.footprintY} onChange={(v) => set("footprintY", v)} />
            </Field>
            <Field label="Height Z (mm, ≤200)" issue={issueFor("footprintZ")}>
              <NumberInput value={spec.footprintZ} onChange={(v) => set("footprintZ", v)} />
            </Field>
            <Field label="A1 offset from left (mm)" issue={issueFor("offsetA1X")}>
              <NumberInput value={spec.offsetA1X} onChange={(v) => set("offsetA1X", v)} />
            </Field>
            <Field label="A1 offset from back (mm)" issue={issueFor("offsetA1Y")}>
              <NumberInput value={spec.offsetA1Y} onChange={(v) => set("offsetA1Y", v)} />
            </Field>
            <Field label="Spacing X (mm)" issue={issueFor("spacingX")}>
              <NumberInput value={spec.spacingX} onChange={(v) => set("spacingX", v)} />
            </Field>
            <Field label="Spacing Y (mm)" issue={issueFor("spacingY")}>
              <NumberInput value={spec.spacingY} onChange={(v) => set("spacingY", v)} />
            </Field>
            <Field label="Well shape">
              <select
                value={spec.wellShape}
                onChange={(e) => set("wellShape", e.target.value as LabwareSpec["wellShape"])}
                className={inputCls}
              >
                <option value="circular">Circular</option>
                <option value="rectangular">Rectangular</option>
              </select>
            </Field>
            {spec.wellShape === "circular" ? (
              <Field label="Well diameter (mm)" issue={issueFor("wellDiameter")}>
                <NumberInput value={spec.wellDiameter} onChange={(v) => set("wellDiameter", v)} />
              </Field>
            ) : (
              <>
                <Field label="Well X size (mm)" issue={issueFor("wellXDimension")}>
                  <NumberInput
                    value={spec.wellXDimension}
                    onChange={(v) => set("wellXDimension", v)}
                  />
                </Field>
                <Field label="Well Y size (mm)" issue={issueFor("wellYDimension")}>
                  <NumberInput
                    value={spec.wellYDimension}
                    onChange={(v) => set("wellYDimension", v)}
                  />
                </Field>
              </>
            )}
            <Field label="Well depth (mm)" issue={issueFor("wellDepth")}>
              <NumberInput value={spec.wellDepth} onChange={(v) => set("wellDepth", v)} />
            </Field>
            <Field label="Well volume (µL)" issue={issueFor("wellVolumeUl")}>
              <NumberInput value={spec.wellVolumeUl} onChange={(v) => set("wellVolumeUl", v)} />
            </Field>
            <Field label="Well bottom">
              <select
                value={spec.wellBottomShape}
                onChange={(e) =>
                  set("wellBottomShape", e.target.value as LabwareSpec["wellBottomShape"])
                }
                className={inputCls}
              >
                <option value="flat">Flat</option>
                <option value="u">U (round)</option>
                <option value="v">V (conical)</option>
              </select>
            </Field>
            {spec.displayCategory === "tipRack" && (
              <Field label="Tip length (mm)" issue={issueFor("tipLength")}>
                <NumberInput value={spec.tipLength} onChange={(v) => set("tipLength", v)} />
              </Field>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
            <button
              type="button"
              onClick={download}
              disabled={!valid}
              className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Download JSON
            </button>
            <button
              type="button"
              onClick={saveToStore}
              disabled={!valid || !isAdmin || busy || protectedNameCollision}
              title={
                protectedNameCollision
                  ? "This load name belongs to a standard / repo-committed definition — pick a new load name to save a variant"
                  : isAdmin
                    ? "Save to the shared lab store (available in the OT-2 deck picker)"
                    : "Admin-only — download the JSON and ask an admin to add it (or open a PR to labware/)"
              }
              className="rounded-md border border-sky-600 px-3 py-1.5 text-xs font-semibold text-sky-700 hover:bg-sky-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-sky-300 dark:hover:bg-sky-950/40"
            >
              Save to lab store
            </button>
            <button
              type="button"
              onClick={() => setShowJson((v) => !v)}
              disabled={!valid}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-ink hover:border-slate-400 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200"
            >
              {showJson ? "Hide JSON" : "Show JSON"}
            </button>
            {!valid && (
              <span className="text-xs text-rose-600 dark:text-rose-400">
                {issues.length} problem{issues.length > 1 ? "s" : ""} to fix
              </span>
            )}
            {saveMsg && (
              <span
                className={`text-xs ${
                  saveMsg.ok
                    ? "text-emerald-700 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400"
                }`}
              >
                {saveMsg.text}
              </span>
            )}
          </div>

          {showJson && definition && (
            <pre className="mt-3 max-h-72 overflow-auto rounded-md bg-slate-50 p-2 text-[10px] leading-tight text-ink dark:bg-slate-950 dark:text-slate-300">
              {JSON.stringify(definition, null, 2)}
            </pre>
          )}
        </section>

        {/* Preview + library */}
        <div className="flex flex-col gap-4">
          <section className="rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">
              Preview (top-down, to scale)
            </h3>
            <Preview spec={spec} />
            <p className="mt-1 text-[10px] text-ink-subtle dark:text-slate-500">
              Row A is at the top (the deck&apos;s back edge). {spec.rows} × {spec.columns} ={" "}
              {spec.rows * spec.columns} wells.
            </p>
            <h3 className="mb-2 mt-3 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">
              Side view (front elevation, to scale)
            </h3>
            <SideView spec={spec} />
            <p className="mt-1 text-[10px] text-ink-subtle dark:text-slate-500">
              Height {spec.footprintZ} mm, wells {spec.wellDepth} mm deep ({spec.wellBottomShape}{" "}
              bottom).
            </p>
          </section>

          <section className="rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">
              Definition library
            </h3>
            {!library?.definitions?.length ? (
              <p className="text-xs text-ink-subtle dark:text-slate-500">
                No custom definitions in the lab store yet — build one here, or start from a
                standard template below.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {library.definitions.map((d: LabwareSummary) => (
                  <li
                    key={d.load_name}
                    className="flex items-center justify-between gap-2 rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-800"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-xs text-ink dark:text-slate-200">
                        {d.display_name}
                      </p>
                      <p className="truncate font-mono text-[10px] text-ink-subtle dark:text-slate-500">
                        {d.load_name} · {d.rows}×{d.columns}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                          d.source === "repo"
                            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300"
                            : "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-300"
                        }`}
                        title={
                          d.source === "repo"
                            ? "Custom definition committed to the repo (change via PR)"
                            : "Custom definition uploaded via this page"
                        }
                      >
                        {d.source === "uploaded" ? "custom · uploaded" : "custom · repo"}
                      </span>
                      <button
                        type="button"
                        onClick={() => loadForEditing(d)}
                        title="Load this definition into the form to modify it"
                        className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-ink hover:border-slate-400 dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500"
                      >
                        Load
                      </button>
                      {isAdmin && d.source === "uploaded" && (
                        <button
                          type="button"
                          onClick={() => removeUploaded(d.load_name)}
                          className="rounded border border-rose-300 px-1.5 py-0.5 text-[10px] text-rose-700 hover:border-rose-400 dark:border-rose-900 dark:text-rose-300"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}

            <h4 className="mb-1.5 mt-4 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-500">
              Standard Opentrons library
              {standardLibrary ? ` (${standardLibrary.definitions.length})` : " (loading…)"}
            </h4>
            <input
              type="search"
              value={standardQuery}
              onChange={(e) => setStandardQuery(e.target.value)}
              placeholder="Search official definitions (e.g. nest, falcon, tiprack)…"
              aria-label="Search standard labware"
              className="mb-1.5 w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink placeholder:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
            <ul className="flex max-h-72 flex-col gap-1.5 overflow-y-auto">
              {standardMatches.slice(0, STANDARD_LIST_CAP).map((d) => (
                <li
                  key={d.load_name}
                  className="flex items-center justify-between gap-2 rounded-md border border-slate-200 px-2 py-1.5 dark:border-slate-800"
                >
                  <div className="min-w-0">
                    <p className="truncate text-xs text-ink dark:text-slate-200">
                      {d.display_name}
                    </p>
                    <p className="truncate font-mono text-[10px] text-ink-subtle dark:text-slate-500">
                      {d.load_name} · {d.rows}×{d.columns}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span
                      className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-slate-600 dark:bg-slate-800 dark:text-slate-400"
                      title="Official Opentrons definition (opentrons-shared-data) — exact geometry"
                    >
                      standard
                    </span>
                    <button
                      type="button"
                      onClick={() => loadForEditing(d)}
                      title="Load the exact official geometry into the form to modify it"
                      className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-ink hover:border-slate-400 dark:border-slate-700 dark:text-slate-200 dark:hover:border-slate-500"
                    >
                      Load
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            {standardMatches.length > STANDARD_LIST_CAP && (
              <p className="mt-1 text-[10px] text-ink-subtle dark:text-slate-500">
                +{standardMatches.length - STANDARD_LIST_CAP} more — refine the search.
              </p>
            )}
            {standardLibrary && standardMatches.length === 0 && (
              <p className="text-[10px] text-ink-subtle dark:text-slate-500">
                No official definition matches “{standardQuery}”.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
