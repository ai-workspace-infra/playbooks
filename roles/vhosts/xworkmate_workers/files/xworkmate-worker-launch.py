#!/usr/bin/python3
"""Bounded root launcher; configuration contains references, never secrets.

Public Gateway argv: dsh-acp|dsh-sdk UUID; export PROFILE UUID.
Admin argv: validate, preflight, verify [--models], stop, network-check,
archive-check ARCHIVE, artifact-check ENGINE DIRECTORY.
"""
import base64
import errno
import hashlib
import ipaddress
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import pwd
import grp
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONFIG_PATH = Path('/etc/xworkmate-workers/worker-config.json')
PROFILES = {'dsh-acp': 'acp', 'dsh-sdk': 'sdk'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise ValueError('readiness endpoint must not redirect credentials')


def http_open(request, timeout):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def namespace(profile, run_id):
    require(profile in PROFILES, 'unsupported DSH profile')
    require(str(uuid.UUID(run_id)) == run_id, 'run ID must be a canonical UUID')
    return profile + '-' + run_id


def validate_config(config, resolve_dns=False):
    url = urllib.parse.urlsplit(config['modelService']['baseUrl'])
    require(url.scheme == 'https' and url.hostname and not url.username and not url.password
            and not url.query and not url.fragment, 'model base URL must be HTTPS without credentials/query/fragment')
    require(url.path.rstrip('/').endswith('/v1'), 'model base URL must end in /v1')
    require(isinstance(config['modelService']['model'], str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', config['modelService']['model']), 'invalid explicit model ID')
    require(config['user'] != 'root' and re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', config['user']), 'worker must use a dedicated non-root account')
    require(re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', config['group']), 'invalid worker group')
    require(re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', config['gatewayUser']), 'invalid Gateway account')
    for field in ('stateRoot', 'configDir'):
        require(re.fullmatch(r'/[A-Za-z0-9_./-]+', config[field]) and '..' not in Path(config[field]).parts, 'invalid managed directory')
    require(config['configDir'] == str(CONFIG_PATH.parent), 'configDir is fixed by the root launcher contract')
    require(config['stateRoot'] == '/var/lib/xworkmate-workers', 'stateRoot is fixed by the Gateway adapter contract')
    shared = config['gatewayTasksRoot']
    require(re.fullmatch(r'/[A-Za-z0-9_./-]+', shared) and '..' not in Path(shared).parts and Path(shared).name == 'tasks', 'Gateway share must be one explicitly configured tasks subtree')
    for field in ('modelEnvFile', 'opencodeEnvFile'):
        require(re.fullmatch(r'/run/[A-Za-z0-9_./-]+', config[field]) and '..' not in Path(config[field]).parts, 'credentials must reference a dedicated /run env file')
    networks = [ipaddress.ip_network(value, strict=True) for value in config['egressCidrs']]
    require(networks and all(network.prefixlen > 0 for network in networks), 'explicit egress CIDRs required; global CIDRs forbidden')
    if resolve_dns:
        addresses = {ipaddress.ip_address(result[4][0]) for result in socket.getaddrinfo(url.hostname, url.port or 443, type=socket.SOCK_STREAM)}
        require(all(any(address in network for network in networks) for address in addresses), 'model DNS addresses are outside approved egress CIDRs')
    require(int(config['opencode']['port']) == 4097, 'loopback port is fixed by the Gateway adapter contract')
    for engine in ('dsh', 'opencode'):
        settings = config[engine]
        require(re.fullmatch(r'[0-9a-f]{40}', settings['sourceRevision']), 'full source revision required')
        require(re.fullmatch(r'/opt/[A-Za-z0-9_./-]+', settings['releaseDir']) and '..' not in Path(settings['releaseDir']).parts, 'release must be under /opt')
        require(re.fullmatch(r'[A-Za-z0-9_./-]+', settings['entrypoint']) and not Path(settings['entrypoint']).is_absolute() and '..' not in Path(settings['entrypoint']).parts, 'entrypoint must stay in immutable release')
    limits = config['limits']
    require(re.fullmatch(r'[1-9][0-9]*[KMG]', str(limits['memoryMax'])), 'invalid MemoryMax')
    require(re.fullmatch(r'[1-9][0-9]*%', str(limits['cpuQuota'])), 'invalid CPUQuota')
    require(1 <= int(limits['tasksMax']) <= 4096 and 1 <= int(limits['maxRuntimeSec']) <= 900
            and 1 <= int(limits['stopTimeoutSec']) <= 60, 'invalid bounded service limits')
    require(1 <= int(config['exportMaxBytes']) <= 32 * 1024 * 1024, 'export size must be at most 32 MiB')
    return config


def load_config():
    metadata = CONFIG_PATH.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022, 'configuration must be a root-owned non-writable regular file')
    return validate_config(json.loads(CONFIG_PATH.read_text()))


def artifact_check(config, engine, directory):
    require(engine in ('dsh', 'opencode'), 'unknown artifact engine')
    root = Path(directory).resolve(strict=True)
    manifest = json.loads((root / 'runtime-manifest.json').read_text())
    require(manifest.get('engine') == engine and manifest.get('sourceRevision') == config[engine]['sourceRevision'], 'artifact manifest engine/source revision mismatch')
    target = (root / config[engine]['entrypoint']).resolve(strict=True)
    require(target.is_relative_to(root) and target.is_file() and os.access(target, os.X_OK), 'artifact executable is missing or escapes release')
    return target


def archive_check(path):
    with tarfile.open(path, 'r:*') as archive:
        for member in archive.getmembers():
            value = PurePosixPath(member.name)
            require(not value.is_absolute() and '..' not in value.parts, 'archive has an unsafe path')
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(), 'archive has a device or special entry')
            if member.issym() or member.islnk():
                # Resolve lexical links within the archive; pnpm links may use
                # .. but must not escape the archive root.
                target = PurePosixPath(member.linkname)
                require(not target.is_absolute(), 'archive has an absolute link')
                stack = list(value.parent.parts) if member.issym() else []
                for part in target.parts:
                    if part == '..':
                        require(stack, 'archive link escapes root')
                        stack.pop()
                    elif part not in ('', '.'):
                        stack.append(part)


def read_runtime_env(path, allowed, required, owners=(0,)):
    path = Path(path)
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid in owners and stat.S_IMODE(metadata.st_mode) == 0o600, 'runtime env must be private 0600 for root/Gateway, not a symlink')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        require(separator and key in allowed and key not in values, 'runtime env contains an unsupported or duplicate key')
        pieces = shlex.split(value, comments=False, posix=True)
        require(len(pieces) == 1 and '\x00' not in pieces[0], 'runtime env value requires one quoted or unquoted literal')
        values[key] = pieces[0]
    require(all(values.get(key) for key in required), 'required runtime credential is missing')
    return values


def credential_owners(config):
    return (0, pwd.getpwnam(config['gatewayUser']).pw_uid)


def preflight(config):
    validate_config(config, resolve_dns=True)
    read_runtime_env(config['modelEnvFile'], {'XWORKMATE_LLM_API_KEY'}, {'XWORKMATE_LLM_API_KEY'}, credential_owners(config))
    read_runtime_env(config['opencodeEnvFile'], {'OPENCODE_PASSWORD'}, {'OPENCODE_PASSWORD'}, credential_owners(config))
    artifact_check(config, 'dsh', config['dsh']['releaseDir'])
    artifact_check(config, 'opencode', config['opencode']['releaseDir'])


def run_properties(config, root):
    limits = config['limits']
    properties = [
        'Type=exec', 'User=' + config['user'], 'Group=' + config['group'],
        'EnvironmentFile=' + config['modelEnvFile'],
        'Environment=HOME=' + str(root / 'home'), 'Environment=DSH_HOME=' + str(root / 'home'),
        'Environment=DSH_TELEMETRY_DISABLED=1', 'Environment=PATH=/usr/bin:/bin',
        'WorkingDirectory=' + str(root / 'workspace'),
        'UMask=0077', 'NoNewPrivileges=yes', 'PrivateTmp=yes', 'ProtectSystem=strict', 'ProtectHome=yes',
        'ProtectKernelTunables=yes', 'ProtectKernelModules=yes', 'ProtectControlGroups=yes',
        'RestrictSUIDSGID=yes', 'CapabilityBoundingSet=', 'KillMode=control-group',
        'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6',
        # Hide sibling runs even though the execution account is shared on
        # this single-account node. Only this namespace is bound back in.
        'TemporaryFileSystem=' + config['stateRoot'] + ':ro',
        'BindPaths=' + str(root), 'ReadWritePaths=' + str(root),
        'InaccessiblePaths=-' + str(Path(config['modelEnvFile']).parent),
        'IPAddressDeny=any', 'IPAddressAllow=localhost',
        'MemoryMax=' + str(limits['memoryMax']), 'CPUQuota=' + str(limits['cpuQuota']),
        'TasksMax=' + str(limits['tasksMax']), 'RuntimeMaxSec=' + str(limits['maxRuntimeSec']),
        'TimeoutStopSec=' + str(limits['stopTimeoutSec']),
    ]
    properties.extend('IPAddressAllow=' + cidr for cidr in config['egressCidrs'])
    return properties


def launch_argv(config, profile, run_id):
    name = namespace(profile, run_id)
    root = Path(config['stateRoot']) / 'runs' / profile / run_id
    argv = ['/usr/bin/systemd-run', '--quiet', '--pipe', '--wait', '--collect',
            '--unit=xworkmate-' + name]
    for value in run_properties(config, root):
        argv.extend(['--property', value])
    argv.extend([str(Path(config['dsh']['releaseDir']) / config['dsh']['entrypoint']), '--profile', PROFILES[profile],
                 '--patch', config['configDir'] + '/dsh-model.patch.yml',
                 '--patch', config['configDir'] + '/' + profile + '.patch.yml'])
    return argv, root, 'xworkmate-' + name + '.service'


def launch(config, profile, run_id):
    preflight(config)
    argv, root, unit = launch_argv(config, profile, run_id)
    # Never chown a reused worker-controlled directory: a previous attempt could
    # replace a child with a link between checking it and privileged chown. The
    # root-owned immutable namespace name and exclusive mkdir prevent that race.
    require(not root.exists() and not root.is_symlink(), 'run UUID already exists; use a new attempt UUID')
    uid, gid = pwd.getpwnam(config['user']).pw_uid, grp.getgrnam(config['group']).gr_gid
    root.mkdir(mode=0o755)
    for child in (root / 'home', root / 'workspace', root / 'workspace' / 'output'):
        child.mkdir(mode=0o700)
        os.chown(child, uid, gid)
        os.chmod(child, 0o700)
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    child = subprocess.Popen(argv, env=environment)
    def terminate(_number, _frame):
        subprocess.run(['/usr/bin/systemctl', 'stop', unit], check=True, timeout=65, env=environment)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    return child.wait()


def export_files(config, profile, run_id):
    namespace(profile, run_id)
    root = Path(config['stateRoot']) / 'runs' / profile / run_id / 'workspace' / 'output'
    require(not root.is_symlink(), 'output directory cannot be a symlink')
    resolved = root.resolve(strict=True)
    require(resolved == root, 'output directory ancestry cannot contain a symlink')
    files, total = [], 0
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink() and path.resolve(strict=True).is_relative_to(resolved), 'output contains a symlink or escaping path')
        if path.is_dir():
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'output must contain only regular files')
            size = os.fstat(fd).st_size
            require(len(files) < 8 and total + size <= int(config['exportMaxBytes']), 'output exceeds file-count or byte limit')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                content = stream.read(int(config['exportMaxBytes']) + 1)
            require(len(content) == size, 'output changed during export')
        finally:
            os.close(fd)
        total += size
        files.append({'relativePath': str(path.relative_to(root)), 'size': size,
                      'sha256': hashlib.sha256(content).hexdigest(),
                      'mimeType': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                      'contentBase64': base64.b64encode(content).decode('ascii')})
    return {'profile': profile, 'runId': run_id, 'artifacts': files}


def verify(config, models=False):
    preflight(config)
    password = read_runtime_env(config['opencodeEnvFile'], {'OPENCODE_PASSWORD'}, {'OPENCODE_PASSWORD'}, credential_owners(config))['OPENCODE_PASSWORD']
    url = 'http://127.0.0.1:' + str(config['opencode']['port']) + '/api/info'
    try:
        http_open(url, timeout=5).close()
        raise ValueError('OpenCode accepted an unauthenticated readiness request')
    except urllib.error.HTTPError as failure:
        require(failure.code == 401, 'OpenCode unauthenticated readiness did not fail closed')
    authorization = base64.b64encode(('opencode:' + password).encode()).decode()
    request = urllib.request.Request(url, headers={'Authorization': 'Basic ' + authorization})
    with http_open(request, timeout=5) as response:
        data = json.load(response)
    require(isinstance(data.get('version'), str) and int(data.get('pid', 0)) > 0, 'OpenCode readiness identity missing')
    result = {'opencodeReady': True, 'pid': data['pid'], 'version': data['version'], 'inferenceVerified': False}
    if models:
        key = read_runtime_env(config['modelEnvFile'], {'XWORKMATE_LLM_API_KEY'}, {'XWORKMATE_LLM_API_KEY'}, credential_owners(config))['XWORKMATE_LLM_API_KEY']
        request = urllib.request.Request(config['modelService']['baseUrl'].rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + key})
        with http_open(request, timeout=10) as response:
            catalog = json.load(response)
        require(any(item.get('id') == config['modelService']['model'] for item in catalog.get('data', [])), 'configured model absent from aggregator catalog')
        result['modelCatalogVerified'] = True
    return result


