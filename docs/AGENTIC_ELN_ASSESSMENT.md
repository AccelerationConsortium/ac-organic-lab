# Agentic ELN — literature-grounded build assessment

> **Status:** assessment note (2026-07-15). Not part of the lab contract.
> Written from a read of the lab's internal design docs plus a targeted
> external literature dive. Lives here because `ac-organic-lab/docs/` is the
> central place lab-stack context is kept (alongside
> [`ANALITICADB_ELN_LIMS_DESIGN.md`](ANALITICADB_ELN_LIMS_DESIGN.md) and the
> root [`LAAGENTEANALITICA_ASSESSMENT.md`](../LAAGENTEANALITICA_ASSESSMENT.md)).
> Internal maturity claims trace to the cited repo docs and may lag the working
> tree — verify against current code before committing engineering time.

## Verdict

**You are ~70% of the way to an agentic ELN and it is mostly a matter of
assembly, not invention.** The capability is spread across four repos that were
never assembled into one product:

- The durable record layer (**AnaliticaDB**) already has a designed ELN+LIMS
  generalization: append-only notes, versioned plans, a materials ledger, and
  an agent-facing ontology contract.
- The analytical agent (**LaAgenteAnalitica**) is a production-grade chat +
  workspace with real analysis workflows and a human-gated commit step.
- The real-time platform (**ac-organic-lab**) owns safety, claims, and
  observability.
- The coordination layer (**PyPoe**) owns the human front door and the
  approval chokepoint.

What is missing is not technology — it is **assembly**: closing the loop from a
conversationally- or protocol-authored *plan* through validated execution to an
immutable, provenance-linked record, behind one human-approval gate.
Recommendation: **build it incrementally, as the seam between existing
components** — and first resolve the one open question of *which surface
authors the design Plan*.

## 1. What "agentic ELN" has to mean here

An ELN records *intent, execution, observation, and conclusion*. A LIMS tracks
*materials, containers, and locations*. "Agentic" means an LLM does real
authoring and interpretation across that record — not just autocomplete — while
a human stays the signing authority.

The governing distinction, already nailed in the ELN/LIMS design doc [1]: the
ELN is an **append-only narrative timeline** (never edit history), the LIMS is a
**ledger plus computed current state** (a bottle of THF belongs to no project).
What unifies them is **provenance**, expressed in W3C PROV terms baked into the
repo: a *Plan* (intent) → *Activities* (execution, analysis, transactions) →
*Entities* (samples, lots, files, results) → *Agents* (human and AI principals).

The target loop is the full DMTA cycle made durable: **Plan → Notes (execution
actuals) → Measurements (instrument data) → Analyses (interpretations) → Report
→ next Plan.** An agentic ELN is the software that lets an agent draft and read
every stage, and lets a human approve the two stages that touch reality — the
plan that will run, and the result that becomes the record.

## 2. What you already have

| Layer | Repo | Maturity | Notes |
|---|---|---|---|
| Durable record (ELN+LIMS) | AnaliticaDB | Core built; ELN/LIMS generalization designed | Live 4-entity core (`Experiment › Sample › Measurement › MeasurementFile`) + `AgentAction` audit, `extra="forbid"`, OTel-baggage identity, versioned `ontology.json`. ELN Phase 1 (`Plan`/`Note`/`Analysis`) shipped 2026-07-03; LIMS ledger (`Substance/Lot/Container/Location`) designed, unbuilt. [1] |
| Analytical agent + notebook UI | LaAgenteAnalitica | ~85–95% built | pydantic-ai/pydantic-graph ("Grafico"); production LC-MS/NMR/GC-MS; 16 AnaliticaDB CRUD tools from the same ontology; React 19 + Yjs collaborative chat + filesystem workspace + deferred-tool approval UI. Already has the scratch-vs-record split and a human-gated `commit_analysis_result` chokepoint. [2][3] |
| Real-time platform + safety | ac-organic-lab | Live fleet | STATUS_SPEC, `lab-skills` SDK (claims, `validate_plan`/`execute_plan`, four-layer interlocks), dashboard, read-only lab-history MCP. Control-capable `lab-skills mcp serve` shipped v0.4. |
| Coordination + approval | PyPoe | Read path shipped; gate planned | Human front door + approval chokepoint by charter. Read-only lab MCP (13 tools, one append-only write, no `control_action` by hard invariant) and Kuma → `claude -p` shipped. Approval card, context pack, AnaliticaDB record client designed, unbuilt. [4] |

