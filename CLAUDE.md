# CLAUDE.md — Claude Code notes

Start with **`AGENTS.md`** — it is the shared, model-agnostic instruction file
(binding contract pointers, repo layout, working conventions, memory policy).
Everything there applies to Claude too. This file adds only what is specific to
Claude Code.

@./AGENTS.md

## Binding contract

`docs/AGENT_RULES.md` (lab operating rules) and `docs/STATUS_SPEC.md` (device
contract) are binding and take precedence. Do not weaken, bypass, or rewrite
them unless the human explicitly asks. See `AGENTS.md` §1.

## Claude-specific

- **Repo memory dir:**
  `~/.claude/projects/-Users-macbook-m2-Projects-ac-organic-lab/memory/`.
  One fact per file with frontmatter; index each in that dir's `MEMORY.md`.
  Follow the type rules: `project`/`feedback`/`user`/`reference`. Cross-repo or
  MacBook-wide facts do **not** go here — propose them for the appropriate
  global memory instead (`AGENTS.md` §5).
- **Slash commands / skills** are listed at session start; invoke a skill only
  when it appears in the available list. Don't guess skill names.
- **Workspace context:** the parent `../CLAUDE.md` is the lab integration
  tracker (shared architecture/status). Treat it as reference, not as
  Claude-only workflow notes.
