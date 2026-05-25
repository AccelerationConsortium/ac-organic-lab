# Device PC Setup

**Audience:** anyone bringing up a Windows PC that hosts one or more lab equipment REST services (plateloc, platereader, platestacker, fume hood actuator, etc.).
**Goal:** get every device service running as an auto-starting Windows Service, isolated from every other service on the same PC, with a uniform install / update / log story across the lab.

This document is the **canonical install recipe** for any device repo that conforms to [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) (v1.0 baseline or v1.1 with claims). Each device repo's README links here rather than duplicating the recipe.

## TL;DR

- One **uv** binary, copied to `C:\SDL_Tools\uv.exe`, manages every device repo's Python environment. (`SDL_Tools` is namespaced to the Self-Driving Lab so it does not collide with arbitrary other software dropped into `C:\Tools` by IT or third-party installers.)
- Each device repo has its own `.venv\` and its own pinned `uv.lock`, so dependency upgrades on one service cannot break another.
- **NSSM** (the Non-Sucking Service Manager) wraps each service into a real Windows Service that auto-starts on boot, restarts on crash, and writes rotated log files. We keep `nssm.exe` next to `uv.exe` in `C:\SDL_Tools\`.
- One PowerShell script (`update_all.ps1`) pulls + re-syncs + restarts every service in a single pass.

If you only ever read one section, read [§3 Install a single device service](#3-install-a-single-device-service).

## 1. Prerequisites

- **Windows 10 or 11** (Pro recommended; required if you want NSSM to run as a non-Administrator account).
- A **lab user account** with "Log on as a service" rights. NSSM grants this automatically when you set `ObjectName` (see §3 step 6). Do **not** run device services as `LocalSystem` — see [§5 Run-as user choice](#5-run-as-user-choice) for why.
- **Tailscale** enrolled with the same tailnet as the dashboard. Device services bind to `0.0.0.0:<port>`; access is gated by Tailscale ACLs, not by Windows firewall rules.
- **PowerShell 5.1 or later** (ships with Windows; no install needed).
- **Per-device pre-reqs** (when applicable):
  - 32-bit Python + `pywin32` for ActiveX-backed drivers (`agilent-plateloc-server`, future Agilent devices). The device repo's README documents the exact `py -X.Y-32` setup; the `.venv` managed by uv is unaffected by it.
  - A serial-port profile created in the vendor's diagnostics dialog (PlateLoc, etc.). Profile creation requires **Administrator** the first time; normal operation does not.

## 2. One-time host setup

These three steps run **once per PC**, not per service. Run from an elevated PowerShell.

### 2.1 Install uv to a system-wide path

```powershell
# Run from an elevated PowerShell.
New-Item -ItemType Directory -Force C:\SDL_Tools | Out-Null

# Install uv via the official installer (drops it under %USERPROFILE%\.local\bin).
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Copy to a stable service path so the Windows Service account can find it.
# Services do NOT inherit the interactive user's PATH, so a per-user install
# alone is not enough. The fallback handles shells where PATH has already
# been refreshed and uv is discoverable via Get-Command.
$uvUser = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (!(Test-Path $uvUser)) {
    $uvUser = (Get-Command uv -ErrorAction Stop).Source
}
Copy-Item $uvUser C:\SDL_Tools\uv.exe -Force

# Verify the exact binary NSSM will call later.
C:\SDL_Tools\uv.exe --version
```

### 2.2 Install NSSM

```powershell
# Preferred: install via winget (built into modern Windows).
winget install -e --id NSSM.NSSM

# Alternative: install via Chocolatey.
choco install nssm -y

# Or download nssm.exe from https://nssm.cc/download and drop it on PATH
# (typically C:\SDL_Tools\nssm.exe)
```

`nssm --version` should print `NSSM 2.24-101-g897c7ad` or similar.

### 2.3 Create the lab directory layout

```powershell
New-Item -ItemType Directory -Force C:\Users\sdl2\Projects | Out-Null
New-Item -ItemType Directory -Force C:\SDL_Logs            | Out-Null
```

Convention: every device repo lives at `C:\Users\sdl2\Projects\<repo-name>\` and writes its logs to `C:\SDL_Logs\<service-name>.{out,err}.log`. Stick to this layout — the troubleshooting and update scripts below assume it. If the service runs under a different lab user, replace `sdl2` with that Windows username consistently.

## 3. Install a single device service

Run from an elevated PowerShell. Replace `<repo>`, `<svc>`, and `<port>` with the device-specific values from the table in [§7 Conventions](#7-conventions).

```powershell
# 3.1 Clone the device repo into the lab directory
cd C:\Users\sdl2\Projects
git clone https://github.com/cyrilcaoyang/<repo>.git
cd C:\Users\sdl2\Projects\<repo>

# 3.2 Copy the example config and edit for this PC
Copy-Item config.example.toml config.toml
notepad config.toml      # set com_port, profile, port, enforce_claims, etc.

