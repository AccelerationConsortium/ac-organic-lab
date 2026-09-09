# Analytical sample submission — integration review

Date: 2026-09-09. **Status: review only. Nothing is agreed, nothing is
implemented, and the three decisions in [Open decisions](#open-decisions) are
deliberately left open.** Do not treat any recommendation below as settled
direction; this document exists so the review that produced them survives.

Scope: what it would take to give a lab user a signed-in page where they submit
their own samples for analytical measurement and then analyse the results.
Governed by [`BITACORA_INTEGRATION_DIRECTION.md`](BITACORA_INTEGRATION_DIRECTION.md)
(record and connector boundaries) and [`ARCHITECTURE.md`](ARCHITECTURE.md)
(which layer owns what). Changes neither.

The subject spans three repositories: this one (dashboard presentation),
[`LaAgenteAnalitica`](https://github.com/aspuru-guzik-group/LaAgenteAnalitica)
(the analytical agent, its GraphChat backend + SPA), and
[`AnaliticaDB`](https://github.com/the-matter-lab/AnaliticaDB) (the
analytical record store). It is filed here because the question is a
*laboratory integration* one, per `AGENTS.md` §2 — product direction lives in
bitácora, record-layer implications in BitacoraDB.

## The finding that should lead

**Most of this is already built, deployed, and identity-bound.** A page built
from scratch in this repo would duplicate roughly 3,200 lines of working,
scope-enforced UI. The gap is narrower and different from what the request
implies.

| Capability | Where it lives | State |
|---|---|---|
| SSO for the analytical UI | `/agente/*` → `127.0.0.1:3000`, `GRAPHCHAT_AUTH_MODE=ac_auth` ([`deploy/Caddyfile.single-edge`](../deploy/Caddyfile.single-edge) L313) | **Live.** Verifies the same `ac_auth_session` cookie this dashboard issues; the shared `/auth/banner.js` bar rides on top. Probed 2026-09-09: `GET /api/auth/config` → `{"mode":"ac_auth","acceptAnyToken":false}` |
| Sample registration | `POST /api/plates/register` (`instruments/plateRoutes.ts`), UI `PlateSetupPanel.tsx` (1231 lines) | Live — one experiment per plate, one sample per well, into AnaliticaDB under the caller's ac_auth scope |
| Run submission | `POST /api/instruments/agilent/submit` (`instruments/agilentRoutes.ts`), UI `AgilentStatusPanel.tsx` (1123 lines) | Live — writes one `submitted` measurement header per injection, takes a real STATUS_SPEC claim on the UPLC-MS sidecar, records a submission row |
| Results browse + analyse | `GET /api/catalog/{experiments,samples,measurements,files}`, `POST /api/catalog/stage` (`catalog/catalogRoutes.ts`), UI `CatalogPanel.tsx` (849 lines) | Live — stages result bytes into a room workspace where the LC-MS / GC-MS / NMR agent tools consume them |
| Authorization | `catalog/acAuthScope.ts` → **this lab's** ac_auth `GET /authz/scope` at `100.64.254.6:8009` | Live. AnaliticaDB's trusted-front gate is **on** — verified 2026-09-09, a direct `GET /samples` returns `401 missing/invalid X-Edge-Secret: request did not pass a trusted front`. Project-scope enforcement (`ENFORCE_AUTHZ`) is asserted by the edge config and the client code; it was **not** tested here |

Line counts and paths are relative to `LaAgenteAnalitica/graphchat/packages/`
(`backend/src/`, `frontend/src/`) at the commit reviewed on 2026-09-09
(`429f16e`). They are a snapshot, not a contract.

## Gaps

**A. Registration is plate-shaped — this is the actual gap.**
`POST /api/plates/register` requires `title` / `project` / `plate` / `tray` plus
a `wells[]` array. The submit route deliberately *looks up* a sample and never
creates one: *"A missing sample is a hard error: it means upstream sample
registration is incomplete, so the run must not proceed."* A user holding three
vials therefore has no form. Their only paths today are to register a
plate-shaped experiment they do not have, or to ask the chat agent to create the
records through its `analytica_db` ontology tools. The record *shape* already
tolerates the standalone case — `frontend/src/catalogModel.test.ts` asserts "a
standalone sample has no well/condition".

**B. Discoverability.** The only entry point from this dashboard is an "Open"
pill on the `laagente_analitica` Services tile (`equipment.yaml`). There is no
Utils pill and no page.

**C. `user_readme.md` is stale and actively misleading.** Its *Submitting a run*
section still says the form takes Matrix / Location / Column / Wavelengths and
that the backend "creates linked AnaliticaDB experiment/sample/measurement
records". Neither is true: those fields are gone (column and DAD wavelengths are
injected server-side and "deliberately NOT fields"), and submission is
lookup-only. The in-app `HelpPanel.tsx` is correct — it says the sample "is
looked up, not created; if it isn't in the database the run is refused". A user
following the written guide hits a refusal the guide does not explain. That
repo's own `AGENTS.md` requires both surfaces to be updated together.

**D. The dashboard tile cannot tell whether the analytical chat is up.**
`laagente_analitica` is `adapter: mock`. GraphChat's `:3000/status` answers 200,
but it is the SPA catch-all (`backend/src/server.ts` L47–51 serve
`FRONTEND_PATH` then `index.html` for `*`), not a STATUS_SPEC envelope. Any real
`/status` must be registered *before* that catch-all or the SPA swallows it.

## Findings that constrain the design

### F1. `/agente/` has no deep-linking

`frontend/src/App.tsx` holds the active panel in local state — `RightTab` (L24)
with `useState<RightTab>("workspace")` (L48) — and reads only `?room=` from the
URL (L41). Framing `/agente/` therefore lands a visitor on the *workspace* tab;
the `agilent`, `catalog`, `plate` and `loop` tabs are additionally `disabled`
until signed in. Seeding `rightTab` from a `?tab=` parameter the way `?room=`
already works is a small upstream change and is what would make a framed
dashboard page land people where intended.

### F2. Sample `hid` is unique only *per experiment*, but submit looks it up by `hid` alone

AnaliticaDB's constraints (`migrations/versions/9d4e7b6c2a1f_*.py` L65–78):

- `uq_experiments_project_hid` — experiment `hid` unique **per project**
- `uq_samples_experiment_hid` — sample `hid` unique **per experiment**
- `uq_measurements_sample_hid` — measurement `hid` unique **per sample**

The submit route resolves a sample with `list("samples", { hid })` and takes
`samples[0]`, with no guard for more than one match. The `project` field the
request body requires **cannot** narrow it: `SampleListFilters`
(`src/analytica_db/schemas/samples.py` L32–42) is `extra="forbid"` and accepts
only `experiment_id` and `hid`, so the field is decorative for the lookup.

Why this is latent rather than live: the plate path derives hids as
`` `${plate}-${well}` `` (`frontend/src/plateGrid.ts` L77, applied via
`effectiveSampleHid` L259) and plate names are project-unique by constraint, so
plate-registered hids are distinctive by construction. The per-well override
field can defeat that.

**A walk-up vial form is what would break it systematically**, because people
type `vial-1`, `S1`, `A1`. Several samples would then share one hid within a
project and submit would pick one silently — the wrong vial's identity on a
measurement, with no error. The variable that matters is therefore the *naming
rule*, not the experiment's lifetime: auto-created short-lived experiments are
safe if hids are derived, and unsafe if they are typed.

Worth reporting upstream on its own merits, independent of every decision below:
a `>1 match` guard that refuses instead of guessing is a small change and is
strictly more honest than the current behaviour.

### F3. The integration direction already speaks to the record question

[`BITACORA_INTEGRATION_DIRECTION.md`](BITACORA_INTEGRATION_DIRECTION.md):

> Shared record clients are candidates for packaging, **not permission for
> clients to open one another's databases.**

> Equipment control stays here; Bitácora's UI and scientific agent need no
> direct device API or sibling SDK checkout.

A dashboard-hosted form writing AnaliticaDB directly is the shape that warns
against. A framed LaAgente page keeps the write inside the product that owns the
store, and the question does not arise. **D1 and D3 are therefore coupled** —
the entry-point choice largely determines the record-boundary exposure.

That document's information table does route *measurements* to BitacoraDB, but
in the context of assistant-captured scientific information. UPLC measurements
live in AnaliticaDB because that is where the analysis pipeline reads them. Two
record stores with near-identical entity sets (`project`, `experiment`,
`sample`, `measurement`, `file`, `plan`, `note`, `analysis`) is a real
architectural question; that same document leaves record-layer consolidation to
the product and record-layer designs, so it is **not** this page's to settle.

## Open decisions

**None of these is decided.** Each changes the work materially, and the
recommendations are the reviewer's reading, recorded for argument rather than
adoption.

### D1 — where the submission surface lives

| Option | For | Against |
|---|---|---|
| Frame `/agente/` from a dashboard Utils page, after adding `?tab=` upstream (F1) | ~80 lines here; no duplication; write stays in the owning product, so F3 does not bite | Users get the full chat IDE, not a focused walk-up form; almost all remaining work lands in another org's repo |
| Build a focused native form in this dashboard | Tighter walk-up UX; the work stays in a repo this team controls | Duplicates ~3,200 working lines; makes the dashboard a writer into another product's database — the shape F3 warns against |
| Frame now, revisit a native form later | Buys discoverability immediately at almost no cost | Defers rather than answers the duplication question |

Reviewer's reading: frame it, fix `?tab=` first. Note the consequence — this
puts most of the work in `LaAgenteAnalitica`, not here.

### D2 — how a walk-up sample is named and attached to an experiment

Every sample needs an `experiment_id` (AnaliticaDB requires the parent FK), so
a standalone submission must attach to *some* experiment. F2 is what makes this
more than a cosmetic choice.

| Option | For | Against |
|---|---|---|
| Auto-create a walk-up experiment per (user, project); **derive** sample hids from it; add the `>1 match` guard | Mirrors the proven `${plate}-${well}` convention; removes the ambiguity instead of inheriting it | Users do not choose their own sample names; touches the deployed submit route for the guard |
| User picks an existing experiment and types their own `hid` | Simplest to build; full naming control for the scientist | Leaves the hid-alone lookup ambiguous — the same `vial-1` in two experiments still resolves arbitrarily |
| Make submit experiment-scoped first, then add any walk-up path | Most correct end state | Touches the live submission path that the campaign loop depends on, before delivering anything |

### D3 — which record store a submitted sample lands in

| Option | For | Against |
|---|---|---|
| AnaliticaDB only, written by LaAgente | The analysis pipeline reads it, so the sample must exist there; respects F3 | Leaves analytical samples outside the lab's stated record layer |
| AnaliticaDB plus a file into BitacoraDB | Matches the direction document's routing for measurements | Doubles the write path and needs a divergence/reconciliation policy |
| Settle the AnaliticaDB ↔ BitacoraDB boundary first | Addresses the actual architectural problem | Blocks a small page on a much larger cross-product decision |

## Candidate phasing (conditional on the decisions above)

Written for D1 = frame. It does **not** apply unchanged to the other options.

1. **Dashboard entry point.** A `Samples` pill in `web/src/app/utils/layout.tsx`
   plus a page following the `/utils/xarm_control` pattern: gate the iframe on
   `useUserAuth()` before framing. The reason to gate differs from `/xarm5` —
   `/agente` is *not* edge-gated (it verifies the cookie itself), so an
   unauthenticated frame renders GraphChat's own sign-in rather than a 401 body.
   The gate buys a consistent dashboard-side message, not access control.
2. **`?tab=` support upstream** (F1), so the page can land on the intended panel.
3. **Standalone registration in LaAgente**, per D2 — a route beside
   `plateRoutes.ts` reusing `requireInstrumentIdentity`, `resolveDataScope` and
   `AnaliticaDbClient` unchanged, plus the form. That repo's `AGENTS.md`
   governs: red/green TDD with the red committed first, `[claude]`/`[codex]`
   commit prefixes, `graphchat/` commits isolated from other areas, and **both**
   `HelpPanel.tsx` and `user_readme.md` updated.
4. **The `>1 match` guard** (F2) — worth doing whichever way D2 goes.
5. **A real STATUS_SPEC `/status` for GraphChat** (gap D), registered before the
   SPA catch-all, then flip `laagente_analitica` to `adapter: http` /
   `protocol: "1.2"` under the §9 read-only clause (`allowed_actions: []`, no
   `/control/*`, nothing to claim). Only then can the aggregator alert on it.
6. **Docs** — fix gap C, and record whichever decisions get made here.

## Constraints any implementation must not paper over

- **Nothing in software puts a vial in the autosampler.** `sample_position`
  (e.g. `D1B-A1`) is typed by whoever physically loads the tray. Such a page
  registers metadata and queues an injection at a *stated* position; it must say
  so, the way the Bambu panel states that dispatch is not implemented.
- **Roster membership is likely a prerequisite.** AnaliticaDB's read rule is
  admin-or-PI-or-active-member, so a user with no project in ac_auth's roster
  should expect refusals from an otherwise perfect form. This follows from the
  edge config and client code, not from a live test — confirm it against a real
  scopeless account before promising anyone a working flow.
- **A claim conflict is not a failed submission.** The existing route does not
  roll AnaliticaDB records back when the instrument is held by someone else —
  the sample and measurement exist as an honest record of the attempt.

## See also

- [`BITACORA_INTEGRATION_DIRECTION.md`](BITACORA_INTEGRATION_DIRECTION.md) — the
  governing record and connector boundaries; F3 quotes it.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — layer ownership; AnaliticaDB is
  registered as a Services tile and is explicitly *not* the lab's record layer.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — ac_auth, the single edge, and why a
  session cookie cannot be shared per-host.
- [`STATUS_SPEC.md`](STATUS_SPEC.md) §9 read-only clause — what gap D's
  `/status` would have to satisfy.
- `equipment.yaml` — `laagente_analitica` (mock) and `analytica_db` entries.
