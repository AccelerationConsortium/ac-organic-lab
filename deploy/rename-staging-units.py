#!/usr/bin/env python3
"""Give the migration-era `*-staging*` units their permanent names and make the
git checkout's registry the only equipment.yaml the lab reads.

Default: read-only preflight. `--apply`: cut over. `--rollback`: restore the
archived originals from the state file.

What changes
------------
* Each staging unit and its drop-in directory is flattened into ONE new unit
  file under its permanent name (the name each product's own `deploy/` folder
  uses). Every fragment is copied verbatim, in systemd's own load order, so the
  merged semantics are identical — except for the lines this script pins on
  purpose (see PINNED below), which are removed from the fragments and from the
  environment files that hid them, and re-stated explicitly in the new unit.
* The dashboard API and the auth sidecar read `equipment.yaml`,
  `platforms.yaml` and `locations.yaml` straight from the ac-organic-lab
  checkout (the documented single source of truth). The copies under
  /data/shared/final-sync, /data/dashboard/{config,bootstrap},
  /data/auth/runtime and /etc/device-gateway-staging are archived once nothing
  references them. The roster (not in git) stays where it is.
* Other units that name a renamed unit (`lab-history-snapshot.service`,
  `ac-auth-login-test.service`) and the one-off installers under
  ~/caoyang/ops are rewritten in place, with the originals archived.
* Old unit files and drop-in directories move to the archive; nothing is
  deleted.

What does NOT change: ports, users, sandboxing, environment-file paths
(e.g. /etc/dashboard-integrations/ac-dashboard-staging-api.service.*.env keep
their names — they are opaque paths), data under /data, the Caddy binary and
Caddyfile paths, and anything in another git repository. PyPoe's monitor
config names the API unit and must be updated separately (reported).

Expect ~10-30 s of downtime for every renamed service while systemd stops the
old set and starts the new one. This script issues no physical equipment
controls.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path('/home/sdl2/caoyang')
REPO = ROOT / 'ac-organic-lab'
OPS = ROOT / 'ops'
UNIT_DIR = Path('/etc/systemd/system')
ARCHIVE_ROOT = ROOT / 'archive'
STATE = ARCHIVE_ROOT / 'unit-rename-state.json'

# old stem -> (new stem, Description)
RENAMES = {
    'ac-dashboard-staging-api': ('ac-organic-lab-api', 'AC Organic Lab dashboard API (FastAPI aggregator)'),
    'ac-dashboard-staging-web': ('ac-organic-lab-web', 'AC Organic Lab dashboard web (Next.js standalone)'),
    'ac-auth-staging': ('ac-organic-lab-auth', 'AC Organic Lab auth sidecar (email-code login)'),
    'dashboard-staging-edge': ('dashboard-edge', 'Lab SSO edge (Caddy: dashboard, Bitacora, BitacoraDB, Kuma, auth)'),
    'bitacora-staging-api': ('bitacora', 'Bitacora - agentic ELN API'),
    'bitacora-staging-web': ('bitacora-frontend', 'Bitacora frontend (Next.js)'),
    'bitacora-beta-staging-api': ('bitacora-beta', 'Bitacora BETA - agentic ELN API (private beta)'),
    'bitacora-beta-staging-web': ('bitacora-beta-frontend', 'Bitacora BETA frontend (Next.js)'),
    'bitacoradb-staging-api': ('bitacoradb', 'BitacoraDB - ELN+LIMS record layer (HTTP API)'),
    'bitacoradb-staging-preview': ('bitacoradb-preview', 'BitacoraDB preview/byte service (read-only)'),
    'bitacoradb-beta-staging-api': ('bitacoradb-beta', 'BitacoraDB BETA - record layer (HTTP API)'),
    'bitacoradb-beta-staging-preview': ('bitacoradb-beta-preview', 'BitacoraDB BETA preview/byte service (read-only)'),
    'go2rtc-staging': ('ac-go2rtc', 'AC Organic Lab - go2rtc RTSP/MSE/WebRTC bridge for Tapo cameras'),
    'kasa-tapo-staging': ('kasa-tapo-services', 'AC Organic Lab - Kasa + Tapo gateway service'),
    'bambu-staging': ('bambu-server', 'AC Bambu printer monitoring gateway'),
}

# Variables whose value the new unit states explicitly. `None` means "keep the
# value the running service has right now" (read from /proc, so the cutover
# cannot silently change a data path); a string is the new value.
PINNED = {
    'ac-dashboard-staging-api': {
        'LAB_REGISTRY_PATH': str(REPO / 'equipment.yaml'),
        'LAB_PLATFORMS_PATH': str(REPO / 'platforms.yaml'),
        'LAB_LOCATIONS_PATH': str(REPO / 'locations.yaml'),
        'LAB_DB_PATH': None,
        'ASSISTANT_DB_PATH': None,
    },
    'ac-auth-staging': {
        'AUTH_PLATFORMS_PATH': str(REPO / 'platforms.yaml'),
        'AUTH_ROSTER_PATH': None,
        'AUTH_DB_PATH': None,
    },
}
# ExecStartPre lines are replaced wholesale for these units.
EXEC_START_PRE = {
    'ac-auth-staging': [
        (f'{REPO}/.venv/bin/python -m ac_auth.cli validate '
         f'--equipment {REPO}/equipment.yaml --platforms {REPO}/platforms.yaml'),
    ],
}

# Registry / config copies that become dead once the pins above are live.
RETIRE_COPIES = [
    Path('/data/shared/final-sync/equipment.yaml'),
    Path('/data/shared/final-sync/equipment.yaml.bak.20260918_180937'),
    Path('/data/dashboard/config/equipment.yaml'),
    Path('/data/dashboard/config/platforms.yaml'),
    Path('/data/dashboard/config/locations.yaml'),
    Path('/data/dashboard/bootstrap/equipment.yaml'),
    Path('/data/auth/runtime/equipment.yaml'),
    Path('/data/auth/runtime/platforms.yaml'),
    Path('/data/auth/config/platforms.yaml'),
    Path('/etc/device-gateway-staging/equipment.yaml'),
]

# Files outside the renamed set that name an old unit and are safe to rewrite
# (host tooling, not another git repository).
REFERRER_FILES = [
    UNIT_DIR / 'lab-history-snapshot.service',
    UNIT_DIR / 'ac-auth-login-test.service',
    OPS / 'installers/check-auth-network.py',
    OPS / 'installers/install-dashboard-voice.py',
    OPS / 'installers/install-auth-smtp-relay.py',
    OPS / 'installers/install-bitacora-llm.py',
    OPS / 'installers/install-model-integrations.py',
    OPS / 'installers/edge-secrets-migration/2-install-here.sh',
]
# Named an old unit, but owned elsewhere: report, never touch.
REPORT_ONLY = [
    ROOT / 'pypoe/src/pypoe/config/slack.yaml',
    Path('/home/sdl2/.config/systemd/user/hermes-dashboard.service'),
    OPS / 'records/KUMA-MIGRATION-RUNBOOK-2026-09-18.md',
    OPS / 'maintenance/README.md',
]
REFERRER_SCAN_DIRS = [Path('/etc/sudoers.d'), Path('/etc/polkit-1/rules.d'), Path('/usr/local/bin'),
                      Path('/usr/local/sbin'), Path('/etc/cron.d'), UNIT_DIR]

# Loopback/tailnet listeners whose "answers at all" state must be the same
# before and after. Any HTTP status counts as answering.
PROBES = {
    'api': 'http://127.0.0.1:8001/api/health',
    'web': 'http://127.0.0.1:8000/',
    'auth': 'http://127.0.0.1:8009/auth/me',
    'edge': 'http://100.64.254.6/',
    'kuma-edge': 'http://100.64.254.6:8005/',
    'kasa-tapo': 'http://127.0.0.1:8002/health',
    'bambu': 'http://127.0.0.1:8012/health',
    'go2rtc': 'http://127.0.0.1:1984/api',
    'bitacora': 'http://127.0.0.1:8050/',
    'bitacora-frontend': 'http://127.0.0.1:3001/',
    'bitacoradb': 'http://127.0.0.1:8013/',
    'bitacoradb-preview': 'http://127.0.0.1:8014/',
    'bitacora-beta': 'http://127.0.0.1:18050/',
    'bitacora-beta-frontend': 'http://127.0.0.1:13001/',
    'bitacoradb-beta': 'http://127.0.0.1:18013/',
    'bitacoradb-beta-preview': 'http://127.0.0.1:18014/',
}

OLD_UNIT_RE = re.compile(r'\b(' + '|'.join(re.escape(k) for k in RENAMES) + r')\.service\b')


class Refusal(Exception):
    pass


def run(*args, check=True, timeout=120):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise Refusal(f'{" ".join(args)} -> rc={proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}')
    return proc


def show(unit, prop):
    return run('systemctl', 'show', unit, '-p', prop, '--value').stdout.strip()


def probe(url, timeout=4):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='GET'), timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:  # connection refused / timeout
        return f'NO-ANSWER ({type(e).__name__})'


def probe_all():
    return {k: probe(u) for k, u in PROBES.items()}


def proc_env(unit):
    pid = show(f'{unit}.service', 'MainPID')
    if not pid or pid == '0':
        raise Refusal(f'{unit}.service has no main PID; it must be running for the cutover')
    raw = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    return dict(item.decode(errors='replace').split('=', 1) for item in raw if b'=' in item)


def env_var_name(line):
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        return None
    return s.removeprefix('export ').split('=', 1)[0].strip()


def parse_env_file(path):
    """Return the variable names a systemd EnvironmentFile defines."""
    return [n for n in (env_var_name(l) for l in path.read_text().splitlines()) if n]


def fragments(old):
    """Unit file + drop-ins, in systemd's load order (only /etc/systemd/system is allowed)."""
    unit = f'{old}.service'
    base = UNIT_DIR / unit
    if not base.is_file():
        raise Refusal(f'{base} missing')
    drop = [Path(p) for p in show(unit, 'DropInPaths').split() if p]
    for p in drop:
        if p.parent != UNIT_DIR / f'{unit}.d':
            raise Refusal(f'{unit}: drop-in outside /etc/systemd/system: {p}')
    fragment_path = show(unit, 'FragmentPath')
    if fragment_path != str(base):
        raise Refusal(f'{unit}: FragmentPath is {fragment_path}, expected {base}')
    return [base] + sorted(drop, key=lambda p: p.name)


