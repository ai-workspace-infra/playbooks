#!/usr/bin/env python3
"""Build pinned Linux release candidates. No publish, deploy, or model calls.

Run in a disposable Linux build host with reviewed dependency install scripts.
Only git-tracked pinned sources are copied. Ambient credentials are not inherited.
The output is a candidate; Linux ACP/SDK/server acceptance precedes publication.
"""
import argparse
import base64
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import secrets
import shutil
import subprocess
import socket
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

PINS = {
    'dsh': '639ed015397290b3745d163aafe02ffee4aa3f84',
    'opencode': '35a41b5d53c71ae0337e614ec192fd5d1a5c7eb8',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def command(argv, cwd, env, capture=False):
    return subprocess.run([str(value) for value in argv], cwd=cwd, env=env,
                          check=True, text=True, stdout=subprocess.PIPE if capture else None,
                          timeout=7200).stdout


def build_host_projects(pnpm, node, repository, env):
    # The aggregate also checks hundreds of desktop/browser tests. Emit and
    # check its unchanged project references separately: each compiler process
    # releases its heap before the next project under bounded Linux resources.
    script = ('const ts=require("typescript"); '
              'const result=ts.readConfigFile("tsconfig.host.json",ts.sys.readFile); '
              'if(result.error)throw Error(ts.flattenDiagnosticMessageText(result.error.messageText,"\\n")); '
              'console.log(JSON.stringify(result.config.references.map(ref=>ref.path)))')
    projects = json.loads(command([node, '-e', script], repository, env, True))
    require(isinstance(projects, list) and projects, 'host project references required')
    for project in projects:
        require(isinstance(project, str) and (repository / project).resolve().is_relative_to(repository.resolve()),
                'host project reference escapes pinned repository')
        command([pnpm, 'exec', 'tsc', '-b', project], repository, env)


def build_worker_node_halves(repository, env):
    # Upstream places these shared Node libraries in its Client pass despite
    # mounting them in ACP/SDK base profiles. Reuse each original package's
    # config factory, selecting only platform=node; no browser assets emitted.
    projects = ('packages/client/connection', 'packages/typert/registry', 'packages/api/gateway')
    for project in projects:
        package = repository / project
        config = package / '.xworkmate-worker-node.config.ts'
        config.write_text('import upstream from "./tsdown.config.ts";\n'
                          'export default upstream({env:{DSH_BUILD_FACE:"client"}})'
                          '.filter(config=>config.platform==="node");\n')
        try:
            command([repository / 'node_modules/.bin/tsdown', '--config', config], package, env)
        finally:
            config.unlink()
        require((package / 'lib/index.js').is_file(), 'worker shared Node entry missing: ' + project)


def smoke_dsh_protocols(wrapper, work, env):
    """Initialize both actual transports without submitting any model prompt."""
    fixture = work / 'keyless-protocol-smoke'
    fixture.mkdir()
    overlay = fixture / 'models.patch.yml'
    overlay.write_text('''- id: llm-pi-ai
  config:
    providers:
      xworkmate:
        api: openai-completions
        apiKeyEnv: XWORKMATE_LLM_API_KEY
        baseURL: https://model.invalid/v1
        models:
          - id: fixture-model
            contextWindow: 32768
            maxTokens: 512
- id: agent-default-model
  config:
    provider: xworkmate
    model: fixture-model
- id: llm-deepseek
  disabled: true
- id: llm-deepseek-account
  disabled: true
- id: deepseek-account
  disabled: true
- id: plugin-manager
  disabled: true
''')
    result = {}
    for profile in ('acp', 'sdk'):
        profile_overlay = overlay
        if profile == 'acp':
            profile_overlay = fixture / 'acp.patch.yml'
            profile_overlay.write_text(overlay.read_text() + '- id: acp\n  config:\n    provider: xworkmate\n    model: fixture-model\n')
        isolated = dict(env, HOME=str(fixture / (profile + '-home')),
                        DSH_HOME=str(fixture / (profile + '-dsh-home')))
        log = fixture / (profile + '.stderr.log')
        with log.open('wb') as stderr:
            peer = subprocess.Popen([str(wrapper), '--profile', profile, '--patch', str(profile_overlay)],
                                    cwd=fixture, env=isolated, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=stderr)
            pending = bytearray()
            request_id = 0
            def request(method, params):
                nonlocal request_id
                request_id += 1
                peer.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': request_id,
                                              'method': method, 'params': params}) + '\n').encode())
                peer.stdin.flush()
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if b'\n' not in pending:
                        ready, _, _ = select.select([peer.stdout], [], [], max(0, deadline - time.monotonic()))
                        require(ready, 'keyless ' + profile + ' initialization timed out')
                        chunk = os.read(peer.stdout.fileno(), 65536)
                        require(chunk, 'keyless ' + profile + ' exited before response; inspect ' + str(log))
                        pending.extend(chunk)
                        require(len(pending) <= 1048576, 'keyless protocol output exceeds limit')
                        continue
                    line, _, remainder = pending.partition(b'\n')
                    pending[:] = remainder
                    frame = json.loads(line)
                    if frame.get('id') == request_id:
                        require('result' in frame and 'error' not in frame, 'keyless protocol request failed: ' + method)
                        return frame['result']
                raise TimeoutError('keyless protocol initialization deadline')
            try:
                if profile == 'acp':
                    initialized = request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}})
                    require(initialized.get('agentInfo', {}).get('name') == 'deepseek-harness-acp', 'unexpected ACP identity')
                    session = request('session/new', {'cwd': str(fixture), 'mcpServers': []})
                    require(isinstance(session.get('sessionId'), str), 'ACP session missing')
                    model_value = json.dumps(['xworkmate', 'fixture-model'], separators=(',', ':'))
                    choices = [choice for option in session.get('configOptions', []) if option.get('id') == 'model'
                               for row in option.get('options', []) for choice in row.get('options', [row])]
                    require(any(choice.get('value') == model_value for choice in choices), 'ACP fixture model route missing')
                    request('session/set_config_option', {'sessionId': session['sessionId'], 'configId': 'model', 'value': model_value})
                    request('session/close', {'sessionId': session['sessionId']})
                    result[profile] = ['initialize', 'session/new', 'session/set_config_option', 'session/close']
                else:
                    initialized = request('initialize', {'cwd': str(fixture), 'provider': 'xworkmate', 'model': 'fixture-model'})
                    require(initialized.get('serverInfo', {}).get('name') == 'deepseek-harness-sdk-runtime', 'unexpected SDK identity')
                    request('shutdown', {})
                    require(peer.wait(timeout=15) == 0, 'SDK keyless shutdown failed')
                    result[profile] = ['initialize', 'shutdown']
            finally:
                if peer.poll() is None:
                    peer.terminate()
                    try:
                        peer.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        peer.kill()
                        peer.wait(timeout=5)
                peer.stdin.close()
                peer.stdout.close()
        require('did not activate' not in log.read_text(), 'DSH profile contains inactive plugin entries; inspect ' + str(log))
    return result


