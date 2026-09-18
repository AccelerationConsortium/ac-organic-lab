#!/usr/bin/env python3
"""Restore migrated Tapo status credentials and a writable ONVIF cache.

Only restarts the specified camera gateway. Does not change the relay, enable
streams, move cameras, or change camera access rules. Requires root.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time


KEYS = {
    'CAM_HTE_TAPO_C245_CLOUD_PASS',
    'CAM_ECHEM_TAPO_C245_CLOUD_PASS',
    'CAM_ECHEM_TAPO_C100_CLOUD_PASS',
}


def ctl(*args):
    return subprocess.check_output(['systemctl', *args], text=True).strip()


def quote(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\0'):
        raise ValueError('Invalid credential value')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--credential-file', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.service', args.unit):
        raise RuntimeError('Expected a simple service name')
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo')
    if args.credential_file.stat().st_mode & 0o077:
        raise RuntimeError('Credential file must be private')
    values = json.loads(args.credential_file.read_text())
    if not isinstance(values, dict) or set(values) != KEYS:
        raise RuntimeError('Expected exactly the three approved camera fields')
    content = ''.join(k + '=' + quote(v) + '\n' for k, v in sorted(values.items()))
    if ctl('is-active', args.unit) != 'active':
        raise RuntimeError('Gateway must already be active')
    account = pwd.getpwnam(ctl('show', args.unit, '-p', 'User', '--value'))
    if account.pw_uid == 0:
        raise RuntimeError('Expected a non-root gateway')
    env_path = Path('/etc/dashboard-integrations') / (args.unit + '.camera-status.env')
    drop_path = Path('/etc/systemd/system') / (args.unit + '.d') / 'zzzzzzzz-camera-status.conf'
    cache_name = args.unit.removesuffix('.service') + '-onvif'
    cache_path = '/var/cache/' + cache_name
    if env_path.exists() or drop_path.exists():
        raise RuntimeError('Repair already installed; refusing to overwrite')
    drop = ('[Service]\nEnvironmentFile=' + str(env_path)
            + '\nCacheDirectory=' + cache_name + '\nCacheDirectoryMode=0700\n'
            + 'Environment="XDG_CACHE_HOME=' + cache_path + '"\n')
    created = []
    try:
        for path, mode, text in ((env_path, 0o600, content), (drop_path, 0o644, drop)):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if path == env_path else 0o755)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            created.append(path)
            with os.fdopen(fd, 'w') as f:
                f.write(text)
        ctl('daemon-reload')
        ctl('restart', args.unit)
        for _ in range(20):
            pid = ctl('show', args.unit, '-p', 'MainPID', '--value')
            if pid != '0':
                proc = Path('/proc', pid)
                env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
                if all(env.get(k.encode()) == v.encode() for k, v in values.items()):
                    if env.get(b'XDG_CACHE_HOME') != cache_path.encode():
                        raise RuntimeError('Cache environment not applied')
                    python = (proc / 'cmdline').read_bytes().split(b'\0')[0].decode()
                    # Test the actual service mount namespace, as its unprivileged
                    # user. No network requests or camera authentication involved.
                    code = (
                        'import os; os.setgroups([]); os.setgid(' + str(account.pw_gid)
                        + '); os.setuid(' + str(account.pw_uid) + '); '
                        + 'os.environ["XDG_CACHE_HOME"]=' + repr(cache_path)
                        + '; from zeep.cache import SqliteCache; SqliteCache()'
                    )
                    subprocess.run(['nsenter', '-t', pid, '-m', '--', python, '-c', code],
                                   check=True, capture_output=True)
                    print('PASS: gateway loaded all three restored credential settings.')
                    print('PASS: ONVIF cache is writable inside the service sandbox.')
                    print('Camera login suspensions may need time to expire; no camera controls issued.')
                    return
            time.sleep(0.5)
        raise RuntimeError('Gateway did not load repaired settings')
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        ctl('daemon-reload')
        ctl('restart', args.unit)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Camera repair failed; credential details suppressed. Check gateway service status.')