def env_files_of(old):
    out = []
    for line in run('systemctl', 'show', f'{old}.service', '-p', 'EnvironmentFiles').stdout.splitlines():
        if line.startswith('EnvironmentFiles='):
            value = line.split('=', 1)[1].strip()
            if value:
                out.append(Path(value.split()[0]))
    return out


def render(old, pins_resolved, now_iso):
    new, desc = RENAMES[old]
    pinned_names = set(pins_resolved.get(old, {}))
    out = [
        f'# {new}.service — permanent name of the former {old}.service.',
        f'# Generated {now_iso} by ac-organic-lab/deploy/rename-staging-units.py from the',
        '# unit file and every drop-in below, copied verbatim in systemd load order.',
        '# Pinned explicitly at the end (removed from the fragments and their',
        f'# environment files): {", ".join(sorted(pinned_names)) or "nothing"}.',
        '',
    ]
    for frag in fragments(old):
        out.append(f'# ---- from {frag}')
        for line in frag.read_text().splitlines():
            s = line.strip()
            if s.startswith('Description='):
                continue
            if s.startswith('Environment=') and any(name in s for name in pinned_names):
                # Only the simple one-assignment form is understood; a quoted
                # multi-assignment line naming a pinned variable is refused
                # rather than half-rewritten.
                if s.split('=', 2)[1] not in pinned_names or '"' in s or ' ' in s.split('=', 2)[2].strip():
                    raise Refusal(f'{frag}: cannot pin over a compound Environment= line: {line!r}')
                out.append(f'# (superseded by the pin below) {line}')
                continue
            if old in EXEC_START_PRE and s.startswith('ExecStartPre='):
                out.append(f'# (replaced by the pin below) {line}')
                continue
            out.append(OLD_UNIT_RE.sub(lambda m: RENAMES[m.group(1)][0] + '.service', line))
        out.append('')
    out += ['[Unit]', f'Description={desc}', '']
    if pinned_names or old in EXEC_START_PRE:
        out.append('[Service]')
        for k in sorted(pinned_names):
            out.append(f'Environment={k}={pins_resolved[old][k]}')
        if old in EXEC_START_PRE:
            out.append('ExecStartPre=')
            out += [f'ExecStartPre={cmd}' for cmd in EXEC_START_PRE[old]]
        out.append('')
    return '\n'.join(out)


