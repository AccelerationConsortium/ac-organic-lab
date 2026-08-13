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
```

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

- DM the bot: "which model are you?" → it answers from its config (SOUL.md
  mandates truthful model disclosure — the "show the model" requirement).
- "list current equipment" → ~33 entries via lab-history.
- "get_run run_nope" → relays `unknown_run` (proves lab-runs through Slack).
- From a NON-allowlisted account: the bot must not respond.
- `sudo -iu hermes ls /home/sdl2/caoyang/ac-organic-lab/.env` still denied
  (the box holds with the gateway running).

### 7. Record

Add the Slack app name + workspace to `docs/AGENTIC_LAB_DESIGN.md`'s agent
surfaces map row for lab-runner, and note the go-live date.

## Division of labour (settled 2026-08-12)

PyPoe keeps the plumbing (alert fan-out, Kuma tile, `claude -p`
investigations, multi-model chat); lab-runner takes conversation + triggers.
Two bots, one channel is fine: the alarm system and the operator.
