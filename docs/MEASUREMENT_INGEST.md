# Measurement Ingest — instrument → data server → record layer

**Status:** design note (2026-07-26). Not agreed, not built, not yet wired into
the README docs router. Captures a discussion about how HPLC-MS acquisitions
become queryable `Measurement` records in AnaliticaDB. Companion to
[`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) (the record layer) and
[`AGENTIC_ELN_DESIGN.md`](AGENTIC_ELN_DESIGN.md) §6 (the Analyze surface that
consumes what this produces).

## 1. The problem

The chain today:

```
agilent-hplcms-server → moses → run
                                 ↓
                        robocopy → data server
                                 ↓
                        ??? ← nothing joins the file to a record
                                 ↓
                 LaAgenteAnalitica / bitacora picks it up
```

The missing link is filled by **a human**: someone browses the Data Catalog,
recognises their file, and stages it into a room. That works for a chemist and
fails for a closed loop — an agent asking "give me the measurements for plan X
step 3" gets rows with no data attached, and files with no rows.

## 2. What exists today

**`agilent-hplcms-server`** (instrument PC `sdl2-pc-06-uplc`, STATUS_SPEC v1.2)
has **no AnaliticaDB awareness at all**. Its dependencies are
`sdl-lab-contract`, fastapi, pydantic, psutil. It owns the queue, the claim,
and the run; it records nothing.

What it *does* know, from `RunRequest` / `GradientConfig` / `SampleConfig` and
the claim:

| Fact | Available? |
|---|---|
| Operator | ✅ claim owner, validated against the central roster, with a role |
| Acquisition params | ✅ gradient table, flow rate, run time, MS mode, injection volume, config path |
| Sample | ◑ `sample_name`, `tray`, `well` — no AnaliticaDB `sample_id` |
| Project | ❌ not in `RunRequest` |
| Experiment / plan / step | ❌ not in `RunRequest` |
| Outcome | ✅ authoritative — `moses.agilent start_run` blocks, so process exit decides (0 → done, non-zero → failed) |
| Result location | ✅ `output_dir` |

**The record write currently lives in a UI.** GraphChat's backend
(`graphchat/packages/backend/src/instruments/agilentRoutes.ts`) writes one
*"submitted" header* `Measurement` per queued injection at submit time,
deliberately ("a measurement without a completed run is a legitimate state").
Consequences:

- A robot or workflow POSTing `/control/queue` directly produces **no record**.
- Column and DAD wavelengths are **hardcoded constants** in that TS route, with
  a comment noting they belong in the instrument method/config.
- Any second submitting surface (bitacora) must re-fork the same logic.

**AnaliticaDB** (`/home/sdl2/AnaliticaDB`, `100.64.254.6:8010`) already offers
the ingest path:

```
POST /uploads/experiments        # nested tree, options.upsert
  experiment{hid, title, operator, started_at, project}
    └ samples[]{hid, title, matrix, taken_at}
        └ measurements[]{hid, title, technique, instrument, measured_at,
                         operator, acquisition_params}
            └ files[]{file_type, storage_uri, is_raw, created_at}
```

Two properties decide the design:

1. **Everything is addressed by `hid`**, not resolved UUIDs, and `upsert` makes
   the call idempotent. Nothing on the instrument side needs to resolve a
   `sample_id`.
2. **`FileCreate` requires `measurement_id`** — a file cannot exist without a
   measurement; `project_id` on a file is derived from it. So the join key is
   the measurement, never a filename.

**Access control:** AnaliticaDB rejects any request without `X-Edge-Secret`
(plus `X-Auth-*` identity headers) — *"request did not pass a trusted front"*.
The edge secret lets a caller assert **any** identity.

## 3. Design

### 3.1 Three writers, one fact each

| Fact | Who alone knows it | Writes |
|---|---|---|
| Intent — project, experiment/sample hids, plan/step | the caller | additive `RunRequest` fields |
| Method + outcome — acquisition params, operator, exit status | **the sidecar** | the manifest |
| Where the data landed | **the data server** | `storage_uri` at ingest |

### 3.2 The manifest is a commit marker

On process exit the sidecar writes `manifest.json` into `output_dir` — **last,
after the result data**. Its presence therefore means *"this dataset is
complete."* Contents: the caller's record context, the acquisition params the
sidecar genuinely knows, the roster-validated operator, and the outcome.

This solves the partial-copy race without locking: an in-progress `.D`
directory simply has no manifest yet.

### 3.3 Robocopy carries it, in two passes

Robocopy gives **no ordering guarantee**, so a single pass can land the
manifest ahead of the data it vouches for:

```powershell
robocopy <src> <dst> /E /MINAGE:1 /XF manifest.json    # data first
if ($LASTEXITCODE -lt 8) {
    robocopy <src> <dst> /E manifest.json              # manifest second
}
```

**Robocopy exit codes are a bitmask** — 0 = nothing copied, 1 = copied,
2 = extras, 3 = both, **≥8 = failure**. Test `-lt 8`; testing `-eq 0` reports
failure on a perfectly good copy.

### 3.4 The sweep job runs on the data server

Not on the instrument PC — because posting to AnaliticaDB requires the edge
secret, and that PC is shared with technicians and OpenLab. Three consequences,
all in the right direction:

- the instrument PC never holds the edge secret and needs no egress to the
  record layer;
- the ingesting side is the side that can *see* the destination path, so
  `storage_uri` is observed rather than guessed;
- a record-layer outage cannot affect acquisition.

The job scans the landing zone for manifests, POSTs `/uploads/experiments` with
`upsert: true`, and renames the manifest to `.ingested`. Because upsert makes
re-POSTs harmless, a sweep that ran while AnaliticaDB was down simply succeeds
on the next pass — self-healing, no queue state to corrupt. A systemd timer
every minute or two is expected to be sufficient.

### 3.5 Why not the alternatives

| Rejected | Why |
|---|---|
| Path-matching reconciler | Would reverse-engineer project/owner/sample from a filename. The metadata should travel with the data, not be re-derived |
| Sidecar POSTs directly to AnaliticaDB | Puts the edge secret on the instrument PC; makes the record layer an availability dependency of acquisition; must guess the destination path |
| Sidecar uploads the file itself | Duplicates the backup; large-file egress competes with acquisition |
| Header-now / file-later split | Produces dangling rows whenever the second half fails |
| Analysis endpoints on the sidecar | Contract is device status/control; STATUS_SPEC best practice #9 (`/status` is current state only); that PC is already a bottleneck (~1.5 s poll latency) |

## 4. Open questions

1. **`hid` conventions** for experiment and sample — the only genuinely new
   vocabulary. Must be agreed between submitters and readers.
2. **`storage_uri` convention** — confirm against an existing row before
   writing new ones, so LaAgenteAnalitica and bitacora resolve paths the same
   way.
3. **Robocopy landing zone + trigger** — scheduled task cadence, destination
   layout.
4. **Sweep job home** — its own small service, or a module in an existing data
   server process.
5. **Additive `RunRequest` fields** — exact names for project / experiment hid
   / sample hid / plan / step. Backward compatible: absent them the manifest
   still writes, with an empty project, and those runs stay unattributed
   exactly as today.

## 5. Why this is worth doing first

It makes the *analysis-home* decision reversible. Once measurements are joined
records — intent + method + outcome + file, queryable by plan and step — every
analysis surface is just a reader. Building the analysis shell in `bitacora`,
keeping it in LaAgenteAnalitica, or both, becomes a product choice changeable
at the cost of one tool module.

If the join stays human, every analysis surface inherits the same manual
staging step, and the DMTA loop cannot close regardless of where the
peak-fitting code lives.

## See also

- [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) — the record layer these rows land in.
- [`AGENTIC_ELN_DESIGN.md`](AGENTIC_ELN_DESIGN.md) §6 — the Analyze surface that consumes them.
- [`STATUS_SPEC.md`](STATUS_SPEC.md) — the device contract the sidecar conforms to (best practices #9, #12).
- `agilent-hplcms-server` — `src/agilent_hplcms_server/control/{models,runner}.py`.
