# Making the XPR balances drivable — design note

**Status: recommendation only. Nothing here is implemented.** This note records
the assessment asked for alongside the `power_strip` catalog module
(`skills/src/lab_skills/skill_catalog/power_strip.py`, 2026-09-20) and the
reasons for the recommended shape. Implementing it is a separate, reviewed
change.

## The gap

`gibbie_balance` and `lle_xpr_balance` are the two Mettler Toledo XPR balances,
each fronted by its own `mt-xpr-balance` service (`:8081` on `sdl2-pc-04` and
on `sdl2-pc-00-lle`). Both are `kind: other` in `equipment.yaml`, and
`skills_for("other")` returns `[]`, so `await lab.skills()` reports zero
capabilities for them. Every one of their control verbs is therefore reachable
only by a raw HTTP call, which the lab contract forbids — and the device's own
`/agent-docs` says so in as many words: *"If the SDK lacks a balance action,
report the missing integration instead of bypassing it."* This note is that
report.

## What the devices actually expose (verified live 2026-09-20)

Both services report `equipment_version: 0.1.2` and byte-identical
OpenAPI control surfaces — 13 `POST /control/*` verbs plus the read-only
`GET /control/access`:

| Verb | Class |
|---|---|
| `claim`, `heartbeat`, `release` | claim protocol — owned by `ClaimManager`, never a skill |
| `access` | read-only authorization probe (GET), not a skill |
| `startup`, `shutdown` | lifecycle |
| `weigh`, `tare`, `zero` | measurement |
| `door/open`, `door/close` | draft-shield motion |
| `dose/start` | automated dosing (202 + `details.job_id`; needs the dosing head) |
| `cancel` | abort an in-flight request |
| `clear_error` | dismiss `last_error`; operator recovery |

`GET /status` on both balances returned exactly:

```
"allowed_actions": ["shutdown", "weigh", "tare", "zero", "door.open", "door.close"]
```

Two things to carry into any implementation:

1. **The advertised names are dotted (`door.open`), the routes are slashed
   (`/control/door/open`).** Availability is a `def.name in allowed_actions`
   membership test, so the `SkillDef.name` must be `door.open` and the
   `endpoint` `/control/door/open` — the same split `solid_doser` already has.
   Do not derive either from the other.
2. **The advertised set is state-dependent.** `startup` appears in the
   `requires_init` branch, `clear_error` only while `last_error` is set and
   no request is running, and `dose/start` only with a dosing head attached
   (both balances currently report `dosing_head: {connected: false, state:
   "absent"}`). A parity test must encode the *union* across states, the way
   `test_solid_doser_names_match_device_allowed_actions` does, not one
   snapshot.

## Recommendation

**Use per-device `skills_for()` overrides now — one `balance.py` module
exporting a shared `XPR_BALANCE_SKILLS` list, returned for
`("other", "gibbie_balance")` and `("other", "lle_xpr_balance")` — and do not
add a `balance` `EquipmentKind`.**

Reasons, in order of weight:

1. **`EquipmentKind` is not ours to extend unilaterally.** Per
   `docs/SKILLS_CATALOG.md`, "the set of `EquipmentKind` enum values is
   governed by `STATUS_SPEC.md`, not the catalog", and STATUS_SPEC is one of
   the two binding documents. The enum physically lives in the shared
   `sdl-lab-contract` package, pinned in `skills/pyproject.toml` at git tag
   `v1.2.0`. A new kind is a contract amendment, a contract release and retag,
   and a bump in every repo that consumes it — a human-approved spec change,
   not a catalog edit.
2. **A new kind is only half a change without touching the devices.** The
   `EquipmentKind` in the envelope is emitted *by the device*. A `balance` kind
   that the two services keep reporting as `other` buys nothing: both repos
   would have to change `equipment_kind` and be redeployed, and until that
   lands `equipment.yaml` and the live envelope disagree. The override path
   needs no device change at all and no service restart.
