#!/usr/bin/env python3
"""Validate staged PEM and atomically publish; never read Vault or reload Caddy."""
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

FILES = ('fullchain.pem', 'cert.pem', 'key.pem', 'ca.pem', 'trust-bundle.pem')


def openssl(*args, data=None):
    return subprocess.run(['openssl', *args], input=data, capture_output=True,
                          check=True, timeout=30).stdout


def restore(stage, base, margin):
    stage, base = Path(stage), Path(base)
    versions = base / 'versions'
    if margin < 0 or stage.parent != versions or not stage.name.startswith('.restore-'):
        raise ValueError('unsafe staging contract')
    for directory in (base, versions, stage):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('unsafe directory')
    for name in FILES:
        path = stage / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('incomplete generation')
    for name in ('fullchain.pem', 'cert.pem', 'ca.pem', 'trust-bundle.pem'):
        openssl('x509', '-in', str(stage / name), '-noout')
    openssl('pkey', '-in', str(stage / 'key.pem'), '-noout')
    openssl('x509', '-in', str(stage / 'cert.pem'), '-checkend', str(margin), '-noout')
    leaf = openssl('x509', '-in', str(stage / 'cert.pem'), '-noout', '-fingerprint', '-sha256')
    chain_leaf = openssl('x509', '-in', str(stage / 'fullchain.pem'), '-noout', '-fingerprint', '-sha256')
    if leaf != chain_leaf:
        raise ValueError('leaf mismatch')
    public = openssl('x509', '-in', str(stage / 'cert.pem'), '-pubkey', '-noout')
    cert_key = openssl('pkey', '-pubin', '-outform', 'DER', data=public)
    private_key = openssl('pkey', '-in', str(stage / 'key.pem'), '-pubout', '-outform', 'DER')
    if cert_key != private_key:
        raise ValueError('key mismatch')
    fingerprint = leaf.decode().strip().split('=', 1)[1].replace(':', '')
    if not re.fullmatch('[A-Fa-f0-9]{64}', fingerprint):
        raise ValueError('invalid fingerprint')
    # Lock publication per destination; preserve every previous generation.
    lock_fd = os.open(base / '.restore.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        version = versions / fingerprint
        if version.exists() or version.is_symlink():
            if version.is_symlink() or not version.is_dir():
                raise ValueError('unsafe existing generation')
            for name in FILES:
                stored = version / name
                if stored.is_symlink() or not stored.is_file() or stored.read_bytes() != (stage / name).read_bytes():
                    raise ValueError('immutable generation differs')
            if stat.S_IMODE(version.stat().st_mode) != 0o700 or stat.S_IMODE((version / 'key.pem').stat().st_mode) != 0o600:
                raise ValueError('insecure stored generation')
        else:
            os.chmod(stage, 0o700)
            for name in FILES:
                os.chmod(stage / name, 0o600 if name == 'key.pem' else 0o644)
            os.rename(stage, version)
        target = 'versions/' + fingerprint
        current = base / 'current'
        if current.is_symlink() and os.readlink(current) == target:
            return False
        if current.exists() and not current.is_symlink():
            raise ValueError('current is not a symlink')
        with tempfile.TemporaryDirectory(prefix='.link-', dir=base) as link_dir:
            temporary_link = Path(link_dir) / 'current'
            temporary_link.symlink_to(target)
            os.replace(temporary_link, current)
        return True


if __name__ == '__main__':
    try:
        changed = restore(sys.argv[1], sys.argv[2], int(sys.argv[3]))
        print('published' if changed else 'unchanged')
    except (ValueError, OSError, subprocess.SubprocessError, IndexError):
        # Do not print PEM, command output or secret-bearing exception detail.
        print('PEM restore failed; current generation was not replaced.', file=sys.stderr)
        sys.exit(1)
