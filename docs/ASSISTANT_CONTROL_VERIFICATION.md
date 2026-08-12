# Assistant Control Mode — Verification Record

**Status:** Step 1 (propose → authorize) verified **end to end on the software path**,
2026-08-11, on branch `actionable-assistant` (working tree, uncommitted). Run on the
`sdl2-pc-03-cytation` device PC. **No hardware was connected**: the xArm service stayed at
`requires_init` throughout, and the one action that was authorized went to a stub. One bug was
found and fixed along the way (§2).

**Server deploy: 2026-08-11**, onto `sdl2-server-gaia`. It surfaced one further bug, in the same
spawn Finding 3 already covered but through a different mechanism — recorded in §4.3 and fixed
in `b542960`.

The design is [`UI_DESIGN.md`](UI_DESIGN.md) §5 — this document does not restate it. This is the
record of what was actually exercised, what broke, and what remains for the server.

> **Headline:** one real bug was found and fixed. The assistant reliably produces a valid
> proposal, but it originally **never reached the browser** — the SSE bridge dropped it on a
> tool-result envelope mismatch, which also silently swallowed the `assistant_proposal` audit
> row (Finding 1). With the fix applied, the whole software path works end to end: propose →
> `proposal` frame → audit row → Authorize → passthrough → `control_action` with
> `origin: assistant`. What remains is hardware-specific only.

---

## 1. Verdict

| Layer | State |
|---|---|
| `lab-control` MCP server, tool surface | ✅ exactly two tools, neither actuating |
| `propose_action` validation + refusal matrix | ✅ all seven gates fire with correct codes |
| Per-equipment authorization (`/authz/check`) | ✅ enforced; fails closed when the sidecar is down |
| Actor binding (`LAB_ACTOR` in env, not a tool argument) | ✅ |
| Server-side mode decision (client `mode` is advisory) | ✅ downgrades to ask without `X-Auth-User` |
| Model behaviour (finds device → checks actions → proposes) | ✅ clean three-call sequence |
| `proposal` SSE frame | ✅ **after the Finding 1 fix** (was ❌) |
| `assistant_proposal` audit row | ✅ after the same fix |
| Confirm-card rendering | ✅ by unit test; the live frame matches the tested fixture field-for-field (§7.6). No visual browser click was performed. |
| Authorize → passthrough → claim dance → device | ✅ against the stub |
| `control_action` row with `origin: "assistant"` | ✅ separates cleanly from tile clicks (`origin: null`) |
| Full production path (session cookie → middleware-injected identity) | ✅ verified through the Next dev server |
| Real hardware: arm `ready`, node pinned, real move | ⏸ server work (§9) |

---

## 2. Finding 1 — the `proposal` frame was dropped (**fixed**)

**Symptom.** In Control mode the model calls `propose_action`, the tool returns a valid
proposal, the model announces "confirm card is up" — and no card renders. The SSE stream
carries `tool_use` / `tool_result` / `text` / `done` and **no `proposal` frame**.

**Root cause.** `_proposal_from_tool_result` (`api/app/assistant.py:358-382`) expects the
tool_result content to be the tool's own JSON:

```json
{"proposal": {...}}
```

Claude Code (CLI 2.1.227) actually delivers MCP tool output **double-wrapped** — an outer
object whose `result` value is a JSON *string*:

```json
{"result": "{\"proposal\": {\"equipment_id\": \"xarm_translocation\", ...}}"}
```

So `json.loads(text)` yields `{"result": "..."}`, `data.get("proposal")` is `None`, the
function returns `None`, and `_translate_event` emits only `tool_result`.

**Evidence** (raw stream-json captured from the CLI, then replayed through the live parser):

```
parser result on the REAL block : None
frames from _translate_event    : ['tool_result']
outer keys                      : ['result']
inner keys                      : ['proposal']
unwrapped proposal action       : move.uplc_draw_home
```

