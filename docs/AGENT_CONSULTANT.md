# Agent Consultant

Agent Consultant lets an approved automation account, including Jiaru's agent,
ask for an answer and send feedback to a human. It runs alongside the dashboard
API, separate from the Hermes lab-runner Slack bot and its lab-control tools.

## Endpoints

Both endpoints require an ac_auth automation key in `X-Api-Key`. The API verifies
it through the existing ac_auth sidecar and stamps the verified actor; a caller
cannot choose the reported identity. The actor must also appear in the local
Agent Consultant allowlist. This host's allowlist is restricted to the three
approved researcher agents; equipment and Hermes principals are excluded.

```bash
curl -sS https://DASHBOARD_HOST/api/agent/questions \
  -H "X-Api-Key: $LAB_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What does this status mean?","context":"Caller-supplied details"}'
```

The response is `{"actor":"…","answer":"…"}`. `question` is required (up
to 4,000 characters); optional `context` is limited to 16,000 characters. One
Codex turn runs at a time, with a 180-second limit. The answer is general
advice based on the supplied text. It cannot inspect the repository, devices,
or lab records and must not be treated as a validated hardware plan.

```bash
curl -sS https://DASHBOARD_HOST/api/agent/feedback \
  -H "X-Api-Key: $LAB_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"message":"I found a discrepancy to review","context":"Optional details"}'
```

The response is `{"actor":"…","delivered":true}` only after Slack accepts
the message. `message` is required and `context` optional, each up to 2,500
characters. Feedback is relayed as plain text with the verified actor. It does
not pass through an LLM or trigger a lab action. If Slack delivery is not
configured or fails, the endpoint returns an error and the caller may retry;
there is no durable queue.

## Deployment

Create `api/agent-consultant.local.json` on the API host with an
`allowed_principals` array of exact ac_auth principal names. This file is
gitignored and should be readable only by the API service account. There is no
default allowlist: a missing or invalid file returns HTTP 503, and a verified
principal absent from it receives HTTP 403. Set
`AGENT_CONSULTANT_ALLOWLIST_PATH` in the API service environment only if the
file lives elsewhere. The API reads the file for each request, so an allowlist
change takes effect without restarting the API.

The API verifies the machine key, then forwards requests over a private Unix
socket to a separate worker. The deployed API unit has `IPAddressDeny=any` and
cannot reach Codex or Slack directly. Install the template
[`deploy/agent-consultant.service.example`](../deploy/agent-consultant.service.example)
as a separate systemd unit under a dedicated account. Set `SERVICE_USER`,
`SERVICE_GROUP` (shared with the API), `REPO_ROOT`, and `CODEX_BIN`. Grant that
account a Codex CLI login and outbound access to the model service and Slack.
The socket defaults to `/run/agent-consultant/worker.sock`; set
`AGENT_CONSULTANT_SOCKET` in the API's private environment if needed. The
worker socket must stay off the public edge.

Put `AGENT_CONSULTANT_SLACK_WEBHOOK_URL` in `/etc/agent-consultant.env` with
root-only permissions. The webhook determines the receiving channel; it is not
committed to git. Do not reuse the PyPoe device-alert webhook or the Hermes
lab-runner's bot token for agent feedback. The Slack destination must be chosen
and its incoming webhook configured before feedback can be delivered.

Each question starts a fresh Codex CLI turn in an empty temporary directory.
The worker passes only the submitted question/context, uses a read-only sandbox
and approvals disabled, ignores user configuration, disables shell and plugin
features, and retains no Codex session. The dashboard's equipment and model
credentials are not passed to the Codex child. Keep the worker under a
least-privilege account because Codex's model service still receives the
submitted text.