def network_check(config):
    # An accepting parent listener proves the destination exists. A cgroup
    # with IPAddressDeny=any must fail with EPERM/EACCES, not ECONNREFUSED.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(2)
        port = listener.getsockname()[1]
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            pass
        script = "import socket,errno,sys\ns=socket.socket();s.settimeout(2)\ntry:\n s.connect(('127.0.0.1',int(sys.argv[1])));sys.exit(1)\nexcept OSError as e:\n sys.exit(0 if e.errno in (errno.EACCES,errno.EPERM) else 2)"
        subprocess.run(['/usr/bin/systemd-run', '--quiet', '--pipe', '--wait', '--collect',
                        '--property', 'User=' + config['user'], '--property', 'IPAddressDeny=any',
                        '/usr/bin/python3', '-c', script, str(port)], check=True, timeout=15,
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})


def stop():
    listing = subprocess.run(['/usr/bin/systemctl', 'list-units', '--all', '--plain', '--no-legend',
                              'xworkmate-dsh-*.service'], check=True, capture_output=True, text=True)
    for line in listing.stdout.splitlines():
        unit = line.split()[0]
        require(re.fullmatch(r'xworkmate-dsh-(acp|sdk)-[0-9a-f-]{36}\.service', unit), 'unexpected worker unit')
        subprocess.run(['/usr/bin/systemctl', 'stop', unit], check=True, timeout=65)