**Second consequence — the audit gap.** `record_proposal` is wired as `on_proposal`, which only
fires when a proposal frame is produced. With the frame dropped, no `assistant_proposal` event
was recorded:

```
GET /api/history/events/xarm_translocation?event_type=assistant_proposal
→ {"device_id":"xarm_translocation","events":[]}
```

UI_DESIGN §5.3 wants the trail to record "what talked the operator into it", and one bug hid
both the card and the record. Worth noting for its own sake: **a dropped frame is silent on
every surface.** The model announces "confirm card is up" (it cannot see the browser), the tool
succeeded, the HTTP status is 200, and the audit table is simply empty — nothing anywhere
reports an error. After the fix the row appears as expected:

```
assistant_proposal rows: 1
 message: assistant proposed move.uplc_draw_home on xarm_translocation to operator@example.edu
 payload: {actor, action, passthrough_action, args: {node_id}, reason}
```

**Why the tests didn't catch it.** `api/tests/test_assistant.py` feeds
`_proposal_from_tool_result` the *unwrapped* shape (both the string and the
`[{"type":"text","text":...}]` list form). Both are shapes the CLI does not emit for MCP tool
results. The tests encode an assumed envelope rather than an observed one, so they pass while
the real path fails. Any fix should add a fixture captured from a real stream.

**Suggested fix** — tolerate one level of `{"result": <json string>}`, and keep accepting the
bare shape so it is robust across CLI versions (the envelope is a CLI implementation detail,
not a contract):

```python
for text in texts:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        continue
    # Claude Code wraps MCP tool output as {"result": "<json string>"}; older
    # builds pass the tool's JSON through unchanged. Accept both.
    if isinstance(data, dict) and isinstance(data.get("result"), str):
        try:
            data = json.loads(data["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(data, dict) and isinstance(data.get("proposal"), dict):
        return data["proposal"]
```

**Applied** (`api/app/assistant.py`), with two regression tests in
`api/tests/test_assistant.py` — one pinning the wrapped envelope captured verbatim from a real
stream, one asserting a *wrapped refusal* still yields no card, so unwrapping cannot manufacture
a proposal out of an error. After the fix, on the same captured block:

```
parser on the REAL block : PROPOSAL -> move.uplc_draw_home
frames from _translate   : ['tool_result', 'proposal']
```

and end to end, the browser now receives `{'tool_use': 4, 'tool_result': 4, 'text': 7,
'proposal': 1, 'done': 1}`.

---

## 3. Finding 2 — `lab-control-mcp` was never installed

`api/pyproject.toml:24` declares the console script, but only `lab-history-mcp.exe` existed in
`.venv/Scripts/`. This is the failure mode that **masquerades as success**: Control mode turns
purple, the assistant chats happily, and it simply never proposes — the model reports its tools
as unreachable, which reads like a connectivity problem.

Check it explicitly after any deploy of this branch:

```powershell
ls .venv/Scripts/ | Select-String "lab-control-mcp"
```

Note `uv sync --all-extras` does **not** fix this on a machine that cannot build the api
project (Finding 3). A targeted `uv pip install --no-deps -e ./api` regenerates the entry
points without touching dependencies.

---

## 4. Finding 3 — the MCP spawn went through `uv run` (**fixed**)

This one bit on **both** hosts, for two unrelated reasons, and cost an hour each time. It is
fixed at the source in `b542960`: the servers are now launched as the console scripts installed
beside the running interpreter, and `uv run` survives only as a fallback for a dev checkout.

### 4.1 The original spawn

`_write_mcp_config` spawned each server as:

```
uv run --project <repo>/api lab-{history,control}-mcp
```

`uv run` **syncs the project first**. Any dependency that cannot install on that host therefore
takes out the assistant's entire tool surface, silently — the failure appears only as the model
saying its tools are missing.

### 4.2 On the Windows device PC — an unbuildable dependency

On that PC the api package depends on `opentrons-shared-data` → `numpy 1.26.4`, which has no
cp314 wheel; with the venv on **Python 3.14.4** and no MSVC toolchain, uv tries a source build
and dies. Both MCP servers failed to start for this reason before it was diagnosed.