3. **The precedent already exists and is load-bearing.** `skills_for()` has
   exactly this shape for `("other", "lumastir")` and for
   `("robot_arm", "ligand_ur5e")`, with a comment saying why: adding one
   device's capabilities must not hand them to every device of that kind. That
   is the same requirement here — `other` holds 29 entries, most of them web
   services and host probes, and none of them may inherit `tare`.
4. **The two balances are genuinely one device type, not two.** Same service,
   same version, identical OpenAPI. One shared `XPR_BALANCE_SKILLS` list
   referenced from two override branches carries no duplication cost, so the
   usual argument for a kind ("stop copy-pasting per device") does not apply
   at n=2.
5. **It is the reversible direction.** An override is deleted in one commit if
   a `balance` kind later lands in STATUS_SPEC; the SkillDefs themselves are
   unchanged by the move, since only the `kind` field and the dispatch branch
   differ. Shipping the kind first and discovering the verbs are wrong costs a
   contract revision.

The counter-argument worth stating: `skills_for()` grows a third special case,
and it is a linear chain of `if`s. If a fourth or fifth per-device override
appears, refactor the dispatch to a `dict[(kind, equipment_id), list[SkillDef]]`
lookup — that is cheap and is not a reason to reach for a contract change now.
Revisit the `balance` kind when a *third*, non-XPR balance is onboarded, or
when the dashboard needs a `BalanceTile` keyed on kind rather than on id.

## Shape of the implementation, when it is approved

- New `skills/src/lab_skills/skill_catalog/balance.py`, **not** eagerly
  imported in `skill_catalog/__init__.py` (follow `lumastir.py` / `ur_arm.py`:
  lazy import inside the `skills_for()` branch, so nothing lands in
  `SKILL_REGISTRY["other"]`).
- Export `XPR_BALANCE_SKILLS` with `kind="other"` on every entry, names taken
  byte-for-byte from `allowed_actions`: `startup`, `shutdown`, `weigh`,
  `tare`, `zero`, `door.open`, `door.close`, and — decided per bullet below —
  `dose.start`, `cancel`.
- Two `skills_for()` branches, both returning `list(XPR_BALANCE_SKILLS)`.
- Args schemas from the live `/openapi.json`, not from `/agent-docs`:
  `weigh`/`tare`/`zero` take `{immediately: bool = false}` and require an
  empty object even at defaults; `door.open` takes `door: "left"|"right"` and
  `position` 1-100 (0 is a 422); `door.close` takes `door` only;
  `dose.start` takes `substance_name` (1-80 chars) and `dose_amount_mg`
  (>0, ≤5000) plus optional tolerances/method/timeout.
- **Leave `clear_error` out of the catalog.** It is operator recovery, and the
  repo's standing rule (the OT-2 `reconcile` precedent, `AGENTS.md` §4) is that
  clearing a latched error is deliberately not agent-proposable.
- `claim` / `heartbeat` / `release` / `access` are not SkillDefs. Note in the
  module docstring that this service requires a trusted dashboard identity
  (`X-Auth-User` + `X-Edge-Auth`, `XPR_EDGE_SHARED_SECRET`) on **every**
  `/control/*` call including the claim dance, and that only
  `XPR_CONTROL_USER` may control it — no admin override.
- `dose.start` returns **202** with a `details.job_id` before dispensing ends,
  so it is not a synchronous skill the way `weigh` is; whoever implements it
  must decide whether the catalog models the submit-and-poll shape or the
  action is held back until it does. Both balances report the dosing head
  absent today, so holding it back costs nothing.
- Parity test modelled on `test_solid_doser_names_match_device_allowed_actions`
  (no network; the advertised union recorded in the test with the live
  verification date), asserting the dotted-name → slashed-endpoint mapping.

## See also

- `docs/STATUS_SPEC.md` — the binding device contract; §6.2 on never
  advertising an action the hardware would refuse
- `docs/SKILLS_CATALOG.md` — `SkillDef` / `Skill` and who governs the kind enum
- `skills/src/lab_skills/skill_catalog/registry.py` — `skills_for()` and the
  two existing per-device overrides
- `skills/src/lab_skills/skill_catalog/power_strip.py` — the sibling change
  that made the HTE bench strips drivable
