#!/usr/bin/env python3
"""Root-only, fixed-path runtime environment bootstrap; never log credentials.

Opt-in role wiring supplies the nonsecret config. Each invocation reloads the
model key; an existing valid OpenCode password is retained. Vault rotation
requires an explicit bootstrap + Gateway/OpenCode restart, not automatic reload.
"""
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import sys
import tempfile

RUN_ROOT = Path('/run')
ETC_ROOT = Path('/etc')
CONFIG_PATH = Path('/etc/xworkmate-workers/runtime-env-source.json')
OUTPUT_DIR = Path('/run/xworkmate-workers')
BEARER = re.compile(rb'[A-Za-z0-9._~+/=\-]{1,8192}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_path(value, root):
    require(isinstance(value, str), 'invalid configured path')
    path = Path(value)
    require(path.is_absolute() and str(path) == value and '..' not in path.parts,
            'invalid configured path')
    require(path.is_relative_to(root) and path != root, 'source must be under managed root')
    return path


def trusted_directory(path):
    metadata = path.lstat()
    require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0 and
            not metadata.st_mode & 0o022, 'unsafe managed directory')
    return metadata


def trusted_parents(path, root):
    trusted_directory(root)
    parent = root
    for name in path.relative_to(root).parts[:-1]:
        parent = parent / name
        trusted_directory(parent)


def checked_read(path, root, owner, limit, private=True):
    trusted_parents(path, root)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == owner and before.st_nlink == 1,
            'unsafe credential file ownership or type')
    require(stat.S_IMODE(before.st_mode) == 0o600 if private else not before.st_mode & 0o022,
            'unsafe file mode')
    require(before.st_size <= limit, 'managed file exceeds size limit')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino, before.st_uid, before.st_mode) ==
                (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode),
                'managed file changed during open')
        value = stream.read(limit + 1)
    require(len(value) <= limit, 'managed file exceeds size limit')
    return value


def bearer_literal(value):
    # A single LF terminates a text file. All remaining whitespace, CR/LF/NUL,
    # quotes, shell substitutions and escapes are rejected by the literal grammar.
    if value.endswith(b'\n'):
        value = value[:-1]
    require(BEARER.fullmatch(value) is not None, 'invalid bearer literal')
    return value.decode('ascii')


def existing_password(path, gateway_uid):
    if not path.exists() and not path.is_symlink():
        return None
    data = checked_read(path, RUN_ROOT, gateway_uid, 256)
    match = re.fullmatch(rb'OPENCODE_PASSWORD=([0-9a-f]{64})\n?', data)
    require(match is not None, 'invalid managed OpenCode password environment')
    return match.group(1).decode('ascii')


def check_existing_model(path, gateway_uid):
    if not path.exists() and not path.is_symlink():
        return
    data = checked_read(path, RUN_ROOT, gateway_uid, 16384)
    prefix = b'XWORKMATE_LLM_API_KEY='
    require(data.startswith(prefix), 'invalid managed model environment')
    bearer_literal(data[len(prefix):])


def atomic_environment(path, variable, value, uid, gid):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=OUTPUT_DIR)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), uid, gid)
            stream.write((variable + '=' + value + '\n').encode('ascii'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(OUTPUT_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def initialize():
    require(os.geteuid() == 0, 'root is required')
    config = json.loads(checked_read(CONFIG_PATH, ETC_ROOT, 0, 32768, private=False))
    require(isinstance(config, dict) and set(config) == {'keySourceFile', 'gatewayUser'},
            'invalid runtime environment source config')
    user = config['gatewayUser']
    require(isinstance(user, str) and re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}\$?', user),
            'invalid Gateway account')
    gateway = pwd.getpwnam(user)
    require(gateway.pw_uid > 0, 'Gateway must use a nonroot account')
    source = checked_path(config['keySourceFile'], RUN_ROOT)
    trusted_directory(RUN_ROOT)
    OUTPUT_DIR.mkdir(mode=0o710, exist_ok=True)
    trusted_directory(OUTPUT_DIR)
    os.chown(OUTPUT_DIR, 0, gateway.pw_gid)
    OUTPUT_DIR.chmod(0o710)
    lock_fd = os.open(OUTPUT_DIR / '.bootstrap.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        lock = os.fstat(lock_fd)
        require(stat.S_ISREG(lock.st_mode) and lock.st_uid == 0 and
                lock.st_nlink == 1 and stat.S_IMODE(lock.st_mode) == 0o600, 'unsafe bootstrap lock')
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # Read after acquiring the bootstrap lock, so competing invocations
        # cannot restore an earlier key snapshot after a Vault rotation.
        model_key = bearer_literal(checked_read(source, RUN_ROOT, 0, 8193))
        password_path = OUTPUT_DIR / 'opencode.env'
        password = existing_password(password_path, gateway.pw_uid)
        preserved = password is not None
        check_existing_model(OUTPUT_DIR / 'model.env', gateway.pw_uid)
        atomic_environment(OUTPUT_DIR / 'model.env', 'XWORKMATE_LLM_API_KEY', model_key,
                           gateway.pw_uid, gateway.pw_gid)
        if not preserved:
            atomic_environment(password_path, 'OPENCODE_PASSWORD', secrets.token_hex(32),
                               gateway.pw_uid, gateway.pw_gid)
    finally:
        os.close(lock_fd)
    return {'status': 'ready', 'gatewayUid': gateway.pw_uid,
            'modelKeyPresent': True, 'opencodePasswordPreserved': preserved}


def main():
    try:
        report = initialize()
    except Exception:
        # Deliberately omit exception text: it may originate in a secret reader.
        sys.stderr.write('XWorkmate runtime environment initialization failed.\n')
        return 1
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 1:
        sys.stderr.write('XWorkmate runtime environment initialization failed.\n')
        sys.exit(1)
    sys.exit(main())