def main(argv):
    require(os.geteuid() == 0, 'launcher requires root via the restricted sudo rule')
    config = load_config()
    if len(argv) == 2 and argv[0] in PROFILES:
        return launch(config, *argv)
    if len(argv) == 3 and argv[0] == 'export':
        unit = 'xworkmate-' + namespace(argv[1], argv[2]) + '.service'
        active = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', unit], capture_output=True)
        require(active.returncode in (3, 4), 'artifact export requires the owned worker unit to be inactive')
        print(json.dumps(export_files(config, argv[1], argv[2])))
    elif argv == ['validate']:
        validate_config(config)
    elif argv == ['preflight']:
        preflight(config)
    elif argv in (['verify'], ['verify', '--models']):
        print(json.dumps(verify(config, len(argv) == 2)))
    elif argv == ['network-check']:
        network_check(config)
    elif argv == ['stop']:
        stop()
    elif len(argv) == 2 and argv[0] == 'archive-check':
        archive_check(argv[1])
    elif len(argv) == 3 and argv[0] == 'artifact-check':
        artifact_check(config, argv[1], argv[2])
    else:
        raise ValueError('invalid launcher arguments')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as failure:
        # Never include URL headers, env content, or a subprocess argv in logs.
        detail = str(failure) if isinstance(failure, ValueError) else type(failure).__name__
        print('xworkmate worker operation failed: ' + detail, file=sys.stderr)
        sys.exit(1)