def resolve_pins():
    resolved = {}
    for old, pins in PINNED.items():
        env = proc_env(old)
        resolved[old] = {}
        for k, v in pins.items():
            if v is None:
                if k not in env:
                    raise Refusal(f'{old}: {k} is pinned to its current value but the running process has no {k}')
                v = env[k]
            resolved[old][k] = v
    return resolved


def scan_referrers():
    hits = {}
    renamed_dirs = {f'{o}.service.d' for o in RENAMES}
    for d in REFERRER_SCAN_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob('*')):
            # Symlinks (multi-user.target.wants/*) point at files scanned on their own.
            if p.is_symlink() or not p.is_file() or p.stem in RENAMES or p.parent.name in renamed_dirs:
                continue
            try:
                txt = p.read_text(errors='replace')
            except OSError:
                continue
            found = sorted(set(OLD_UNIT_RE.findall(txt)))
            if found:
                hits[str(p)] = found
    for p in REFERRER_FILES + REPORT_ONLY:
        if p.is_file():
            found = sorted(set(OLD_UNIT_RE.findall(p.read_text(errors='replace'))))
            if found:
                hits[str(p)] = found
    return hits


def load_yaml():
    for cand in sorted((REPO / '.venv/lib').glob('python*/site-packages')):
        sys.path.insert(0, str(cand))
    import yaml  # from the repo venv
    return yaml


