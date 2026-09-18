# hermes-lab-runner — Slack wiring runbook

Connects the **boxed** `lab-runner` Hermes profile (OS user `hermes`; see
`docs/AGENTIC_LAB_DESIGN.md` Part II and `docs/HERMES_ACCESS_DESIGN.md`) to
Slack. The five wiring conditions this implements: connector runs in the box;
per-user allowlist; channel = confidentiality domain; provider chain known
and disclosed; SOUL.md in place before the floodgates.

Files here are **templates** (no secrets). The live profile is machine-local
at `/home/hermes/.hermes/profiles/lab-runner/` and is deliberately not in
git.

## Steps (one command per line — this terminal mangles multiline pastes)

### 1. Install SOUL.md + the extended config into the profile

```
sudo -iu hermes cp /home/sdl2/caoyang/ac-organic-lab/deploy/hermes-lab-runner/SOUL.md /home/hermes/.hermes/profiles/lab-runner/SOUL.md
sudo -iu hermes cp /home/sdl2/caoyang/ac-organic-lab/deploy/hermes-lab-runner/config.yaml /home/hermes/.hermes/profiles/lab-runner/config.yaml
sudo -iu hermes chmod 600 /home/hermes/.hermes/profiles/lab-runner/config.yaml
```

### 2. Create the Slack app (its own bot identity — never reuse PyPoe's)

Generate the manifest, then create the app at https://api.slack.com/apps →
"From a manifest", in the lab workspace:

```
sudo -iu hermes /usr/local/bin/hermes slack manifest
```

App name: `SDL2 Lab Runner` (the live app, renamed at go-live). After creating: install to workspace,
collect the **bot token** (`xoxb-…`) from OAuth & Permissions (plus the app
token `xapp-…` if the manifest enables Socket Mode).

### 3. Tokens into the boxed profile .env (600, owned by hermes — never sdl2's env)

```
sudo -iu hermes nano /home/hermes/.hermes/profiles/lab-runner/.env
```

