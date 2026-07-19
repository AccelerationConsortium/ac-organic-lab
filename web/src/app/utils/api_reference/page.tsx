"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface JsonSchemaProperty {
  type?: string | string[];
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  items?: { type?: string };
}

interface JsonSchema {
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  title?: string;
  description?: string;
}

interface ActionDef {
  name: string;
  description: string;
  method: string;
  endpoint: string;
  args_schema: JsonSchema;
  requires_states: string[];
  estimated_duration_s: number | null;
}

interface InstrumentCatalog {
  id: string;
  name: string;
  kind: string;
  adapter: string;
  base_url: string;
  protocol: string;
  actions: ActionDef[];
}

interface PlatformCatalog {
  label: string;
  instruments: InstrumentCatalog[];
}

interface CatalogResponse {
  platforms: Record<string, PlatformCatalog>;
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchCatalog(): Promise<CatalogResponse> {
  const res = await fetch("/api/catalog", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<CatalogResponse>;
}

function useCatalog() {
  return useQuery<CatalogResponse, Error>({
    queryKey: ["catalog"],
    queryFn: fetchCatalog,
    staleTime: Infinity, // catalog is static — no polling needed
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const METHOD_BADGE: Record<string, string> = {
  POST:   "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  GET:    "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  DELETE: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
};

const ADAPTER_BADGE: Record<string, string> = {
  http:        "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300",
  legacy_http: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
  mock:        "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

function methodBadge(method: string) {
  return METHOD_BADGE[method] ?? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
}

function adapterBadge(adapter: string) {
  return ADAPTER_BADGE[adapter] ?? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400";
}

function typeLabel(prop: JsonSchemaProperty): string {
  if (!prop.type) return "any";
  const t = Array.isArray(prop.type) ? prop.type.filter((x) => x !== "null").join(" | ") : prop.type;
  if (prop.enum) return prop.enum.map(String).join(" | ");
  if (t === "array" && prop.items?.type) return `${prop.items.type}[]`;
  if (prop.minimum !== undefined && prop.maximum !== undefined)
    return `${t} [${prop.minimum}–${prop.maximum}]`;
  return t;
}

// ---------------------------------------------------------------------------
// Schema table
// ---------------------------------------------------------------------------

function SchemaTable({ schema, actionName }: { schema: JsonSchema; actionName: string }) {
  const props = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const entries = Object.entries(props);

  if (entries.length === 0) {
    return (
      <p className="text-xs italic text-ink-subtle dark:text-slate-500">No parameters.</p>
    );
  }

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-slate-100 dark:border-slate-800">
          <th className="pb-1 pr-3 text-left font-medium text-ink-subtle dark:text-slate-500">
            Field
          </th>
          <th className="pb-1 pr-3 text-left font-medium text-ink-subtle dark:text-slate-500">
            Type
          </th>
          <th className="pb-1 text-left font-medium text-ink-subtle dark:text-slate-500">
            Notes
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-50 dark:divide-slate-800/60">
        {entries.map(([field, prop]) => (
          <tr key={`${actionName}-${field}`}>
            <td className="py-1 pr-3 font-mono font-medium text-ink dark:text-slate-200">
              {field}
              {!required.has(field) && (
                <span className="ml-1 font-sans font-normal text-ink-subtle dark:text-slate-500">
                  ?
                </span>
              )}
            </td>
            <td className="py-1 pr-3 font-mono text-sky-700 dark:text-sky-400">
              {typeLabel(prop)}
            </td>
            <td className="py-1 text-ink-muted dark:text-slate-400">
              {prop.description ?? ""}
              {prop.default !== undefined && (
                <span className="ml-1 text-ink-subtle dark:text-slate-500">
                  default: <span className="font-mono">{String(prop.default)}</span>
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Single action row (expandable)
// ---------------------------------------------------------------------------

function ActionRow({ action }: { action: ActionDef }) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <button
        onClick={() => setOpen((p) => !p)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50"
      >
        {/* Method badge */}
        <span
          className={`w-12 shrink-0 rounded px-1.5 py-0.5 text-center font-mono text-[10px] font-semibold ${methodBadge(action.method)}`}
        >
          {action.method}
        </span>

        {/* Endpoint */}
        <span className="w-44 shrink-0 truncate font-mono text-xs text-ink dark:text-slate-200">
          {action.endpoint}
        </span>

        {/* Description */}
        <span className="flex-1 truncate text-xs text-ink-muted dark:text-slate-400">
          {action.description}
        </span>

        {/* Duration */}
        {action.estimated_duration_s !== null && (
          <span className="hidden shrink-0 text-xs tabular-nums text-ink-subtle dark:text-slate-500 sm:block">
            ~{action.estimated_duration_s}s
          </span>
        )}

        {/* Chevron */}
        <span className="ml-1 text-xs text-ink-subtle dark:text-slate-500">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div className="border-t border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/50">
          {/* States that allow this action */}
          {action.requires_states.length > 0 && (
            <div className="mb-3 flex items-center gap-2 flex-wrap">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-subtle dark:text-slate-500">
                Allowed in
              </span>
              {action.requires_states.map((s) => (
                <span
                  key={s}
                  className="rounded-full bg-slate-200 px-2 py-0.5 font-mono text-[10px] text-ink dark:bg-slate-700 dark:text-slate-300"
                >
                  {s}
                </span>
              ))}
            </div>
          )}

          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-ink-subtle dark:text-slate-500">
            Request body
          </p>
          <SchemaTable schema={action.args_schema} actionName={action.name} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Instrument tile (card with collapsible action list)
// ---------------------------------------------------------------------------

function InstrumentCard({ instrument }: { instrument: InstrumentCatalog }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
      {/* Instrument header — acts as the toggle */}
      <button
        onClick={() => setOpen((p) => !p)}
        className="flex w-full items-center justify-between gap-3 bg-slate-50 px-4 py-3 text-left transition-colors hover:bg-slate-100 dark:bg-slate-900/60 dark:hover:bg-slate-800/80"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink dark:text-slate-100">
              {instrument.name}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${adapterBadge(instrument.adapter)}`}
            >
              {instrument.adapter}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-subtle dark:text-slate-500">
            <span className="font-mono">{instrument.id}</span>
            <span>·</span>
            <span className="font-mono">{instrument.kind}</span>
            {instrument.base_url && (
              <>
                <span>·</span>
                <span className="font-mono truncate max-w-[200px]">{instrument.base_url}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs text-ink-subtle dark:text-slate-500">
            {instrument.actions.length} action{instrument.actions.length !== 1 ? "s" : ""}
          </span>
          <span className="text-xs text-ink-subtle dark:text-slate-500">
            {open ? "▲" : "▼"}
          </span>
        </div>
      </button>

      {open && (
        <>
          {/* Column header */}
          <div className="flex items-center gap-3 border-b border-slate-100 bg-white px-4 py-1.5 dark:border-slate-800 dark:bg-slate-950/30">
            <span className="w-12 shrink-0 text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">
              Method
            </span>
            <span className="w-44 shrink-0 text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">
              Endpoint
            </span>
            <span className="flex-1 text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">
              Description
            </span>
            <span className="hidden w-10 shrink-0 text-right text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500 sm:block">
              ~s
            </span>
            <span className="ml-1 w-3 shrink-0" />
          </div>

          <div className="divide-y divide-slate-100 bg-white dark:divide-slate-800 dark:bg-slate-950/20">
            {instrument.actions.length === 0 ? (
              <p className="px-4 py-3 text-xs italic text-ink-subtle dark:text-slate-500">
                No registered actions for this kind yet.
              </p>
            ) : (
              instrument.actions.map((action) => (
                <ActionRow key={action.name} action={action} />
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Platform tile — matches History page PlatformGroup card style
// ---------------------------------------------------------------------------

function PlatformTile({
  platformId,
  catalog,
}: {
  platformId: string;
  catalog: PlatformCatalog;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
      {/* Platform header */}
      <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-900/60">
        <h4 className="text-xs font-semibold uppercase tracking-widest text-ink-muted dark:text-slate-400">
          {catalog.label}
        </h4>
        <span className="text-xs text-ink-subtle dark:text-slate-500">
          {catalog.instruments.length} instrument{catalog.instruments.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="flex flex-col gap-3 bg-white p-4 dark:bg-slate-950/20">
        {catalog.instruments.map((inst) => (
          <InstrumentCard key={inst.id} instrument={inst} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ApiReferencePage() {
  const { data, isPending, error } = useCatalog();

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading catalog…</p>;
  }

  if (error) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load catalog: {error.message}
      </p>
    );
  }

  const platforms = Object.entries(data?.platforms ?? {});

  return (
    <div className="flex flex-col gap-6">
      <header>
        <p className="text-sm text-ink-muted dark:text-slate-400">
          Static skill catalog — all registered instrument actions grouped by platform.
          Click an instrument to expand its actions; click an action for parameter details.
        </p>
      </header>

      {platforms.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center dark:border-slate-700">
          <p className="text-sm font-medium text-ink-muted dark:text-slate-400">
            No platforms in catalog.
          </p>
          <p className="mt-1 text-xs text-ink-subtle dark:text-slate-500">
            Add equipment entries in <span className="font-mono">equipment.yaml</span>.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {platforms.map(([id, catalog]) => (
            <PlatformTile key={id} platformId={id} catalog={catalog} />
          ))}
        </div>
      )}
    </div>
  );
}
