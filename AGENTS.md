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

- **`docs/AGENT_RULES.md`** — canonical lab-wide operating rules: safety and
  hardware, records and data integrity, protocols and change control, chemicals,
  escalation. This is the "lab contract." Nothing may weaken a rule in it.
- **`docs/STATUS_SPEC.md`** — the authoritative device contract (v1.0 status
  envelope + v1.1 cooperative claims / `allowed_actions`). Every device I/O path
  conforms to it.

Load-bearing rules worth internalizing (the contract is authoritative, this is
just the short list agents most often need):

- Drive hardware only through the `lab-skills` SDK — never raw device
  `/control/*`. The SDK refusing a call is the safety system working.
- Never bypass or weaken an interlock at any layer. If one blocks you, stop and
  report — do not retry with adjusted parameters to get past it.
- Only human-approved, `main`-merged, validated plans execute against hardware.
- Records live in AnaliticaDB, immutable and truthful. **No run data in git** —
  measurements, tables, images go to AnaliticaDB, never into a repo.
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
  against a running `:8001`.
- **Prefer reading source in `.venv/` over searching online** when you need a
  usage example for a dependency.
- **Fail-fast style.** Don't add defensive code that swallows exceptions and
  hides failures. Report errors truthfully — this mirrors the contract's
  "report truthfully" rule.
- **Deployment** is Linux + systemd on the single Tailnet host; see
  `deploy/README.md`. Don't assume the dev machine (this MacBook) is the
  deploy target.

## 4. Recurring pitfalls (project-specific)

- **Single-PC concentration:** xArm (8000), PlateLoc (8010), OT-2 (8020),
  Cytation 5 (9333) all live on `sdl2-pc-03-cytation`. One reboot takes out four
  workflow-critical services.
- **Port 8010 is used by two different hosts** (UPLC-MS on `sdl2-pc-06-uplc`,
  PlateLoc on `sdl2-pc-03-cytation`). No collision, but easy to confuse.
- **`legacy_http` devices** (fume hood, filter-every-well) are translated
  per-device in the aggregator; treat their shapes as non-standard.
- **No app-level auth between aggregator and equipment** — Tailscale ACLs are
  the only gate. Don't design as if there were device auth.

## 5. Memory & instruction policy (how agents keep notes)

This is a durable, repo-wide convention. It governs where knowledge goes so all
agents stay on the same page.

- **`AGENTS.md` (this file)** — shared, model-agnostic repo instructions:
  durable conventions, commands, architecture facts, safety pointers, recurring
  pitfalls. When you learn one of those, update this file.
- **`CLAUDE.md`** — Claude-Code-specific only: slash commands, the Claude memory
  directory, Claude Code behavior. Other agents keep their own equivalent files.
  Nothing another agent needs belongs here.
- **`docs/AGENT_RULES.md`** — the binding contract; only changes when a human
  explicitly asks. Never edit it to smooth over a working problem.
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
   its `AGENT_RULES.md` (links back to the canonical `docs/AGENT_RULES.md` here
   and adds project rules — see `organic-hte-template/AGENT_RULES.md`), and a
   thin `CLAUDE.md`.

A repo file may add specifics; it may never weaken the binding contract or a
rule inherited from this base.