**The load-bearing fact:** LaAgenteAnalitica and AnaliticaDB grew up *outside*
the lab's SDK / claim / observability fabric — the agent reaches the Agilent
instrument through its own REST API, not through the dashboard's audited claim
path, and there is no shared claim between the two. An agentic ELN is largely
the project of **joining these two worlds** at three seams the architecture
already names: plan executions referencing validated plans, notes/measurements
carrying `equipment.yaml` ids, and trace/session ids linking records to chat
rooms. [1][3]

## 3. Where the field actually is

Three distinct conversations; conflating them is the usual category error.

### A. Agents that operate labs

Landmark autonomy demos are real but narrow. **Coscientist** (Boiko et al.,
*Nature* 2023) drove a GPT-4 agent with search/code/robotic APIs to optimize a
Pd cross-coupling [5]. **ChemCrow** (Bran et al., *Nat. Mach. Intell.* 2024)
wrapped 18 expert tools to plan syntheses and guide a chromophore discovery [6].
Berkeley's **A-Lab** (*Nature* 2023) ran 17 days of closed-loop inorganic
synthesis — and its later **correction** (43→36 compounds; characterization
disputed) is the cautionary tale: **autonomy without a trustworthy, auditable
record layer is fragile** [7]. Recent syntheses (*Chem. Rev.* 2024 SDL review;
"Self-Driving Laboratory 2.0" / "Platform to Knowledge Graph") converge on the
same gap: SDL 1.0 optimized the *loop*; SDL 2.0 needs machine-readable
ontologies, version-controlled provenance-tracked recipes, and human-in-the-loop
as first-class infrastructure [8][9].

### B. The ELN/LIMS market went "agentic" in 2025

Every major platform now ships an LLM copilot — so "agentic ELN" is about to be
a crowded term. **Benchling** launched an AI layer (Oct 2025); **Sapio**
announced a "3rd-generation" ELN with "agentic intelligence" (Sep 2025);
**Dotmatics** was acquired by Siemens for $5.1B (Jul 2025) [10]. Open-source /
academic: **Chemotion** (KIT) and **eLabFTW** are the FAIR-focused ELNs of
record; **AI4Green** targets green chemistry; and — closest to this project —
**Airalogy** (Westlake, 2025) is explicitly "beyond traditional ELNs":
customizable standardized data records representing whole workflows + an AI
copilot for automated entry, analysis, and automation [11][12]. The
differentiator the vendors mostly lack — and this lab has — is a **physical
execution fabric with real interlocks and claims** under the notebook.

### C. Standards, provenance, and the compliance floor

The repo's provenance patterns are the mainstream ones. **AiiDA** is the
reference for a queryable immutable provenance DAG over automated workflows
[13]. **ESCALATE** is the chemistry-specific template-vs-object /
nominal-vs-actual model the AnaliticaDB design cites directly [14]. For
interchange, **SiLA 2** (device communication) and **AnIML / Allotrope AFO**
(analytical data + ontology) are the adopted standards, now being aligned
semantically [15]. The hard floor for an auditable record is **21 CFR Part 11 /
ALCOA+**: independent audit trail, bound e-signatures, unique-user attribution,
immutable originals — precisely the append-only + `AgentAction` + author-kind
posture AnaliticaDB already adopts [16]. On the agent side, **MCP** is the
de-facto tool protocol and **LangGraph**-style checkpoint/interrupt is the
mainstream human-in-the-loop pattern — both of which the lab already uses (MCP
servers) or has an equivalent of (the interlock/claim gate) [17].

## 4. How the existing design maps to best practice

| Requirement (from the field) | Established pattern | Already in the stack? |
|---|---|---|
| Immutable, attributable record | 21 CFR 11 / ALCOA+; PROV [16][1] | Append-only `Note`/`Measurement`, versioned `Plan`/`Analysis`, per-mutation `AgentAction`, author-kind from principal — **yes** |
| Queryable provenance graph | AiiDA DAG [13] | Plan→Note→Measurement→Analysis + container-lineage recursive query — **yes** |
| Template vs run instance | ESCALATE nominal/actual [14] | `protocol` (git, PR-reviewed) vs `Plan` (rendered run + `source_commit`) — **yes** |
| Agent as untrusted tool-caller | MCP + typed tools [17] | Ontology-driven tools, `extra="forbid"`, `ModelRetry` on 4xx, compact summaries — **yes** |
| Human-in-the-loop on side effects | LangGraph interrupt / approval [17] | Human-gated `commit_analysis_result`; lockable `AnalysisPlan`; PyPoe gate — **partial** |
| Physical-action safety | SiLA locks; interlocks [15] | Four-layer interlocks + cooperative claims + device authority — **yes** |
| Standardized workflow records | Airalogy; AnIML/AFO [12][15] | Ontology contract + typed steps; **no AnIML/AFO export** — **gap** |
| Unified materials/inventory ledger | LIMS ledger model [1] | Designed (`Substance/Lot/Container`) but **not built** — **planned** |
| One agent across design→execute→record | Coscientist / SDL 2.0 [5][9] | Two agent worlds not yet joined; execution not agent-driven end-to-end — **gap** |