def registry_check():
    """The checkout registry must describe the same lab the live registry does."""
    yaml = load_yaml()
    live_path = Path(proc_env('ac-dashboard-staging-api')['LAB_REGISTRY_PATH'])
    repo_ids = {e['id']: e for e in yaml.safe_load((REPO / 'equipment.yaml').read_text())['equipment']}
    live_ids = {e['id']: e for e in yaml.safe_load(live_path.read_text())['equipment']}
    if set(repo_ids) != set(live_ids):
        raise Refusal(f'registry id sets differ: only-in-repo={sorted(set(repo_ids) - set(live_ids))} '
                      f'only-in-live={sorted(set(live_ids) - set(repo_ids))}')
    diffs = {}
    for i, repo_entry in repo_ids.items():
        live_entry = live_ids[i]
        if repo_entry != live_entry:
            keys = sorted(k for k in set(repo_entry) | set(live_entry) if repo_entry.get(k) != live_entry.get(k))
            diffs[i] = {k: {'repo': repo_entry.get(k), 'live': live_entry.get(k)} for k in keys}
    same_cfg = {}
    for name in ('platforms.yaml', 'locations.yaml'):
        for copy in (Path('/data/dashboard/config') / name, Path('/data/auth/config') / name):
            if copy.is_file():
                same_cfg[str(copy)] = copy.read_bytes() == (REPO / name).read_bytes()
    git = run('git', '-C', str(REPO), 'status', '--porcelain', '--branch', '--',
              'equipment.yaml', 'platforms.yaml', 'locations.yaml', check=False).stdout.strip()
    return {'live_registry': str(live_path), 'devices': len(repo_ids), 'field_diffs_repo_vs_live': diffs,
            'config_copies_identical_to_repo': same_cfg, 'checkout_git_status': git}


def unit_state(stem):
    return {'active': show(f'{stem}.service', 'ActiveState'), 'enabled': show(f'{stem}.service', 'UnitFileState')}


def preflight(retire_login_test):
    if os.geteuid() != 0:
        raise Refusal('run with sudo (unit files and several drop-ins are root-only)')
    if STATE.exists():
        raise Refusal(f'{STATE} exists — a previous apply is recorded; use --rollback or move it aside')
    report = {'time': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'units': {},
              'probes_before': probe_all()}
    for old, (new, _) in RENAMES.items():
        st = unit_state(old)
        if st['active'] != 'active':
            raise Refusal(f'{old}.service is {st["active"]}; every renamed unit must be running')
        if (UNIT_DIR / f'{new}.service').exists() or (UNIT_DIR / f'{new}.service.d').exists():
            raise Refusal(f'{new}.service already exists')
        if run('systemctl', 'cat', f'{new}.service', check=False).returncode == 0:
            raise Refusal(f'systemd already knows a unit named {new}.service')
        frags = fragments(old)
        report['units'][old] = {'new': new, 'fragments': [str(f) for f in frags], **st,
                                'env_files': [str(p) for p in env_files_of(old)]}
    pins = resolve_pins()
    report['pins'] = pins
    # Which environment files currently define a pinned variable (they get the key removed).
    report['env_file_edits'] = {}
    for old, kv in pins.items():
        for ef in env_files_of(old):
            if not ef.is_file():
                raise Refusal(f'{old}: EnvironmentFile {ef} missing')
            defined = [k for k in parse_env_file(ef) if k in kv]
            if defined:
                report['env_file_edits'][str(ef)] = defined
    report['registry'] = registry_check()
    report['referrers'] = scan_referrers()
    unexpected = {p: v for p, v in report['referrers'].items()
                  if Path(p) not in REFERRER_FILES and Path(p) not in REPORT_ONLY}
    if unexpected:
        raise Refusal('unexpected files name a staging unit; review before renaming: '
                      + json.dumps(unexpected, indent=1))
    report['no_answer_before'] = [k for k, v in report['probes_before'].items() if not isinstance(v, int)]
    report['login_test'] = {'retire': retire_login_test, **unit_state('ac-auth-login-test')}
    report['retire_copies_present'] = [str(p) for p in RETIRE_COPIES if p.exists()]
    report['rendered'] = {old: render(old, pins, report['time']) for old in RENAMES}
    return report


