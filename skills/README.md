# lab-skills

The Python SDK and aggregator for the AC Organic Self-driving Lab. This is the package that workflow code, the dashboard server (`api/`), and (later) agents import to drive the lab.

This package is a workspace member of the [`ac-organic-lab`](../README.md) monorepo. The repo-root [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) describes how it fits into the platform.

## Status

**v0.1 in progress.** The package is currently a skeleton; `EquipmentAggregator`, `Registry`, the SDK session layer, and the adapter modules move in here from `../api/app/` during PR-2 of the v0.1 restructure. See `.cursor/plans/build_lab-skills_*.plan.md` for the working plan.

## Local development

From the repo root:

```bash
uv sync
uv run pytest skills/tests -q
```