## 5. The one decision to make first

Everything else is sequencing. This is the fork the internal docs flag as the
biggest open question (PyPoe decision D4): **which surface authors the design
`Plan`?** [4]

Two legitimate sign-off authorities already exist and must not be forked:

- **Protocol-authored plans** — the procedure lives in a git repo
  (`organic-hte-template`), and *the merge to `main` under CODEOWNERS review is
  the human sign-off*. The orchestrator renders it into a `Plan` with a
  `source_commit` and executes only from `main`. [18]
- **Conversational / ad-hoc plans** — the plan is negotiated in chat, and *a
  Slack/web approval card is the sign-off*. This is PyPoe's planned gate:
  propose → human confirms a stored structured payload → execute → audit row.
  [4]

**Recommendation:** support *both*, keep their sign-off authorities *distinct*,
and make GraphChat the *design surface* while AnaliticaDB stays the *system of
record* and PyPoe owns the *approval gate + identity bridge*. The agent drafts a
`Plan` (status `draft`); entering `approved` requires a human principal — routed
through PyPoe's gate for conversational plans, or stamped from the git merge for
protocol plans; `execute_plan` runs it under claims and interlocks; notes,
measurements, and analyses append back under the same `plan_id`. Do *not* let
PyPoe re-declare the ontology — it should front AnaliticaDB's own agent surface,
not re-wrap it, to avoid schema drift. [4]

## 6. Honest gaps & risks

- **Two agent worlds, no shared claim.** If both LaAgenteAnalitica and the
  dashboard can drive the Agilent instrument, that coupling is unmodeled.
  Joining them behind one hard-enforced claim is prerequisite work. [3]
- **The ledger is only true if it is the only path.** Bench-top aliquoting must
  actually get recorded or balances become fiction — an agent-UX problem
  (barcode scan + one utterance) more than a schema one. [1]
- **No automated evals for the agent.** Flagged as the honest gap; an agent that
  authors records needs regression evals before it authors *trusted* records.
  [2]
- **Execution not yet agent-driven end-to-end.** `execute_plan` validated live
  against PlateLoc, but a fully green agentic run is still blocked on facilities
  and OT-2 typed skill args.
- **Compliance is designed, not certified.** The posture is Part-11-shaped, but
  e-signature binding, validation-through-upgrades, and AnIML/AFO export are not
  built. Fine for a research lab; scope explicitly before anyone says GxP. [16]
- **Term dilution.** Every ELN vendor now says "agentic". The defensible
  differentiator is the *execution fabric under the notebook* — lead with that.

## 7. Recommended path

Ordered so each phase is independently useful and none blocks on facilities.

1. **Phase 0 — settle the authoring surface & write the seam spec.** Resolve D4
   (§5). One short doc fixing the three seams (plan↔validated-execution,
   record↔`equipment.yaml` ids, record↔trace/session id) + the identity bridge
   (Slack/lab user → AnaliticaDB principal). No code.
2. **Phase 1 — ship PyPoe's approval gate + the `Plan` lifecycle.** Reusable
   confirm-card (allowlist, expiry, audit row) + the AnaliticaDB record client.
   Yields an *auditable* agentic ELN for conversational plans. Highest leverage.
3. **Phase 2 — join the two agent worlds under one claim.** Front the instrument
   path behind the audited claim (or teach LaAgenteAnalitica the `lab-skills`
   MCP surface); wire `execute_plan` so an approved `Plan` runs under interlocks
   and appends its notes/measurements under the same `plan_id`. Closes the loop.
4. **Phase 3 — build the LIMS core & make the ledger the only path.** AnaliticaDB
   Phase 2 (`Substance/Lot/Container/Location` + `ContainerAction`,
   `lab-inventory` shared project). Make `register_lot`/`consume`/`transfer`
   part of the agent-guided flow with barcode capture.
5. **Phase 4 — trust & interoperability hardening.** Agent evals; report
   generation; and *only if* an external audit/data-exchange need appears,
   AnIML/AFO export and e-signature binding to reach the Part-11/FAIR bar. Defer
   until a concrete driver exists.

## Sources

Internal docs [1–4, 18] are repo-local and authoritative for the stack's current
state. External sources [5–17] are the literature dive.

1. AnaliticaDB → ELN + LIMS generalization — design analysis.
   [`docs/ANALITICADB_ELN_LIMS_DESIGN.md`](ANALITICADB_ELN_LIMS_DESIGN.md) (internal).