Workaround used locally — set `UV_NO_SYNC=1` in the API server's environment. The CLI inherits
it and passes it to the spawned servers, which then run against the existing venv:

```
UV_NO_SYNC=1 uv run --project <repo>/api lab-control-mcp
→ INFO:app.assistant_control:lab-control MCP server: 32 devices, actor=…, authz_enforced=True
```

> **This paragraph originally predicted the whole thing would be a non-issue on the Linux
> server, "because numpy ships wheels there". That was wrong** — see §4.3. The dependency tree
> was never the only way a self-syncing spawn can fail, and the prediction cost an hour of
> misdiagnosis on the server. Corrected 2026-08-11.

> A PATH shim named `uv.cmd` was tried first and did **not** work: the CLI spawns the MCP
> command without a shell, and Node cannot execute a `.cmd` that way on Windows. Only an `.exe`
> or the `UV_NO_SYNC` route works here.

### 4.3 On the Linux server — the unit's own sandbox (2026-08-11)

Same spawn, different mechanism, identical symptom. `uv run` syncs before it runs, and syncing
needs **`~/.cache/uv` writable**. `ac-organic-lab-api.service` sets `ProtectHome=read-only`
(deliberately — see the unit's own comment), so under systemd uv cannot write its cache and
every MCP server failed to start.

What made it expensive is that the failure is invisible from both ends:

- the CLI records it as `status: "failed"` in its **init event**, which the SSE bridge does not
  forward (it forwards only `text` / `tool_use` / `tool_result` / `proposal` / `done` / `error`);
- `app.assistant`'s log lines are INFO on an unconfigured logger, so they never reach the
  journal (Finding 5) — and the code had no error to log anyway, since `claude` exits 0;
- the model, seeing zero tools, says its tools are unreachable. Every reading of that sentence
  points at connectivity, an uninstalled entry point (Finding 2), or the MCP config — not at the
  service unit.

It is **not** control-mode specific: Ask mode was equally toolless. Any `mode=ask` chat that
answers without ever calling a tool is the cheap check.

**How it was bisected**, with `bwrap` reproducing the unit's `ProtectHome=read-only`:

| condition | init event |
|---|---|
| HOME read-only (mimics the unit) | `lab-history=failed` |
| HOME writable | `lab-history=connected` |
| read-only HOME, `~/.claude` writable | `failed` |
| read-only HOME, `~/.cache/uv` writable | `connected` ← pins it to uv, not to the CLI |
| direct console script, HOME fully read-only | `connected` |

> **`systemd-run --user` is useless for this test.** It silently ignores `ProtectHome` and
> `PrivateTmp` — a transient user unit asked for `ProtectHome=read-only` still writes to
> `$HOME`. Three "the sandbox is ruled out" results were produced this way before the check
> `touch $HOME/x` exposed them as unsandboxed. Use `bwrap`, or a **system** transient unit.

**The fix** (`b542960`): `_mcp_server_command()` prefers the console script beside
`sys.executable` — the same venv serving the app — which needs no writable HOME at all. Both
servers were then verified `connected` under a fully read-only home, with `lab-control` answering
`list_available_actions`. `uv run --project` remains only as the fallback for a dev checkout
where the api package is not installed into its own environment.

Two consequences worth carrying forward:

- **Finding 2's check is now load-bearing, not merely diagnostic.** If the console script is
  missing from the venv the resolver falls back to `uv run`, which is exactly the path that
  fails under systemd. Check it after any deploy, on Linux too:
  `ls .venv/bin/lab-*-mcp`.
- The assistant's tool surface no longer depends on the api dependency tree resolving on every
  chat turn — which is what §4.2 and §4.3 are both really instances of.

**Side effect on the test suite.** Because `opentrons_shared_data` is absent for the same
reason, `api/tests/test_labware.py` fails two tests on this PC
(`test_standard_definitions_served`, `test_upload_cannot_shadow_standard_definition` — both
`assert 0 > 100`, i.e. zero standard definitions found). Pre-existing and unrelated to the
assistant; the rest of `api/tests` is green (149 passed) and `web` is fully green (109 passed).

---

## 5. Finding 4 — the auth sidecar cannot start on Windows

`auth/ac_auth/main.py:256-258` guards the SIGHUP roster-reload registration:

```python
try:
    loop.add_signal_handler(signal.SIGHUP, _reload_roster)
except (NotImplementedError, ValueError, RuntimeError):
```

The comment explicitly anticipates Windows, but the guard is on the wrong expression: on
Windows `signal.SIGHUP` **does not exist**, so evaluating the *argument* raises `AttributeError`
before `add_signal_handler` is reached. Startup aborts:

```
AttributeError: module 'signal' has no attribute 'SIGHUP'
ERROR:    Application startup failed. Exiting.
```

Harmless in production (Linux), but it blocks any auth work from a Windows device PC. One-line
fix, either:

```python
except (AttributeError, NotImplementedError, ValueError, RuntimeError):
# or
sighup = getattr(signal, "SIGHUP", None)
if sighup is not None:
    loop.add_signal_handler(sighup, _reload_roster)
```

Locally this was worked around by defining `signal.SIGHUP` before importing the app, which lets
the call raise the `NotImplementedError` that ac_auth already handles (degrading to
"restart to pick up roster changes").

---

## 6. Finding 5 — the mode-downgrade log line is invisible under plain `uvicorn`

`assistant.py` logs `assistant chat: user=… mode=control->ask` on the `app.assistant` logger,
which is the documented way to see whether Control mode was honoured. Under a bare
`uvicorn api.app.main:app` that logger is not configured, so the line never appears — only the
access log does.

A reliable observable signal exists regardless, and is why the code uses distinct filenames:
inspect `$ASSISTANT_RUNTIME_DIR`. `mcp.control.json` means control mode was granted;
`mcp.json` means it was downgraded to ask.

---

## 7. What was verified, and how

### 7.1 Tool surface is non-actuating (the safety claim)

```
server name : lab-control
devices     : 32
tool count  : 2
  - list_available_actions(equipment_id)
  - propose_action(equipment_id, action, args, reason)
```

### 7.2 Refusal matrix — every gate, direct tool calls

| Case | Action | Code |
|---|---|---|
| safety floor | `stop` | `unmappable_action` |
| lifecycle | `connect` | `not_allowed` |
| not advertised by the device | `move.deck_home` | `not_allowed` |
| bad args (`speed: "fast"`) | `move.robot_home` | `invalid_args` |
| unknown equipment | — | `unknown_equipment` |
| batch attempt (`"a,b"` as one id) | — | `unknown_equipment` |
| actor with no grant | `move.robot_home` | `not_authorized` |
| sidecar unreachable | `move.robot_home` | `not_authorized` (fails closed) |

The last two isolate the authorization gate: same legal action, only the actor changed
(`stranger@example.edu` → refused), and pointing `AUTH_SERVICE_BASE` at a dead port refuses
rather than passing.

### 7.3 Against the real xArm, read-only

The live device was probed **without connecting it** (it stayed `requires_init` throughout):

```
list_available_actions → equipment_status: requires_init, activity: idle
                         action: connect | proposable: False | passthrough: None
propose_action(move.uplc_draw_home)
                       → not_allowed, allowed_actions: ["connect"]
```

This exercises registry load, the live `/status` fetch, and the fail-closed path against real
hardware. It also shows lifecycle/safety-floor actions are excluded *before* any model sees
them.

### 7.4 Model behaviour

With the servers reachable, the sequence was clean and needed no prompting:

```
[TOOL_USE] list_equipment_now      → "Found it: xarm_translocation (UFactory xArm5), status ready."
[TOOL_USE] list_available_actions  → "move.uplc_draw_home is proposable. Submitting the proposal."
[TOOL_USE] propose_action          → "Proposal sent — confirm card … expires in 120s."
```

The proposal object itself was correct:

```json
{"equipment_id":"xarm_translocation","equipment_name":"UFactory xArm5","kind":"robot_arm",
 "action":"move.uplc_draw_home","passthrough_action":"graph/move_to",
 "args":{"node_id":"uplc_draw_home"},"actor":"operator@example.edu",
 "expires_in_s":120,"device_state":{"equipment_status":"ready","activity":"idle"}}
```

Note the model's closing claim that the card "is up" is **not** evidence the card rendered — it
cannot observe the browser. That is precisely how Finding 1 hid, and it is worth remembering the
next time this feature is debugged: **trust the frame, not the narration.**

### 7.5 Mode gating — the client's `mode` is advisory

The server decides the toolset, and the reliable observable is which config file it writes
(§6). All four cases behave as UI_DESIGN §5.2 specifies:

| Request | Config written | Servers |
|---|---|---|
| `mode: "control"` + verified `X-Auth-User` | `mcp.control.json` | `lab-history`, `lab-control` |
| `mode: "control"`, **no** identity | `mcp.json` | `lab-history` only |
| `mode: "ask"` + verified identity | `mcp.json` | `lab-history` only |
| `mode: "control"` + identity, but `DASHBOARD_CONTROL_OPEN=true` | `mcp.json` | `lab-history` only |

The last row is the important one: the dev bypass refuses Control mode even for a valid user,
because that path has no verified identity to bind a proposal to.

### 7.6 The confirm card, and why it is covered without a visual click

`web/src/components/AssistantBubble.test.tsx` renders the card from a fixture and asserts its
fields plus the Authorize call. The live frame was compared to that fixture:

```
missing from live : none
extra in live     : none
device_state keys : ['activity','equipment_status','message']
=> shapes match field-for-field: True
```

So the component under test receives exactly the shape the server now emits. **No browser
click was performed** — the rig in §8 is what to bring up if you want the visual.

### 7.7 Authorize → passthrough → audit (against the stub)

Sending exactly what the browser sends:

```
POST /api/equipment/xarm_translocation/control/graph/move_to
X-Auth-User: …            X-Control-Origin: assistant
{"node_id":"uplc_draw_home"}                                    → HTTP 200
```

The stub received `graph/move_to {'node_id': 'uplc_draw_home'}`, and since it 423s a tokenless
call, the per-request claim → act → release dance demonstrably ran. The audit trail then
separates the two origins cleanly:

```
action=graph/move_to  owner=operator@example.edu  origin='assistant'  outcome=ok
action=graph/move_to  owner=operator@example.edu  origin=None         outcome=ok   # tile click
```

(`origin: null` is what `/admin` renders as the muted `tile` label.)

### 7.8 The full production path, not just the API

Repeating the Control-mode turn through the **Next dev server** with only an `ac_auth_session`
cookie — no `X-Auth-User` header — still produced a proposal, so the middleware's
strip-then-inject of the verified identity works and the actor is resolved from the session
rather than anything the client supplied. Related checks:

- unauthenticated control POST through `:3000` → **401** `"Sign in to control equipment."`
- `GET /auth/verify` with the cookie → 200 + `x-auth-user`, `x-auth-role: none`
- `/api/auth/mine` → `{"role":"none","equipment":{"xarm_translocation":"user", …}}`

That last one is what gates the Control toggle, and it is the sharpest case: the flat role
`none` does **not** qualify, the single equipment grant does — so the toggle enables on the
grant alone.

> Note the grant value is `"user"`, not `"operator"`. `/authz/check` reports `allowed` for any
> device grant, so §5.2's "`operator`+ on that equipment" means in practice "holds a grant on
> that equipment" — identical to the tile path. Not a defect; just don't expect a role-name
> comparison.

### 7.9 Test suites

`api/tests/test_assistant.py`, `test_assistant_control.py`, `test_history_db.py` — **51 passed**
(49 before, plus the two Finding 1 regressions). `web` `AssistantBubble.test.tsx` — **4
passed**.

---

## 8. The local rig (reproducible, no repo edits)

Everything below lives outside the repo; the committed `equipment.yaml` and the production
`xarm` service were untouched.

| Piece | How |
|---|---|
| Stub device | scratchpad HTTP service on `:8123` serving a STATUS_SPEC v1.2 envelope with `allowed_actions: ["stop","move.robot_home","move.uplc_draw_home"]`, the claim trio, and `POST /control/graph/move_to` → 200. Needed because `propose_action` requires a **live** `move.*` in `allowed_actions`, which an unconnected arm never advertises. |
| Registry | copy of `equipment.yaml` with only the xArm `base_url` → `http://127.0.0.1:8123`, via `LAB_REGISTRY_PATH` |
| Roster | scratchpad `roster.yaml` (real email — never committed): one admin to satisfy validation, plus the test user at `role: none` with a single `{scope: equipment, id: xarm_translocation, role: operator}` grant, so the per-equipment check is genuinely exercised |

> **Redaction note.** The captured outputs quoted throughout this document are verbatim except
> for the operator's address, which is shown as `operator@example.edu`. The real one is a lab
> member's email — the same reason `roster.yaml` is gitignored while only
> `roster.yaml.example` is committed.
| Auth sidecar | `:8009`, `AUTH_ROSTER_PATH`, throwaway `AUTH_DB_PATH`, `AUTH_COOKIE_SECURE=false`, dummy `AUTH_SMTP_USER`/`AUTH_SMTP_PASSWORD` (the lifespan builds a mailer and raises without them; no mail is sent), plus the Finding 4 workaround |
| History DB | throwaway `LAB_DB_PATH` so test audit rows never land in the real `data/lab.db` |
| API | `:8001` with `UV_NO_SYNC=1`, `AUTH_SERVICE_BASE`, `ASSISTANT_RUNTIME_DIR`, and **`DASHBOARD_CONTROL_OPEN` unset** |

### Environment matrix that matters

| Var | Setting | Why |
|---|---|---|
| `DASHBOARD_CONTROL_OPEN` | **must be unset** | `=true` silently forces Control mode to ask (`assistant.py:661`) — the usual local dev bypass is unusable for this feature |
| `AUTH_SERVICE_BASE` | `http://127.0.0.1:8009` | forwarded into the MCP server env; unreachable ⇒ every proposal refused |
| `CONTROL_AUTHZ_ENFORCE` | leave enforcing | `=false` skips `/authz/check` entirely |
| `LAB_ACTOR` | set by `assistant.py`, never by hand | the actor rides in the child env, not a tool argument |
| `ASSISTANT_RUNTIME_DIR` | any writable dir | holds `mcp.json` / `mcp.control.json` — the reliable mode signal (Finding 5) |
| `UV_NO_SYNC` | `1` on this PC | Finding 3 |

Login without SMTP: mint a code with ac_auth's own `Db.create_login_code(email, code, ttl)` and
submit it to `POST /auth/verify-code`, or mint a session directly with `Db.create_session`.
Codes are stored hashed, so they cannot be read back out of the DB.

---

## 9. Server handoff — what is left

The software path is verified, so what follows is hardware-specific only. Step 2 is the one most
likely to trip you up.

> **Steps 1-6 done, 2026-08-12 — the assistant proposed a real move on real
> hardware.** With the arm connected and `opentrons_home` pinned through the
> audited passthrough, a Control-mode turn produced a validated proposal:
> `move.robot_home` → `graph/move_to {"node_id":"robot_home"}`, actor bound to
> the verified operator, 120 s expiry, device state attached. The model's path
> was clean — `list_equipment_now` → `list_available_actions` → `propose_action`.
> **Nothing was authorized, so nothing moved**; steps 7-8 (authorize, then check
> both audit rows) are what remain, and they are a deliberate human decision
> rather than a verification chore.
>
> Getting there required two fixes found on the server, neither of them in this
> feature: the MCP spawn (§4.3) and a per-device edge-secret mismatch that made
> *every* dashboard control action on this arm fail 401
> ([`AUTH_DESIGN.md`](AUTH_DESIGN.md) → *How a device learns who the operator
> is*).
>
> **Earlier progress, 2026-08-11 (server).** Steps 1-2 were done: `lab-control-mcp` is installed in the
> gaia venv, and the arm has been connected by hand — it reports `ready` / `idle`. Step 4 (the
> node pin) is the current blocker and behaves exactly as described below: with
> `details.current_node` still `null`, `/status.allowed_actions` is `["stop"]` alone, so the
> assistant correctly reports that it has no proposable move. `GET /graph/nearest` suggests
> `opentrons_home` (arm residual 7e-5 deg, rail 0.0 mm, gripper `empty` and matching,
> `within_tolerance: true`). Steps 5-8 remain.

1. Confirm `lab-control-mcp` is installed on the server venv (Finding 2) — the single check most
   likely to save an hour of misdiagnosis.
2. Bring the arm to `ready`. Note `equipment.yaml:119` carries `do_not_call_connect: true` —
   automation must not connect this arm; a human does it. Connecting energizes the servos
   (`motion_enable`) and the gripper/track but commands no motion, and the linear track is
   enabled **without** a homing sweep.
4. **Pin the current node**, or `allowed_actions` collapses to `["stop"]` and no move is
   proposable. Graph mode boots STRICT, and `current_node` stays `None` until pinned. Read
   `GET /graph/nearest` first and pin the node it suggests with
   `POST /control/graph/recover_to {"node_id":"<suggested>"}` — **without `force`**, so the
   device validates the claim against the real pose and refuses a false one. This is
   bookkeeping, not motion. It is claim-gated: claim → pin → **release**, or the leftover claim
   will 423 the passthrough later.
5. Confirm `/status.allowed_actions` now lists `move.<node>` targets. From a fresh `robot_home`
   pin, `uplc_draw_home` is the only single-hop target (`motion_graph.yaml` edges); use
   `graph/travel_to` for multi-hop.
6. Propose, and confirm the card renders with the device-authoritative fields.
7. Only then decide about authorizing. The passthrough claims as the human, runs one action, and
   releases in a `finally`; the device's 412/423 is the only backstop (no interlocks — ARCHITECTURE
   decision #1), which is why Step 1 is capped at one device per proposal.
8. Check both audit rows: `control_action` with `origin: "assistant"` (purple pill on `/admin`)
   and the `assistant_proposal` event.

---

## 10. Incidental findings (not this feature's fault, not fixed)

- **`stop` has no route on the xArm.** `/status.allowed_actions` advertises `"stop"` and
  `web/src/lib/api.ts:275` composes `POST /control/stop`, but the device implements only
  `POST /move/stop` → **404**. Harmless to Control mode (`stop` is not proposable) but the
  dashboard's stop button for this device cannot work.
- `web/src/components/AssistantBubble.test.tsx:40` uses `move.plateloc_out`; the `plateloc_*`
  nodes are commented out of `motion_graph.yaml`. The test passes because the device is mocked,
  but the fixture no longer resembles a legal action.
- README's `docs/UI_DESIGN.md` row lists §1–§3 and omits §5.
- `xarm-translocation/README.md:94` points at `src/docker/docker_setup.sh`, which does not
  exist; `src/docs/PYXARM_TESTING.md` still documents the removed in-process
  `simulation_mode=True` path as "Stage 1".

## See also

- [`UI_DESIGN.md`](UI_DESIGN.md) §5 — the design this verifies; §5.1 is the propose-only safety
  argument.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — the actor binding and `operator`+ requirement.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #10 — proposer, not actuator; the three MCP
  servers and their distinct trust levels.
- [`STATUS_SPEC.md`](STATUS_SPEC.md) §6.2 — why `allowed_actions` is the authority a proposal
  must respect.
