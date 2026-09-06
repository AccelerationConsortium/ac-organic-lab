# AGENTS.md — shared agent instructions

**Read this before proposing or editing anything.** It is the shared
instruction file for every coding agent working in this repo — Hermes, Codex,
Claude Code, and any future agent. Keep it model-agnostic: anything specific to
one agent goes in that agent's own file (e.g. `CLAUDE.md`), never here.

This repo (`ac-organic-lab`) is the **canonical base** for the lab's agent
setup. Other repos in this workspace inherit this pattern (see
[Inheritance model](#inheritance-model) at the bottom) and add only their own
specifics on top.

## 1. The binding contract — do not weaken

Two documents are **binding** and take precedence over everything in this file.
Agents reference them; they do not restate, reinterpret, or work around them.
If a working convention here ever conflicts with them, the contract wins — stop
and flag the conflict.

- **`docs/AGENTIC_LAB_DESIGN.md` (Part I)** — canonical lab-wide operating
  rules: safety and hardware, records and data integrity, protocols and change
  control, chemicals, escalation. This is the "lab contract." Nothing may
  weaken a rule in it. (Part II of the same file records the deployed
  agent-ops layer; it is descriptive, not binding.)
- **`docs/STATUS_SPEC.md`** — the authoritative device contract (v1.0 status
  envelope + v1.1 cooperative claims / `allowed_actions` + v1.2 `activity`).
  Every device I/O path conforms to it.

Load-bearing rules worth internalizing (the contract is authoritative, this is
just the short list agents most often need):

- Drive hardware only through the `lab-skills` SDK — never raw device
  `/control/*`. The SDK refusing a call is the safety system working.
- Never bypass or weaken an interlock at any layer. If one blocks you, stop and
  report — do not retry with adjusted parameters to get past it.
- Only human-approved, `main`-merged, validated plans execute against hardware.
- Records live in BitacoraDB, immutable and truthful. **No run data in git** —
  measurements, tables, images go to BitacoraDB, never into a repo.
- Local machine paths, hostnames, and secrets stay in gitignored `*.local.json`
  / `.env` files, never in commits.
- When something is irreversible, ambiguous, or not covered: stop and ask a
  human. The absence of a rule is not permission.

## 2. Repo layout & sources of truth

Monorepo layering (data flows left to right; details in `README.md` /
`docs/ARCHITECTURE.md`):

```
web/ (Next.js :8000)  ->  api/ (FastAPI :8001)  ->  skills/ (lab-skills SDK)  ->  equipment APIs over Tailscale
                                                            ^
                                    workflow scripts -------+  (same SDK, no dashboard)
```

- **`skills/`** — `lab-skills` Python SDK. Registry, polling aggregator,
  per-device adapters, workflow-facing session API. The layer that owns claims
  and preconditions.
- **`api/`** — FastAPI dashboard server; thin presentation over `skills/`.
- **`web/`** — Next.js 14 (App Router) + TypeScript + TanStack Query.
- **`auth/`** — `ac_auth` email-code login + `roster.yaml`.
- **`equipment.yaml`** — the single source of truth for what the dashboard
  shows (hardware identity, adapter, URLs, tiles). Edit it when hardware
  changes; `uvicorn --reload` picks up YAML via `--reload-include "*.yaml"`.
- **`platforms.yaml`** — Overview layout config (sections, order, membership).
- **`mcp/servers.yaml`** — reviewed, client-neutral MCP registry: provenance,
  approved tool allowlists, and data/safety boundaries. Client installation
  remains machine-local; never commit MCP credentials or local paths.
- **`docs/`** — start at `docs/STATUS_SPEC.md` to bring up a device, or
  `docs/ARCHITECTURE.md` to understand the whole.

## 3. Working conventions

- **Environment: `uv`.** This is a uv virtual workspace (`skills/`, `api/`,
  `auth/` are members sharing one root `.venv/`). Use `uv sync` to set up,
  `uv run …` to execute.
- **Python tests:** `uv run pytest skills/tests api/tests` (or a single path).
  `asyncio_mode = "auto"`; add `-m 'not integration'` to skip hardware-touching
  tests.
- **Dashboard server (local):** `uv run uvicorn api.app.main:app`.
- **Web (`web/`):** `pnpm dev` / `pnpm test` (vitest) / `pnpm typecheck`
  (`tsc --noEmit`) / `pnpm lint`. Regenerate API types with `pnpm gen:api-types`
  against a running `:8001`. Component tests have **no jest-dom**: assert
  `(el as HTMLButtonElement).disabled`, not `toBeDisabled()`. The bubble tests
  mock `@/lib/api` wholesale, so a new browser-side API client that must work
  under them uses plain `fetch` (see `lib/assistant-sessions.ts`).
- **Prefer reading source in `.venv/` over searching online** when you need a
  usage example for a dependency.
- **Fail-fast style.** Don't add defensive code that swallows exceptions and
  hides failures. Report errors truthfully — this mirrors the contract's
  "report truthfully" rule.
- **Deployment** is Linux + systemd on the single Tailnet host; see
  `deploy/README.md`. Don't assume the dev machine (this MacBook) is the
  deploy target.

## 4. Recurring pitfalls (project-specific)

- **Single-PC concentration:** xArm (8000), PlateLoc (8010), both OT-2
  gateways (8020/8021), shaker (8030), Cytation 5 (8040), BioStack (8050),
  and hostops (8060) all live on `sdl2-pc-03-cytation`. One reboot takes out
  most workflow-critical services (see DEVICE_PC_SETUP §7 for the full table).
- **Port 8010 is used by two different hosts** (UPLC-MS on `sdl2-pc-06-uplc`,
  PlateLoc on `sdl2-pc-03-cytation`). No collision, but easy to confuse.
- **A WebSocket route under `/api/*` must be excluded from the Next
  middleware `matcher`**, not merely early-returned inside `middleware()`.
  Next resolves routes for an upgrade with the raw socket standing in for the
  response, so invoking middleware there throws (`Error handling upgrade
  request TypeError: … reading 'bind'`) and kills the handshake before the
  rewrite to FastAPI. Use a negative lookahead, e.g.
  `"/api/ssh/((?!ws$).*)"`. Authenticate the socket with a short-lived ticket
  minted over plain HTTP instead — the same reason `/xarm5/ws` and
  `/hermes/api/ws` are `forward_auth`-exempt at the edge.
- **Mostly no app-level auth between aggregator and equipment** — Tailscale
  ACLs are the main gate; don't design as if every device authenticated its
  callers. The exceptions are per-device: hard claim enforcement
  (`X-Claim-Token` → 423) on most control surfaces, and the xArm's
  login-gated `/control/claim` + per-device edge secret (see ROADMAP →
  *Control-surface exposure*).
- **On the OT-2 gateways, the run engine is the only truth for labware and
  pipette names.** Read them from `/status`'s `details.snapshot.labwares` /
  `.pipettes`. Two neighbouring fields look authoritative and are not:
  `details.session_recipe` is a convenience mirror, not a substitute — it is
  written *before* the setup runs, and historically was never rolled back, so a
  failed setup advertised names that were never loaded. The gateway now rolls
  the recipe back on a failed setup, resolves an unknown name by adopting the
  run's own ids, and routes a slot-12 fixed-trash recipe entry to the trash
  registrar instead of failing the load (opentrons-server PR #11, deployed
  2026-08-27) — still resolve from the snapshot; it stays authoritative, and a
  run left diverged before the fix persists until the next restart or fresh
  setup. Separately, `POST /control/deck/declare` is a **full-layout replace**,
  so saving a layout that omits a slot silently wipes it while
  `details.tip_racks` still reports the rack stocked. Both produced a
  `409 … is not loaded in this run` on 2026-08-19 (`ot2_hte`) and 2026-08-27
  (`ot2_complexation`). Any `/control/*`
  refusal then *latches* the gateway into `error` — recovery is the operator's
  CLEAR ERROR (`reconcile`) in the device panel, which is deliberately not
  agent-proposable.
- **OT-2 deck slots are the bare key `"1"`..`"12"` in every argument** —
  `tips.reset` / `tips.mark` `slot`, `move_labware` `new_location`, `setup`
  `labware[].location`, the keys of `deck.declare` `slots`. The same shelf has
  other names elsewhere (`ot2_hte/slot_2` in `locations.yaml`,
  `opentrons_2_low` / `opentrons_2_high` in the xArm graph); a string in the
  wrong vocabulary passes the catalog's schema and is refused by the gateway,
  which latches the robot into `error` (see the previous bullet).
  `lab_skills.deck_slots` canonicalises any spelling inside `validate_plan` /
  `execute_plan` and in the assistant's `lab-control` (UI_DESIGN §5 Step 1m),
  but a direct `EquipmentClient.command()` body carries no skill name and is
  sent as written — so write the key. **Check the deck first:** before
  any step that uses a slot or moves labware on or off the deck, read
  `details.snapshot.labwares` (and `details.tip_racks`) and confirm with the
  operator that the physical deck matches; the snapshot is the gateway's
  belief, the person at the bench is the authority.

## 5. Memory & instruction policy (how agents keep notes)

This is a durable, repo-wide convention. It governs where knowledge goes so all
agents stay on the same page.

- **`AGENTS.md` (this file)** — shared, model-agnostic repo instructions:
  durable conventions, commands, architecture facts, safety pointers, recurring
  pitfalls. When you learn one of those, update this file.
- **`CLAUDE.md`** — Claude-Code-specific only: slash commands, the Claude memory
  directory, Claude Code behavior. Other agents keep their own equivalent files.
  Nothing another agent needs belongs here.
- **`docs/AGENTIC_LAB_DESIGN.md` Part I** — the binding contract; only changes
  when a human explicitly asks. Never edit it to smooth over a working problem.
- **Cross-repo / MacBook-wide facts** (repo roles, stable machine setup, durable
  personal preferences) belong in the **agent's global memory** (Hermes memory,
  Codex `~/.codex/memories/`, Claude user memory) — *proposed for approval*,
  not silently written. Do not put cross-repo facts in this repo.
- **Never** put temporary debugging notes, stale TODOs, or one-off observations
  into any memory or instruction file.

## 6. Safety protocol for edits outside this repo

Before editing anything outside this repository — another repo, `~/.hermes`,
`~/.claude`, `~/.codex`, shell config — first show the human: the exact file
path, the reason, the proposed change, and whether it affects only this repo or
future global behavior. **Do not modify other repos or global settings until the
human approves.**

## Inheritance model

Every other repo in this workspace is based on this pattern, layered:

1. **This repo's `AGENTS.md` + `CLAUDE.md`** — the canonical base. Other repos
   start from these files and keep the structure.
2. **The repo's own root files** — its `AGENTS.md` (repo-specific conventions),
   its `AGENT_RULES.md` (links back to the canonical rules in
   `docs/AGENTIC_LAB_DESIGN.md` Part I here and adds project rules — see
   `organic-hte-template/AGENT_RULES.md`), and a
   thin `CLAUDE.md`.

A repo file may add specifics; it may never weaken the binding contract or a
rule inherited from this base.