# 3.3 Create the .venv and install runtime deps
C:\SDL_Tools\uv.exe sync --extra api

# 3.4 Smoke-test in the foreground (Ctrl-C to stop)
C:\SDL_Tools\uv.exe run --extra api <svc>-serve --port <port>
# In another shell:  curl http://127.0.0.1:<port>/   -> protocol_version field

# 3.5 Install the Windows Service
nssm install <svc> C:\SDL_Tools\uv.exe `
    run --project C:\Users\sdl2\Projects\<repo> --extra api <svc>-serve

# 3.6 Configure it
nssm set <svc> AppDirectory       C:\Users\sdl2\Projects\<repo>
nssm set <svc> DisplayName        "<Human-readable device name> REST service"
nssm set <svc> Description        "STATUS_SPEC service for <device>"
nssm set <svc> Start              SERVICE_AUTO_START
nssm set <svc> AppStdout          C:\SDL_Logs\<svc>.out.log
nssm set <svc> AppStderr          C:\SDL_Logs\<svc>.err.log
nssm set <svc> AppRotateFiles     1
nssm set <svc> AppRotateBytes     10485760           # 10 MB
nssm set <svc> AppExit Default    Restart            # like Restart=on-failure
nssm set <svc> AppRestartDelay    5000               # 5 s backoff

# 3.7 Run as the lab user (NOT LocalSystem -- see §5)
nssm set <svc> ObjectName         ".\labuser" "<password>"

# 3.8 Start it
nssm start <svc>
```

Verify:

```powershell
sc query <svc>                                       # STATE = RUNNING
Get-Content C:\SDL_Logs\<svc>.out.log -Tail 20       # uvicorn startup messages
curl http://127.0.0.1:<port>/                        # protocol_version field
curl http://127.0.0.1:<port>/status | ConvertFrom-Json | Format-List
```

## 4. Update workflow

Per-service update (run any time after a `git push` to the device repo):

```powershell
# C:\Users\sdl2\Projects\update.ps1
param([string]$Service, [string]$RepoDir)
Push-Location $RepoDir
git pull
C:\SDL_Tools\uv.exe sync --extra api
nssm restart $Service
Pop-Location
```

```powershell
.\update.ps1 plateloc C:\Users\sdl2\Projects\agilent-plateloc-server
```

Multi-service "update everything" (run nightly via Task Scheduler, or manually after an SDK release):

```powershell
# C:\Users\sdl2\Projects\update_all.ps1
$services = @(
    @{ Name = "plateloc";     Repo = "C:\Users\sdl2\Projects\agilent-plateloc-server" },
    @{ Name = "platereader";  Repo = "C:\Users\sdl2\Projects\bmg_platereader"      },
    @{ Name = "platestacker"; Repo = "C:\Users\sdl2\Projects\agilent_platestacker" }
)
foreach ($s in $services) {
    Write-Host "=== $($s.Name) ===" -ForegroundColor Cyan
    Push-Location $s.Repo
    git pull
    C:\SDL_Tools\uv.exe sync --extra api
    nssm restart $s.Name
    Pop-Location
}
```

The shape is uniform across every device because `uv run --project <path>` makes the supervisor config trivial. Conda equivalents need `cmd.exe /c "conda activate ..."` wrappers and have caused service-startup races in practice.

## 5. Run-as user choice

Always set `nssm set <svc> ObjectName ".\<labuser>" "<password>"` to a real local user account. **Do not run device services as `LocalSystem`** for two reasons:

1. **`HKCU` registry hive.** Vendor drivers (PlateLoc ActiveX, BMG Reader Control, etc.) store profiles in the user's `HKCU\Software\<vendor>` hive. `LocalSystem` has its own hive and will not see the profile you created interactively in the diagnostics dialog. Symptom: the service starts but every `/control/startup` returns 503 with a "profile not found" or "could not open device" message.
2. **COM / serial port permissions.** Some COM ports are bound to the interactive user's session. `LocalSystem` may fail to open them.

The lab account also needs the "Log on as a service" right. NSSM grants this automatically when you set `ObjectName` and gives a clear error message if it cannot.

## 6. Logs, health checks, and boot ordering

- **Logs.** NSSM appends to `AppStdout` / `AppStderr`. `AppRotateFiles=1` + `AppRotateBytes=10485760` rotates at 10 MB. For richer rotation, send uvicorn's logs to the Windows Event Log via `nxlog` or similar — but for the small log volume of a device service, NSSM's built-in is sufficient.
- **Health check.** Every device exposes `GET /health` (always 200 unless the process itself is broken) and `GET /status` (always 200 unless the process is broken; reports `requires_init` / `degraded` / `error` in-band). The dashboard's polling already shows you which services are up; you do not need a separate Nagios-style monitor.
- **Boot ordering.** Almost never needed on modern Windows. If a service depends on a specific virtual interface (Tailscale, etc.) being up, set `nssm set <svc> DependOnService Tailscale` so Windows Service Control Manager waits.