def smoke_opencode_api(binary, work, env):
    """Verify the compiled HTTP API and Basic auth with task-only credentials."""
    fixture = work / 'keyless-http-smoke'
    fixture.mkdir()
    config = fixture / 'opencode.json'
    config.write_text(json.dumps({'update': 'disable', 'share': 'disabled', 'plugins': [],
                                 'model': {'providerID': 'xworkmate', 'model': 'fixture-model'},
                                 'providers': {'xworkmate': {'package': '@opencode/ai/providers/openai-compatible',
                                                           'settings': {'baseURL': 'https://model.invalid/v1'},
                                                           'models': {'fixture-model': {'name': 'fixture-model', 'capabilities': {'tools': True}}}}}}))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    password = secrets.token_urlsafe(32)
    isolated = dict(env, HOME=str(fixture / 'home'), XDG_CONFIG_HOME=str(fixture / 'config'),
                    XDG_CACHE_HOME=str(fixture / 'cache'), XDG_DATA_HOME=str(fixture / 'data'),
                    OPENCODE_CONFIG=str(config), OPENCODE_CONFIG_PROJECT_DISABLE='1',
                    OPENCODE_DISABLE_MODELS_FETCH='1', OPENCODE_PASSWORD=password)
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = 'http://127.0.0.1:' + str(port) + '/api/info'
    header = 'Basic ' + base64.b64encode(('opencode:' + password).encode()).decode()
    with (fixture / 'server.log').open('wb') as log:
        peer = subprocess.Popen([str(binary), 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                cwd=fixture, env=isolated, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 90
            info = None
            while time.monotonic() < deadline:
                require(peer.poll() is None, 'OpenCode API exited during keyless initialization')
                try:
                    with client.open(urllib.request.Request(url, headers={'Authorization': header}), timeout=2) as response:
                        info = json.load(response)
                        break
                except urllib.error.HTTPError as error:
                    error.close()
                    time.sleep(0.5)
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.5)
            require(isinstance(info, dict) and info.get('version') == '2.0.22', 'OpenCode API readiness/version failed')
            try:
                client.open(url, timeout=2).close()
                raise ValueError('OpenCode API unexpectedly accepted an unauthenticated request')
            except urllib.error.HTTPError as error:
                try:
                    require(error.code == 401, 'OpenCode API must return 401 without Basic credentials')
                finally:
                    error.close()
            return {'version': info['version'], 'authenticatedInfo': True, 'unauthenticatedStatus': 401,
                    'modelPromptSubmitted': False, 'nativeProviderPackage': '@opencode/ai/providers/openai-compatible'}
        finally:
            if peer.poll() is None:
                peer.terminate()
                try:
                    peer.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    peer.kill()
                    peer.wait(timeout=5)


def export_source(repository, revision, destination, source_tar=None, source_sha256=None):
    # No working-tree files, .env, auth cache, or existing node_modules are copied.
    if source_tar is not None:
        require(re.fullmatch('[0-9a-f]{64}', source_sha256 or '') is not None,
                'transported git archive requires its locally verified SHA256')
        require(digest(source_tar) == source_sha256, 'transported source archive checksum mismatch')
        destination.mkdir(parents=True)
        with tarfile.open(source_tar) as archive:
            archive.extractall(destination, filter='data')
        require(not (destination / '.env').exists(), 'tracked .env is forbidden in release input')
        return
    actual = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', revision + '^{commit}'], text=True).strip()
    require(actual == revision, 'source revision does not resolve to the pinned commit')
    destination.mkdir(parents=True)
    archive = destination.parent / (destination.name + '.source.tar')
    subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar',
                    '--output=' + str(archive), revision], check=True)
    with tarfile.open(archive) as file:
        file.extractall(destination, filter='data')
    archive.unlink()
    require(not (destination / '.env').exists(), 'tracked .env is forbidden in release input')