Add (alongside the existing OPENROUTER_API_KEY):

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...   # only if the manifest uses Socket Mode
SLACK_HOME_CHANNEL=C...    # default delivery channel (see the trap below)
SLACK_HOME_CHANNEL_NAME=Lab
```

> **Home-channel trap (hit 2026-08-14).** The env var above is the ONLY
> reliable way to set the home channel for this profile. In `config.yaml`,
> `home_channel:` must be a *dict* (`{platform, chat_id, name}`) — a bare
> string is **silently ignored** (same class of trap as `custom_toolsets`).
> And the gateway's native `/sethome` is admin-tier, so with
> `user_allowed_commands` locked to `help/status/stop` no channel user can
> set it from Slack. The live profile carries `SLACK_HOME_CHANNEL` in its
> `.env` since 2026-08-14; restart `hermes-slack.service` after changing it.

### 4. Fill the allowlist BEFORE first start

Edit `platforms.slack.allow_from` in the live config (Slack member IDs,
`U…`, comma-separated — profile → three dots → "Copy member ID"):

```
sudo -iu hermes nano /home/hermes/.hermes/profiles/lab-runner/config.yaml
```

Remember: everyone on that list can trigger/abort human-authorized runs and
is inside the agent's confidentiality domain (Phase 4.2). The
`user_allowed_commands` gate is already set — `/model` (which can CHANGE the
model) stays disabled for channel users; model choice is a host-side admin
decision (Phase 4.4).

> **`allow_from` is inert for Slack (found 2026-09-18).** The Slack adapter
> never reads `platforms.slack.allow_from` — only the WhatsApp and WeCom
> adapters do. It reads the env vars `SLACK_ALLOWED_USERS` (platform) and
> `GATEWAY_ALLOWED_USERS` (global), comma-separated member IDs, from the
> profile `.env`. With neither set the gateway logs *"No env user allowlists
> configured … will deny unknown senders"* and **fails closed**: the pairing
> gate (§5b) is then the only admission path, and interactive components
> (slash-confirm buttons) are refused for everyone. Same trap class as
> `custom_toolsets` and `home_channel` above. To have a real allowlist, put
> `SLACK_ALLOWED_USERS=U…,U…` in `.env`, not in `config.yaml`.

### 5. Install + start the connector (root)

```
sudo cp /home/sdl2/caoyang/ac-organic-lab/deploy/hermes-lab-runner/hermes-slack.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-slack.service
systemctl status hermes-slack.service
```

### 5b. Pair each user (discovered at go-live 2026-08-13)

The gateway has its own per-user pairing gate on top of `allow_from`: an
unrecognized Slack user who DMs the bot gets a pairing code, and only a
host-side approval admits them — Phase 4.2 (audience = confidentiality
domain) enforced by mechanism. For each new user:

```
sudo -iu hermes /usr/local/bin/hermes pairing approve slack <CODE>
```

Codes expire; have the user re-DM the bot for a fresh one if needed.
Approving someone means they can trigger/abort human-authorized runs and
are inside the agent's memory audience — same judgement as `allow_from`.

### 6. Verify (in Slack, from an allowlisted account)

The runner does not author or compile protocols. Before testing a real
Cytation run, the selected project repo must map its protocol action to a
`plate_reader` skill in `compile/actions.yaml`, pin
`plate_reader: cytation_5` in `compile/binding.yaml`, merge the protocol to
`main`, and obtain a human-created bitácora authorization. The generic bitácora
template intentionally leaves those project-specific maps empty.

- DM the bot: "which model are you?" → it answers from its config (SOUL.md
  mandates truthful model disclosure — the "show the model" requirement).
- "list current equipment" → the live roster via lab-history (57 entries on
  the current registry; the count tracks `equipment.yaml`, not this doc).
  An empty answer means `lab-history` cannot find its database — see §8d.
- "preflight a plate-reader absorbance step for A1 at 600 nm" → reports a
  dry-run verdict; it must not claim or POST to the reader. The template's
  `lab-skills` args pin `--binding plate_reader=cytation_5`; the **live**
  profile's do not, so expect it to ask for an explicit binding rather than
  fail (§8f).
- "get_run run_nope" → relays `unknown_run` (proves lab-runs through Slack).
- Given an authorization id supplied by a human for a main-merged Cytation
  protocol, `start_run(..., dry_run=true)` must pass before the real
  `start_run(..., dry_run=false)` is offered. The executor, not the bot,
  re-verifies the digest, binding, revocation, live actions, and interlocks.
- From a NON-allowlisted account: the bot must not respond.
- `sudo -iu hermes ls /home/sdl2/caoyang/ac-organic-lab/.env` still denied
  (the box holds with the gateway running).

### 7. Record

Add the Slack app name + workspace to `docs/AGENTIC_LAB_DESIGN.md`'s agent
surfaces map row for lab-runner, and note the go-live date.

### 8. Bringing the runner up on a fresh host — what steps 1–7 omit

Everything below was found moving the live instance from gaia to
`sdl2-server-agents` (100.64.254.6) on 2026-09-18. Each item made a gateway
that *started and reported healthy* while doing the wrong thing, so they are
recorded here rather than in memory. Order matters for (c).

**a. Install the agent for the `hermes` user first.** The unit's
`ExecStart=/usr/local/bin/hermes` and HERMES_ACCESS_DESIGN Phase 0 assume a
hermes-owned install at `/home/hermes/.hermes/hermes-agent` with its own venv
and the wrapper in `/usr/local/bin`. The venv cannot be copied (editable
install, absolute shebangs). Pin the same commit the old host runs
(`sudo -iu hermes /usr/local/bin/hermes --version` there — the live build was
v0.19.0 · `e57918ac`). `requires-python` is `>=3.11,<3.14`; Ubuntu 26.04 ships
only 3.14 and packages no older interpreter, so give the hermes user its own
uv-managed 3.13 (`uv python install 3.13`, `uv venv --python …`) — never a
symlink into sdl2's uv cache. Pre-warm a uv cache as sdl2 and install
`--offline` if the hermes user has no egress.

**b. `~/.hermes/active_profile` selects the profile — not the unit.** The
unit sets no `HERMES_HOME`; `hermes_cli/main.py` reads
`/home/hermes/.hermes/active_profile` at launch and points `HERMES_HOME` at
`profiles/<name>`. That file sits *outside* `profiles/lab-runner/`, so a
profile copy does not bring it. Without it the gateway boots the **default**
profile — no tokens, no MCP servers, wrong SOUL.md — and looks fine. Check
the log for `Active profile: lab-runner`.

**c. Lock first, then TWO traversal ACLs.** Phase 0's order: `chmod 700
~/.claude ~/.codex ~/.config/gh` (agent transcripts quote secrets), *then*
`setfacl -m u:hermes:x /home/sdl2`. A second ACL that gaia carries but Phase 0
never recorded: `setfacl -m u:hermes:x /home/sdl2/.local/share` — the repo
venv's interpreter is a uv-managed Python under it. Without it `lab-skills`
is reachable but "cannot execute". Verify as hermes: `.venv/bin/lab-skills
--help` runs; `ls /home/sdl2`, `~/.claude`, `~/.ssh`, the repo `.env` are all
denied.

**d. `lab-history` needs `LAB_DB_PATH`, and not the live database.** The
profile sets none, so it falls back to `<repo>/data/lab.db`, which exists on
gaia and nowhere else. The live history DB is WAL-mode and `600`; a read-only
SQLite reader on a WAL database must write the `-shm` file, which breaks the
Phase 0 invariant that hermes cannot write lab data. Use the snapshot timer
in this directory instead (`lab-history-snapshot.{sh,service,timer}`:
`Connection.backup()` → `journal_mode=DELETE`, atomic rename, hermes gets
`r` via ACL, every 15 min ≈ 484 MB written) and set
`mcp_servers.lab-history.env.LAB_DB_PATH: /data/dashboard/snapshots/lab.db`.

**e. Verify with stdin at EOF.** `lab-history-mcp`, `lab-runs-mcp`,
`lab-control-mcp`, `lab-inventory-mcp` have no argument parsing: `--help`
starts the stdio server and hangs on a TTY. `cmd </dev/null` exits 0 if it
boots. Only `lab-skills` has a real `--help`.

**f. Template drift.** The live profile runs `model.default: z-ai/glm-5.3`
(template: `glm-5.2`) and its `lab-skills` args omit `--binding
plate_reader=cytation_5`. Trust the live profile over `config.yaml` here;
reconcile deliberately, not mid-migration.

**g. Move the state after stopping the old instance.** Copy config/`.env`
any time; re-copy `state.db`, `sessions/sessions.json`,
`channel_directory.json`, `gateway_state.json` (Slack pairings live there)
*after* `systemctl stop` on the old host, or the WAL copy can be torn. Delete
any stale `state.db-shm`/`-wal` on the new host before dropping the clean
copy in. Transfer file-to-file (tar as root → `scp -3` → `shred -u`); never
cat a profile into a terminal.

**h. Never run two instances on one Slack app token.** Socket Mode delivers
each event to exactly one connected gateway, so "verify the new one while the
old still runs" is a coin flip. Verify locally, stop the old, start the new,
then run §6 from Slack.

**i. `failed` after a clean stop is not a health signal.** The gateway exits 1
on SIGTERM, so `systemctl stop` always leaves the unit `failed`. Read
`is-enabled` and the log, not the colour.

## Division of labour (settled 2026-08-12)

PyPoe keeps the plumbing (alert fan-out, Kuma tile, `claude -p`
investigations, multi-model chat); lab-runner takes conversation + triggers.
Two bots, one channel is fine: the alarm system and the operator.
