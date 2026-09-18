#!/usr/bin/env python3
"""Install the existing OT-2 edge credential into a dashboard API service.

The value must match the deployed gateway/edge; this does not create or rotate
credentials. No equipment endpoints are called. Run --help before installation.
"""
import argparse
import getpass
import os
from pathlib import Path
import re
import subprocess
import time


def systemctl(*args):
    return subprocess.check_output(['systemctl', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', required=True, help='Active dashboard API systemd unit')
    parser.add_argument('--credential-file', type=Path,
                        help='Private file containing the existing credential; otherwise prompt')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.service', args.unit):
        raise RuntimeError('Expected a simple .service unit name')
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo; system configuration requires root')
    if systemctl('is-active', args.unit) != 'active':
        raise RuntimeError('Target service must already be active')
    env_path = Path('/etc/dashboard-integrations') / (args.unit + '.ot2.env')
    drop_path = Path('/etc/systemd/system') / (args.unit + '.d') / 'zzzzzzzz-ot2-auth.conf'
    if env_path.exists() or drop_path.exists():
        raise RuntimeError('OT-2 override already exists; refusing to overwrite it')
    if args.credential_file:
        info = args.credential_file.stat()
        if info.st_mode & 0o077:
            raise RuntimeError('Credential file must not be accessible to group or others')
        secret = args.credential_file.read_text()
    else:
        secret = getpass.getpass('Existing OT2_EDGE_SECRET from the OT-2 edge (hidden): ')
    if not secret or secret != secret.strip() or any(c in secret for c in '\r\n\0'):
        raise RuntimeError('Credential must be nonempty, single-line, without surrounding whitespace')
    if not args.credential_file and secret != getpass.getpass('Repeat existing credential (hidden): '):
        raise RuntimeError('Values do not match; nothing changed')
    escaped = secret.replace('\\', '\\\\').replace('"', '\\"')
    created = []
    try:
        env_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        drop_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        for path, mode, content in (
            (env_path, 0o600, f'OT2_EDGE_SECRET="{escaped}"\n'),
            (drop_path, 0o644, f'[Service]\nEnvironmentFile={env_path}\n'),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            created.append(path)
            with os.fdopen(fd, 'w') as f:
                f.write(content)
        systemctl('daemon-reload')
        systemctl('restart', args.unit)
        for _ in range(20):
            pid = systemctl('show', args.unit, '-p', 'MainPID', '--value')
            if pid != '0':
                env = dict(item.split(b'=', 1) for item in
                           Path('/proc', pid, 'environ').read_bytes().split(b'\0') if b'=' in item)
                if env.get(b'OT2_EDGE_SECRET') == secret.encode():
                    print('Installed: dashboard API loaded the OT-2 credential. No device commands issued.')
                    print('Gateway acceptance still needs verification through the normal operator flow.')
                    return
            time.sleep(0.5)
        raise RuntimeError('Restarted service did not load the credential')
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        systemctl('daemon-reload')
        systemctl('restart', args.unit)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never include subprocess output or credential-bearing exception data.
        raise SystemExit('Installation failed; no credential value was printed. Check service status.')