## 7. Conventions

| Repo                      | Service name   | Port | Entry point / NSSM AppParameters          | Tailnet host        |
|---------------------------|----------------|------|-------------------------------------------|---------------------|
| `xarm-translocation`      | `xarm`         | 8000 | `run pyxarm api` (NOT `pyxarm web`)       | `sdl2-pc-03-cytation.<tailnet>` |
| `agilent-plateloc-server` | `plateloc`     | 8010 | `run --extra api agilent-plateloc-serve`  | `sdl2-pc-03-cytation.<tailnet>` |
| `agilent-cytation-server` | `cytation`     | 9333 | `run agilent-cytation-serve`              | `sdl2-pc-03-cytation.<tailnet>` |
| `opentrons-server`        | `ot2-gateway`  | 8020 | `run uvicorn opentrons_server.gateway.api:app --host 0.0.0.0 --port 8020` | `sdl2-pc-03-cytation.<tailnet>` |
| `torry-pines-shaker-server` | `torry-pines-shaker` | 8030 | `run --extra api torry-pines-shaker-serve` | `sdl2-pc-03-cytation.<tailnet>` |
| `agilent-biostack4-standalone` | `biostack4`     | 8050 | `run --extra api agilent-biostack4-serve --dry-run` (set `[service].port = 8050` in `config.toml` to override the 8030 default) | `sdl2-pc-03-cytation.<tailnet>` |
| `ac-organic-lab`          | `ac-organic-lab-api` | 8001 | `run uvicorn app.main:app --host 0.0.0.0 --port 8001` (AppDirectory=`api/`) | `sdl2-pc-03-cytation.<tailnet>` |
| `bmg_platereader` (TBD)   | `platereader`  | 8001 | `run platereader-serve`                   | `platereader-pc.<tailnet>` |
| `agilent_platestacker` (TBD) | `platestacker` | 8002 | `run platestacker-serve`               | `platestacker-pc.<tailnet>` |
| `fume_hood_actuator`      | `fume-hood`    | 5000 | —                                         | `fume-hood-pc.<tailnet>` |
| `filter_every_well`       | `press`        | 8000 | —                                         | `press-pc.<tailnet>` |
| `dose_every_well`         | `solid-doser`  | 8000 | —                                         | `solid-doser-pc.<tailnet>` |

> **Windows quirk:** after every `nssm start <svc>`, run `sc resume <svc>` to clear
> the unexpected `SERVICE_PAUSED` state. This is an NSSM quirk; the service otherwise works correctly.

When multiple services share one PC, every service gets a distinct port; when each service has its own PC, the same port is fine across PCs (Tailscale's hostname is what disambiguates).

After install + smoke, register the service in the monorepo's `equipment.yaml` with the matching `id`, `base_url`, and `protocol` fields. See [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) for the registry shape (v1.0 and v1.1).

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `sc query <svc>` says `STOPPED` immediately after `nssm start` | wrong `AppDirectory` or `.venv` not synced | Run `C:\SDL_Tools\uv.exe sync --extra api` from the repo dir; check `<svc>.err.log`. |
| `/control/startup` returns 503 with "profile not found" | service is running as `LocalSystem` | Reset `ObjectName` to `.\labuser` (see §5). |
| `/control/seal/start` returns 423 | strict claim enforcement, no `X-Claim-Token` | The SDK's `ClaimManager` handles this automatically. For manual `curl` debugging, acquire a claim first via `POST /control/claim`. |
| Logs grow without bound | `AppRotateBytes` not set | `nssm set <svc> AppRotateFiles 1; nssm set <svc> AppRotateBytes 10485760`. |
| Service starts but `curl http://127.0.0.1:<port>/` hangs | Windows Defender Firewall blocking loopback (rare) | `New-NetFirewallRule -DisplayName "<svc>" -Direction Inbound -Action Allow -LocalPort <port> -Protocol TCP`. |
| `uv run` reports "no project found" under NSSM | `--project <path>` not specified | NSSM does not change directory unless `AppDirectory` is set; either set it (preferred) or always pass `--project`. |

## 9. Uninstall

```powershell
nssm stop <svc>
nssm remove <svc> confirm
Remove-Item -Recurse -Force C:\Users\sdl2\Projects\<repo>
```

The lab user account, uv binary, and NSSM stay; uninstalling one device does not affect the others.

## See also

- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) — combined v1.0 + v1.1 contract every device REST API implements (claim protocol, `allowed_actions`, SiLA comparison appendix).
- [`docs/ROADMAP.md`](ROADMAP.md) — per-device migration status.
- [`docs/EQUIPMENT_INTEGRATION.md`](EQUIPMENT_INTEGRATION.md) — registry / maintenance runbook on the dashboard side.
- `equipment.yaml` — the monorepo's source of truth for "which devices exist and where to reach them".