def print_report(report):
    print(f'== preflight {report["time"]}')
    print('-- units (old -> new; fragments; env files)')
    for old, u in report['units'].items():
        print(f'  {old}.service -> {u["new"]}.service   [{u["active"]}/{u["enabled"]}]  '
              f'{len(u["fragments"])} fragment(s), {len(u["env_files"])} env file(s)')
    print('-- pinned variables (explicit in the new unit files)')
    for old, kv in report['pins'].items():
        for k, v in kv.items():
            print(f'  {RENAMES[old][0]}: {k}={v}')
    print('-- environment files that will lose those keys (backup archived)')
    for p, keys in (report['env_file_edits'] or {'(none)': []}).items():
        print(f'  {p}: {", ".join(keys)}')
    r = report['registry']
    print(f'-- registry: live={r["live_registry"]} devices={r["devices"]}')
    print(f'   field differences repo vs live: {json.dumps(r["field_diffs_repo_vs_live"]) or "none"}')
    for p, same in r['config_copies_identical_to_repo'].items():
        print(f'   {p}: {"identical to repo" if same else "DIFFERS from repo"}')
    print('   checkout (the API and auth will read these files as checked out, branch included):')
    for line in r['checkout_git_status'].splitlines() or ['   (git status unavailable)']:
        print(f'     {line}')
    print('-- files naming an old unit')
    for p, names in report['referrers'].items():
        tag = 'rewrite' if Path(p) in REFERRER_FILES else 'REPORT ONLY (owned elsewhere)'
        print(f'  [{tag}] {p}: {", ".join(names)}')
    print(f'-- probes before: {json.dumps(report["probes_before"])}')
    if report['no_answer_before']:
        print(f'   NOTE: not answering before the cutover (will not be required after): {report["no_answer_before"]}')
    print(f'-- ac-auth-login-test.service: {report["login_test"]}')
    print(f'-- registry/config copies to archive once unreferenced: {len(report["retire_copies_present"])}')
    print('-- rendered unit files: pass --show <old-stem> to print one')


def archive_dir(ts):
    d = ARCHIVE_ROOT / f'unit-rename-{ts}'
    d.mkdir(parents=True, exist_ok=False)
    return d


def archive_copy(src, arch):
    dest = arch / 'originals' / src.relative_to('/')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest)
    return dest


def strip_keys_from_env_file(path, keys):
    kept, dropped = [], []
    for line in path.read_text().splitlines():
        (dropped if env_var_name(line) in keys else kept).append(line)
    path.write_text('\n'.join(kept) + ('\n' if kept else ''))
    return dropped


def grep_paths_referenced(paths):
    """Which of `paths` are still named by any unit fragment or environment file."""
    texts = {}
    for p in UNIT_DIR.rglob('*'):
        if p.is_file():
            try:
                texts[str(p)] = p.read_text(errors='replace')
            except OSError:
                pass
    listing = run('systemctl', 'list-units', '--type=service', '--all', '--no-legend', '--plain').stdout
    for line in listing.splitlines():
        stem = line.split()[0] if line.strip() else ''
        if not stem.endswith('.service'):
            continue
        for l in run('systemctl', 'show', stem, '-p', 'EnvironmentFiles', check=False).stdout.splitlines():
            value = l.split('=', 1)[1].strip() if '=' in l else ''
            ef = value.split()[0] if value else ''
            if ef and Path(ef).is_file() and ef not in texts:
                try:
                    texts[ef] = Path(ef).read_text(errors='replace')
                except OSError:
                    pass
    def live_lines(text):
        return '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))

    still = {}
    for target in paths:
        refs = [f for f, t in texts.items() if str(target) in live_lines(t)]
        if refs:
            still[str(target)] = refs
    return still