2. LaAgenteAnalitica — Implementation Assessment.
   [`LAAGENTEANALITICA_ASSESSMENT.md`](../LAAGENTEANALITICA_ASSESSMENT.md) (internal).
3. Analytical-Chemistry Agent: Architecture Considerations —
   `LaAgenteAnalitica/architecture-considerations.md` (internal): two-tier
   tools+graph, scratch-vs-record store, human-gated commit, lockable
   plan-as-data.
4. PyPoe master plan & agent-integration design — `pypoe/CLAUDE.local.md`
   §§4–5, App. A/B (internal): approval gate, context pack, record client,
   decision D4.
5. Boiko, MacKnight, Kline, Gomes — "Autonomous chemical research with large
   language models." *Nature* 624, 570–578 (2023).
   https://www.nature.com/articles/s41586-023-06792-0
6. M. Bran et al. — "ChemCrow: Augmenting large-language models with chemistry
   tools." *Nat. Mach. Intell.* (2024); arXiv:2304.05376.
   https://www.nature.com/articles/s42256-024-00832-8 · https://arxiv.org/abs/2304.05376
7. Szymanski et al. — "An autonomous laboratory for the accelerated synthesis of
   inorganic materials" (A-Lab). *Nature* (2023) + Author Correction.
   https://www.nature.com/articles/s41586-023-06734-w · correction context:
   https://cen.acs.org/research-integrity/Nature-robot-chemist-paper-corrected/104/web/2026/01
8. "Self-Driving Laboratories for Chemistry and Materials Science." *Chem. Rev.*
   (2024). https://pubs.acs.org/doi/10.1021/acs.chemrev.4c00055
9. "Toward self-driving laboratory 2.0" (*Mater. Horiz.* 2026,
   DOI:10.1039/D5MH01984B) + "From Platform to Knowledge Graph" (*JACS Au* 2021,
   10.1021/jacsau.1c00438). https://pubs.rsc.org/en/content/articlehtml/2026/mh/d5mh01984b ·
   https://pubs.acs.org/doi/10.1021/jacsau.1c00438
10. ELN market "agentic" turn, 2025 — Benchling AI (Oct 2025), Sapio 3rd-gen ELN
    (Sep 2025), Siemens–Dotmatics acquisition (Jul 2025). Overview:
    https://intuitionlabs.ai/articles/llm-copilots-bench-scientists
11. Chemotion ELN (*J. Cheminform.* 2017)
    https://link.springer.com/article/10.1186/s13321-017-0240-0 ; AI4Green
    (*J. Chem. Inf. Model.* 2023) https://pubs.acs.org/doi/10.1021/acs.jcim.3c00306
12. Airalogy — "AI-empowered universal data digitization for research
    automation." arXiv:2506.18586 (2025); deployed at Westlake University.
    https://arxiv.org/abs/2506.18586
13. Huber et al. — "AiiDA 1.0 … automated reproducible workflows and data
    provenance." *Sci. Data* (2020).
    https://www.nature.com/articles/s41597-020-00638-4
14. ESCALATE — Experiment Specification, Capture and Laboratory Automation
    Technology (template-vs-object / nominal-vs-actual). Context:
    https://pubs.acs.org/doi/10.1021/jacsau.1c00438
15. SiLA 2 (device communication); AnIML + Allotrope AFO (analytical data +
    ontology). https://sila-standard.com/about-us/ ; AnIML Ontology:
    https://link.springer.com/chapter/10.1007/978-3-032-28110-4_8
16. 21 CFR Part 11 / ALCOA+ — electronic records, audit trails, e-signatures.
    https://intuitionlabs.ai/articles/21-cfr-part-11-compliance-guide-pharma ·
    https://totallab.com/resources/alcoa-principles/
17. Model Context Protocol (Anthropic, 2024); LangGraph human-in-the-loop
    (interrupts/checkpoints, GA Oct 2025). Framework comparison:
    https://alicelabs.ai/en/insights/best-ai-agent-frameworks-2026
18. `organic-hte-template` — the git side of the record layer: Protocol-vs-Plan,
    permanent `step_id`s, CODEOWNERS merge = sign-off, "no run data in git"
    (internal template repo).

## See also

- [`ANALITICADB_ELN_LIMS_DESIGN.md`](ANALITICADB_ELN_LIMS_DESIGN.md) — the record-layer design this assessment builds on.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — platform layering; the real-time vs record-layer split.
- [`INTERLOCKS.md`](INTERLOCKS.md) — four-layer safety model; `validate_plan`/`execute_plan`.
- [`../LAAGENTEANALITICA_ASSESSMENT.md`](../LAAGENTEANALITICA_ASSESSMENT.md) — the agent's current maturity.