def archive_candidate(root, engine, output, target):
    # Dependencies may link to other directories inside the archive, never to a
    # developer checkout or a global package store outside the artifact.
    resolved_root = root.resolve()
    for path in root.rglob('*'):
        if path.is_symlink():
            require(path.resolve().is_relative_to(resolved_root), 'dependency symlink escapes candidate')
        require(path.name not in ('.env', 'auth.json', 'auth-profiles.json'), 'runtime credential file cannot be packaged')
    archive = output / (engine + '-' + PINS[engine] + '-' + target + '.tar.gz')
    require(not archive.exists(), 'refuse to overwrite an existing release candidate')
    with tarfile.open(archive, 'w:gz', dereference=False) as file:
        # Relative paths match the root-extraction deployment contract.
        for path in sorted(root.iterdir()):
            file.add(path, arcname=path.name)
    checksum = digest(archive)
    archive.with_suffix(archive.suffix + '.sha256').write_text(checksum + '  ' + archive.name + '\n')
    return {'engine': engine, 'sourceRevision': PINS[engine], 'file': str(archive),
            'sha256': checksum, 'entrypoint': 'bin/' + ('dsh' if engine == 'dsh' else 'opencode'),
            'liveInferenceVerified': False, 'deploymentVerified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsh-repo', type=Path)
    parser.add_argument('--dsh-source-tar', type=Path)
    parser.add_argument('--dsh-source-sha256')
    parser.add_argument('--opencode-repo', type=Path)
    parser.add_argument('--opencode-source-tar', type=Path)
    parser.add_argument('--opencode-source-sha256')
    parser.add_argument('--linux-node-archive', type=Path, required=True)
    parser.add_argument('--linux-node-sha256', required=True)
    parser.add_argument('--pnpm', type=Path, required=True, help='preinstalled pnpm 11.7.0 executable')
    parser.add_argument('--bun', type=Path, required=True, help='preinstalled Bun 1.4.2 executable')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--musl-license', type=Path, help='copyright notice for the Linux static musl-linked launcher')
    parser.add_argument('--worker-only', action='store_true', help='build ACP/SDK and v2 API workers without separate desktop/web UI bundles')
    parser.add_argument('--work-root', type=Path, help='fresh task-owned directory; retained on failure for diagnostics')
    parser.add_argument('--engine', choices=['dsh', 'opencode', 'both'], default='both')
    args = parser.parse_args()
    for engine in ('dsh', 'opencode'):
        if args.engine in (engine, 'both'):
            require(bool(getattr(args, engine + '_repo')) != bool(getattr(args, engine + '_source_tar')), 'supply one repository or transported git archive per selected engine')
    require(platform.system() == 'Linux', 'native Linux build host required')
    if args.engine in ('dsh', 'both'):
        args.musl_license = args.musl_license or Path('/usr/share/doc/musl/copyright')
        require(args.musl_license.is_file(), 'provide --musl-license for the static Linux launcher before building')
    arch = {'x86_64': 'x64', 'aarch64': 'arm64'}.get(platform.machine())
    require(arch is not None, 'only Linux x64 and arm64 glibc build hosts supported')
    require(re.fullmatch('[0-9a-f]{64}', args.linux_node_sha256) is not None,
            'supply the verified Node Linux archive checksum')
    require(digest(args.linux_node_archive) == args.linux_node_sha256, 'Node archive checksum mismatch')
    for tool in (args.pnpm, args.bun):
        require(tool.is_absolute() and tool.is_file() and os.access(tool, os.X_OK), 'explicit executable tool paths required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.work_root:
        args.work_root.mkdir(parents=True)
    workspace = nullcontext(str(args.work_root.resolve())) if args.work_root else tempfile.TemporaryDirectory(prefix='xworkmate-worker-release-')
    with workspace as temporary:
        work = Path(temporary)
        home = work / 'home'
        home.mkdir()
        node = work / 'node'
        node.mkdir()
        with tarfile.open(args.linux_node_archive) as archive:
            archive.extractall(node, filter='data')
        node_roots = list(node.iterdir())
        require(len(node_roots) == 1 and (node_roots[0] / 'bin/node').is_file(), 'expected official Node archive layout')
        node_root = node_roots[0]
        env = {'HOME': str(home), 'PATH': ':'.join([str(node_root / 'bin'), str(args.pnpm.parent), str(args.bun.parent), '/usr/local/bin', '/usr/bin', '/bin']),
               'CI': 'true', 'DSH_TELEMETRY_DISABLED': '1', 'OPENCODE_CHANNEL': 'local',
               'OPENCODE_VERSION': '2.0.22', 'OPENCODE_DISABLE_MODELS_FETCH': '1',
               'CMAKE_BUILD_PARALLEL_LEVEL': '1', 'MAKEFLAGS': '-j1', 'npm_config_jobs': '1', 'HUSKY': '0'}
        node_version = command([node_root / 'bin/node', '--version'], work, env, True).strip()
        require(re.fullmatch(r'v(?:22\.(?:19|[2-9][0-9])\.[0-9]+|(?:2[4-9]|[3-9][0-9])\.[0-9]+\.[0-9]+)', node_version), 'DSH requires Node >=22.19 except Node 23')
        node_arch = command([node_root / 'bin/node', '-p', 'process.arch'], work, env, True).strip()
        require(node_arch == arch, 'Node archive architecture mismatch')
        require(command([args.pnpm, '--version'], work, env, True).strip() == '11.7.0', 'pnpm must be exactly 11.7.0')
        require(command([args.bun, '--version'], work, env, True).strip() == '1.4.2', 'Bun must be exactly 1.4.2')
        results = []
        if args.engine in ('dsh', 'both'):
            dsh = work / 'dsh-candidate'
            export_source(args.dsh_repo, PINS['dsh'], dsh / 'repo', args.dsh_source_tar, args.dsh_source_sha256)
            print('stage: dsh frozen dependency install', flush=True)
            command([args.pnpm, 'install', '--frozen-lockfile'], dsh / 'repo', env)
            print('stage: dsh upstream build', flush=True)
            if args.worker_only:
                command([args.pnpm, 'run', 'build:native-system'], dsh / 'repo', env)
                build_host_projects(args.pnpm, node_root / 'bin/node', dsh / 'repo', env)
                command([args.pnpm, 'exec', 'tsdown', '--env.DSH_BUILD_FACE', 'host'], dsh / 'repo', env)
                build_worker_node_halves(dsh / 'repo', env)
            else:
                command([args.pnpm, 'run', 'build'], dsh / 'repo', env)
            # The root build only makes the glibc host addon. A Linux release
            # must also carry the static Landlock fallback and musl addon.
            command([args.pnpm, 'exec', 'tsx', 'native/system/scripts/build.ts'], dsh / 'repo', env)
            require(args.musl_license and args.musl_license.is_file(), 'provide the static musl launcher copyright notice')
            (dsh / 'third-party-licenses').mkdir()
            shutil.copy2(args.musl_license, dsh / 'third-party-licenses/musl-copyright.txt')
            (dsh / 'runtime').mkdir()
            shutil.move(str(node_root), dsh / 'runtime/node')
            (dsh / 'bin').mkdir()
            wrapper = dsh / 'bin/dsh'
            wrapper.write_text('#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$root/runtime/node/bin/node" "$root/repo/apps/cli/lib/bin.js" "$@"\n')
            wrapper.chmod(0o755)
            env['PATH'] = str(dsh / 'runtime/node/bin') + ':' + env['PATH']
            # Inspection only, no prompt/model credentials. StdIO initialization,
            # cancellation and server acceptance remain separate Linux runtime gates.
            for profile in ('acp', 'sdk'):
                command([wrapper, '--profile', profile, '--dump-default-config'], dsh, env, True)
            protocol_smoke = smoke_dsh_protocols(wrapper, work, env)
            (dsh / 'runtime-manifest.json').write_text(json.dumps({'engine': 'dsh', 'sourceRevision': PINS['dsh'], 'version': json.loads((dsh / 'repo/apps/cli/package.json').read_text())['version'], 'nodeVersion': node_version, 'sourceArchiveSha256': args.dsh_source_sha256, 'keylessConfigSmoke': ['acp', 'sdk'], 'keylessProtocolSmoke': protocol_smoke, 'workerOnly': args.worker_only, 'hostProjectReferencesChecked': True, 'rootTestAggregateChecked': not args.worker_only, 'sharedWorkerNodeHalves': ['client/connection', 'typert/registry', 'api/gateway'], 'inactivePluginEntries': False}, indent=2) + '\n')
            results.append(archive_candidate(dsh, 'dsh', output, 'linux-' + arch))
        if args.engine in ('opencode', 'both'):
            oc_source = work / 'opencode-source'
            export_source(args.opencode_repo, PINS['opencode'], oc_source, args.opencode_source_tar, args.opencode_source_sha256)
            print('stage: opencode frozen dependency install', flush=True)
            command([args.bun, 'install', '--frozen-lockfile'], oc_source, env)
            target = 'opencode-linux-' + arch + ('-baseline' if arch == 'x64' else '')
            print('stage: opencode upstream compiler', flush=True)
            command([args.bun, '--smol', 'run', 'packages/cli/script/build.ts', '--target=' + target, '--skip-install'] + (['--skip-web-ui'] if args.worker_only else []), oc_source, env)
            built = oc_source / 'packages/cli/dist' / target.replace('opencode', 'cli', 1)
            oc = work / 'opencode-candidate'
            shutil.copytree(built, oc, symlinks=True)
            shutil.copy2(oc_source / 'LICENSE', oc / 'LICENSE')
            command([oc / 'bin/opencode', '--version'], oc, env, True)
            api_smoke = smoke_opencode_api(oc / 'bin/opencode', work, env)
            (oc / 'runtime-manifest.json').write_text(json.dumps({'engine': 'opencode', 'sourceRevision': PINS['opencode'], 'version': '2.0.22', 'target': target, 'sourceArchiveSha256': args.opencode_source_sha256, 'keylessVersionSmoke': True, 'keylessApiSmoke': api_smoke, 'workerOnly': args.worker_only}, indent=2) + '\n')
            results.append(archive_candidate(oc, 'opencode', output, 'linux-' + arch))
        (output / ('worker-release-candidates-' + args.engine + '.json')).write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