def tidy():
    """After a completed apply: archive registry copies nothing references
    (comments do not count) and old-named environment-file duplicates whose
    content equals the new-named file the units now read."""
    if os.geteuid() != 0:
        raise Refusal('run with sudo')
    state = json.loads(STATE.read_text())
    if state.get('cutover') != 'complete':
        raise Refusal(f'cutover state is {state.get("cutover")!r}; --tidy runs after a complete apply')
    arch = Path(state['archive'])

    def save():
        STATE.write_text(json.dumps(state, indent=2))

    present = [p for p in RETIRE_COPIES if p.exists()]
    still = grep_paths_referenced(present)
    for p in present:
        if str(p) in still:
            print(f'kept (still referenced): {p} <- {still[str(p)]}')
            continue
        dest = arch / 'registry-copies' / p.relative_to('/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
        state['moved'].append([str(p), str(dest)])
        print(f'archived {p}')
    state['copies_kept'] = still
    # Old-named env files left beside their renamed twins (e.g. copied by hand).
    for old, (new, _) in RENAMES.items():
        for p in sorted(Path('/etc/dashboard-integrations').glob(f'{old}.service.*.env')):
            twin = p.with_name(p.name.replace(f'{old}.service', f'{new}.service'))
            if not twin.exists():
                print(f'kept {p}: no {twin.name} beside it')
                continue
            if p.read_bytes() != twin.read_bytes():
                print(f'kept {p}: content differs from {twin.name}; compare by hand')
                continue
            dest = arch / 'originals' / p.relative_to('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            state['moved'].append([str(p), str(dest)])
            print(f'archived duplicate {p}')
    save()


def apply(report, retire_login_test):
    ts = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    arch = archive_dir(ts)
    state = {'time': report['time'], 'archive': str(arch), 'renames': {o: n for o, (n, _) in RENAMES.items()},
             'written': [], 'env_file_edits': {}, 'referrers_rewritten': [], 'moved': [], 'login_test_retired': False}
    (arch / 'preflight.json').write_text(json.dumps({k: v for k, v in report.items() if k != 'rendered'}, indent=2))

    def save():
        STATE.write_text(json.dumps(state, indent=2))

    # 1. Write the new unit files (no behaviour change yet).
    for old, (new, _) in RENAMES.items():
        dest = UNIT_DIR / f'{new}.service'
        dest.write_text(report['rendered'][old])
        dest.chmod(0o644)
        state['written'].append(str(dest))
    save()
    run('systemctl', 'daemon-reload')
    for old, (new, _) in RENAMES.items():
        v = run('systemd-analyze', 'verify', str(UNIT_DIR / f'{new}.service'), check=False)
        if v.returncode != 0:
            raise Refusal(f'systemd-analyze verify {new}.service failed:\n{v.stderr}{v.stdout}')

    # 2. Pinned keys leave the environment files (originals archived first).
    for ef, keys in report['env_file_edits'].items():
        archive_copy(Path(ef), arch)
        dropped = strip_keys_from_env_file(Path(ef), set(keys))
        state['env_file_edits'][ef] = dropped
    save()

    # 3. Referrers (host tooling only).
    for p in REFERRER_FILES:
        if p.is_file() and OLD_UNIT_RE.search(p.read_text(errors='replace')):
            archive_copy(p, arch)
            p.write_text(OLD_UNIT_RE.sub(lambda m: RENAMES[m.group(1)][0] + '.service', p.read_text()))
            state['referrers_rewritten'].append(str(p))
    save()
    run('systemctl', 'daemon-reload')

    # 4. Environment files whose NAME embeds an old unit name follow the rename
    #    (the unit-name substitution above already rewrote the paths).
    rename_embedded_env_files(state)
    save()

    # 5. Cut over: stop + disable the old set, enable + start the new set.
    olds = [f'{o}.service' for o in RENAMES]
    news = [f'{n}.service' for n, _ in RENAMES.values()]
    print(f'stopping {len(olds)} old units …', flush=True)
    run('systemctl', 'stop', *olds, timeout=300)
    run('systemctl', 'disable', *olds)
    state['cutover'] = 'old-stopped'
    save()
    print(f'starting {len(news)} new units …', flush=True)
    run('systemctl', 'enable', '--now', *news, timeout=300)
    state['cutover'] = 'new-started'
    save()
    verify_and_archive(state, arch, report['probes_before'], retire_login_test, save)


def rename_embedded_env_files(state):
    """mv old-named EnvironmentFiles to the new-named paths the new units cite."""
    for old, (new, _) in RENAMES.items():
        unit_file = UNIT_DIR / f'{new}.service'
        for line in unit_file.read_text().splitlines():
            if not line.startswith('EnvironmentFile='):
                continue
            p = Path(line.split('=', 1)[1].strip().lstrip('-').split()[0])
            if p.exists() or f'{new}.service' not in p.name:
                continue
            src = p.with_name(p.name.replace(f'{new}.service', f'{old}.service'))
            if not src.exists():
                raise Refusal(f'{new}: EnvironmentFile {p} missing and no {src} to rename')
            shutil.move(str(src), str(p))
            state.setdefault('moved', []).append([str(src), str(p)])
            print(f'renamed env file {src} -> {p}')


def verify_and_archive(state, arch, probes_before, retire_login_test, save):
    news = [f'{n}.service' for n, _ in RENAMES.values()]
    # Verify: every new unit active, every listener that answered before answers now.
    deadline = time.time() + 90
    while True:
        states = {n: show(n, 'ActiveState') for n in news}
        probes = probe_all()
        bad_units = [n for n, s in states.items() if s != 'active']
        bad_probes = [k for k, v in probes.items()
                      if isinstance(probes_before[k], int) and not isinstance(v, int)]
        if not bad_units and not bad_probes:
            break
        if time.time() > deadline:
            state['verify_failed'] = {'units': states, 'probes': probes}
            save()
            raise Refusal(f'cutover verification failed after 90 s: units not active={bad_units}, '
                          f'listeners not answering={bad_probes}. Old units are stopped+disabled but their files are '
                          f'still in place: fix and `sudo {sys.argv[0]} --finish`, or '
                          f'`sudo {sys.argv[0]} --rollback` restores them.')
        time.sleep(3)
    state['probes_after'] = probes
    print(f'probes after: {json.dumps(probes)}')
    try:
        with urllib.request.urlopen(PROBES['api'], timeout=5) as r:
            health = json.load(r)
        print(f'api health: {health}')
        state['api_health_after'] = health
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        print(f'api health read failed: {e}')

    # Old unit files and drop-ins move to the archive.
    (arch / 'units').mkdir(exist_ok=True)
    for old in RENAMES:
        for p in (UNIT_DIR / f'{old}.service', UNIT_DIR / f'{old}.service.d'):
            if p.exists():
                dest = arch / 'units' / p.name
                shutil.move(str(p), str(dest))
                state['moved'].append([str(p), str(dest)])
    if retire_login_test:
        run('systemctl', 'disable', '--now', 'ac-auth-login-test.service')
        p = UNIT_DIR / 'ac-auth-login-test.service'
        dest = arch / 'units' / p.name
        shutil.move(str(p), str(dest))
        state['moved'].append([str(p), str(dest)])
        state['login_test_retired'] = True
    save()
    run('systemctl', 'daemon-reload')
    run('systemctl', 'reset-failed', check=False)

    # Registry/config copies: archive the ones nothing references any more.
    present = [p for p in RETIRE_COPIES if p.exists()]
    still = grep_paths_referenced(present)
    for p in present:
        if str(p) in still:
            print(f'kept (still referenced): {p} <- {still[str(p)]}')
            continue
        dest = arch / 'registry-copies' / p.relative_to('/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
        state['moved'].append([str(p), str(dest)])
    state['copies_kept'] = still
    state['cutover'] = 'complete'
    save()
    print(f'\nDone. Archive: {arch}\nState: {STATE}')
    print('Follow-ups this script does not do (other owners):')
    for p in REPORT_ONLY:
        if p.is_file() and OLD_UNIT_RE.search(p.read_text(errors='replace')):
            print(f'  - {p} still names an old unit')


def finish(retire_login_test):
    """Resume an apply that stopped after the new units were enabled (e.g. one
    failed to start): repair renamed environment files, start everything under
    the new names, then verify and archive exactly as --apply would have."""
    if os.geteuid() != 0:
        raise Refusal('run with sudo')
    if not STATE.exists():
        raise Refusal(f'{STATE} missing; nothing to finish')
    state = json.loads(STATE.read_text())
    if state.get('cutover') not in ('old-stopped', 'new-started'):
        raise Refusal(f'cutover state is {state.get("cutover")!r}; --finish only resumes a half-done apply')
    arch = Path(state['archive'])
    probes_before = json.loads((arch / 'preflight.json').read_text())['probes_before']

    def save():
        STATE.write_text(json.dumps(state, indent=2))

    rename_embedded_env_files(state)
    save()
    run('systemctl', 'daemon-reload')
    news = [f'{n}.service' for n in state['renames'].values()]
    run('systemctl', 'reset-failed', *news, check=False)
    print(f'starting {len(news)} units …', flush=True)
    run('systemctl', 'enable', '--now', *news, timeout=300)
    state['cutover'] = 'new-started'
    save()
    verify_and_archive(state, arch, probes_before, retire_login_test, save)


def retire_login_test_only():
    """Standalone: disable and archive ac-auth-login-test.service after a complete
    apply. /etc/auth-staging-login-test/ stays: the auth unit reads mail.env from it."""
    if os.geteuid() != 0:
        raise Refusal('run with sudo')
    state = json.loads(STATE.read_text())
    if state.get('cutover') != 'complete':
        raise Refusal(f'cutover state is {state.get("cutover")!r}; retire the login test after a complete apply')
    unit = UNIT_DIR / 'ac-auth-login-test.service'
    if not unit.exists():
        raise Refusal(f'{unit} already gone')
    arch = Path(state['archive'])
    run('systemctl', 'disable', '--now', unit.name)
    (arch / 'units').mkdir(exist_ok=True)
    dest = arch / 'units' / unit.name
    shutil.move(str(unit), str(dest))
    state['moved'].append([str(unit), str(dest)])
    state['login_test_retired'] = True
    STATE.write_text(json.dumps(state, indent=2))
    run('systemctl', 'daemon-reload')
    print(f'retired {unit.name}: {show(unit.name, "LoadState")}/{show(unit.name, "ActiveState")}; '
          f'archived at {dest}; :8081 -> {probe("http://127.0.0.1:8081/")}')


AUTH_UNIT = UNIT_DIR / 'ac-organic-lab-auth.service'
OLD_MAIL_ENV = '/etc/auth-staging-login-test/mail.env'
NEW_MAIL_ENV = Path('/etc/dashboard-integrations/ac-organic-lab-auth.service.mail.env')


def repair_auth_mail_env():
    """The auth unit's mail settings lived in /etc/auth-staging-login-test/mail.env,
    a directory that was removed together with the login-test unit. The running
    auth process still carries those variables, so rebuild the file from its
    environment (root reads /proc, nothing is printed) and point the unit at a
    path that belongs to auth. Without this the next restart or reboot fails
    with "Failed to load environment files"."""
    if os.geteuid() != 0:
        raise Refusal('run with sudo')
    text = AUTH_UNIT.read_text()
    if OLD_MAIL_ENV not in text:
        raise Refusal(f'{AUTH_UNIT} does not reference {OLD_MAIL_ENV}; nothing to repair')
    if Path(OLD_MAIL_ENV).exists():
        raise Refusal(f'{OLD_MAIL_ENV} exists; the unit is loadable as is')
    env = proc_env('ac-organic-lab-auth')
    keys = sorted(k for k in env if k.startswith('AUTH_SMTP_'))
    if not keys:
        raise Refusal('running auth process has no AUTH_SMTP_* variables to recover')
    if NEW_MAIL_ENV.exists():
        raise Refusal(f'{NEW_MAIL_ENV} already exists; refusing to overwrite')
    state = json.loads(STATE.read_text())
    arch = Path(state['archive'])
    archive_copy(AUTH_UNIT, arch)
    NEW_MAIL_ENV.write_text(''.join(f'{k}={env[k]}\n' for k in keys))
    NEW_MAIL_ENV.chmod(0o600)
    AUTH_UNIT.write_text(text.replace(f'EnvironmentFile={OLD_MAIL_ENV}', f'EnvironmentFile={NEW_MAIL_ENV}'))
    run('systemctl', 'daemon-reload')
    v = run('systemd-analyze', 'verify', str(AUTH_UNIT), check=False)
    if v.returncode != 0:
        raise Refusal(f'systemd-analyze verify failed:\n{v.stderr}{v.stdout}')
    state.setdefault('repairs', []).append({'auth_mail_env': str(NEW_MAIL_ENV), 'keys': keys})
    STATE.write_text(json.dumps(state, indent=2))
    print(f'wrote {NEW_MAIL_ENV} (0600, {len(keys)} keys: {", ".join(keys)}) and repointed {AUTH_UNIT.name}.')
    print('Optional proof: sudo systemctl restart ac-organic-lab-auth.service  (a few seconds of 401s; sessions persist).')


def rollback():
    if os.geteuid() != 0:
        raise Refusal('run with sudo')
    state = json.loads(STATE.read_text())
    arch = Path(state['archive'])
    news = [f'{n}.service' for n in state['renames'].values()]
    olds = [f'{o}.service' for o in state['renames']]
    # Put moved files back, restore edited originals, swap the enabled set.
    for src, dest in reversed(state.get('moved', [])):
        if Path(dest).exists() and not Path(src).exists():
            Path(src).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(dest, src)
    for edited in list(state.get('env_file_edits', {})) + state.get('referrers_rewritten', []):
        backup = arch / 'originals' / Path(edited).relative_to('/')
        if backup.exists():
            shutil.copy2(backup, edited)
    run('systemctl', 'daemon-reload')
    if state.get('cutover'):
        run('systemctl', 'stop', *news, check=False, timeout=300)
        run('systemctl', 'disable', *news, check=False)
        run('systemctl', 'enable', '--now', *olds, timeout=300)
        if state.get('login_test_retired'):
            run('systemctl', 'enable', '--now', 'ac-auth-login-test.service', check=False)
    for w in state.get('written', []):
        Path(w).unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    STATE.rename(arch / 'state.rolled-back.json')
    print('rolled back; old units enabled and started, new unit files removed. probes:', json.dumps(probe_all()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='perform the cutover (default: preflight only)')
    mode.add_argument('--rollback', action='store_true', help='undo a recorded apply')
    mode.add_argument('--finish', action='store_true',
                      help='resume an apply that stopped after enabling the new units: repair, start, verify, archive')
    mode.add_argument('--tidy', action='store_true',
                      help='after a complete apply: archive unreferenced registry copies and duplicate old-named env files')
    mode.add_argument('--repair-auth-mail-env', action='store_true',
                      help='rebuild the auth mail env file from the running process if /etc/auth-staging-login-test was removed')
    mode.add_argument('--show', metavar='OLD_STEM', help='print the rendered new unit file for one old unit and exit')
    ap.add_argument('--retire-login-test', action='store_true',
                    help='disable and archive ac-auth-login-test.service (the :8081 loopback login test page); '
                         'combined with --apply/--finish it happens during the cutover, alone it runs after one')
    args = ap.parse_args()
    try:
        if args.rollback:
            return rollback()
        if args.retire_login_test and not (args.apply or args.finish or args.show):
            return retire_login_test_only()
        if args.finish:
            return finish(args.retire_login_test)
        if args.tidy:
            return tidy()
        if args.repair_auth_mail_env:
            return repair_auth_mail_env()
        report = preflight(args.retire_login_test)
        if args.show:
            print(report['rendered'][args.show])
            return
        print_report(report)
        if not args.apply:
            print('\npreflight only; re-run with --apply to cut over.')
            return
        apply(report, args.retire_login_test)
    except Refusal as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
