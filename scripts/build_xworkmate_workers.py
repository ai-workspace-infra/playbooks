#!/usr/bin/env python3
"""Build pinned Linux release candidates. No publish, deploy, or model calls.

Run in a disposable Linux build host with reviewed dependency install scripts.
Only git-tracked pinned sources are copied. Ambient credentials are not inherited.
The output is a candidate; Linux ACP/SDK/server acceptance precedes publication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile

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


def export_source(repository, revision, destination):
    # No working-tree files, .env, auth cache, or existing node_modules are copied.
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
    parser.add_argument('--dsh-repo', type=Path, required=True)
    parser.add_argument('--opencode-repo', type=Path, required=True)
    parser.add_argument('--linux-node-archive', type=Path, required=True)
    parser.add_argument('--linux-node-sha256', required=True)
    parser.add_argument('--pnpm', type=Path, required=True, help='preinstalled pnpm 11.7.0 executable')
    parser.add_argument('--bun', type=Path, required=True, help='preinstalled Bun 1.4.2 executable')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(platform.system() == 'Linux', 'native Linux build host required')
    arch = {'x86_64': 'x64', 'aarch64': 'arm64'}.get(platform.machine())
    require(arch is not None, 'only Linux x64 and arm64 glibc build hosts supported')
    require(re.fullmatch('[0-9a-f]{64}', args.linux_node_sha256) is not None,
            'supply the verified Node Linux archive checksum')
    require(digest(args.linux_node_archive) == args.linux_node_sha256, 'Node archive checksum mismatch')
    for tool in (args.pnpm, args.bun):
        require(tool.is_absolute() and tool.is_file() and os.access(tool, os.X_OK), 'explicit executable tool paths required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='xworkmate-worker-release-') as temporary:
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
               'OPENCODE_VERSION': '2.0.22', 'OPENCODE_DISABLE_MODELS_FETCH': '1'}
        node_version = command([node_root / 'bin/node', '--version'], work, env, True).strip()
        require(re.fullmatch(r'v(?:22\.(?:19|[2-9][0-9])\.[0-9]+|(?:2[4-9]|[3-9][0-9])\.[0-9]+\.[0-9]+)', node_version), 'DSH requires Node >=22.19 except Node 23')
        node_arch = command([node_root / 'bin/node', '-p', 'process.arch'], work, env, True).strip()
        require(node_arch == arch, 'Node archive architecture mismatch')
        require(command([args.pnpm, '--version'], work, env, True).strip() == '11.7.0', 'pnpm must be exactly 11.7.0')
        require(command([args.bun, '--version'], work, env, True).strip() == '1.4.2', 'Bun must be exactly 1.4.2')
        dsh = work / 'dsh-candidate'
        export_source(args.dsh_repo, PINS['dsh'], dsh / 'repo')
        command([args.pnpm, 'install', '--frozen-lockfile'], dsh / 'repo', env)
        command([args.pnpm, 'run', 'build'], dsh / 'repo', env)
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
        (dsh / 'runtime-manifest.json').write_text(json.dumps({'engine': 'dsh', 'sourceRevision': PINS['dsh'], 'nodeVersion': node_version}, indent=2) + '\n')
        oc_source = work / 'opencode-source'
        export_source(args.opencode_repo, PINS['opencode'], oc_source)
        command([args.bun, 'install', '--frozen-lockfile'], oc_source, env)
        target = 'opencode-linux-' + arch + ('-baseline' if arch == 'x64' else '')
        command([args.bun, 'run', 'packages/cli/script/build.ts', '--target=' + target, '--skip-install'], oc_source, env)
        built = oc_source / 'packages/cli/dist' / target.replace('opencode', 'cli', 1)
        oc = work / 'opencode-candidate'
        shutil.copytree(built, oc, symlinks=True)
        shutil.copy2(oc_source / 'LICENSE', oc / 'LICENSE')
        command([oc / 'bin/opencode', '--version'], oc, env, True)
        (oc / 'runtime-manifest.json').write_text(json.dumps({'engine': 'opencode', 'sourceRevision': PINS['opencode'], 'version': '2.0.22', 'target': target}, indent=2) + '\n')
        results = [archive_candidate(dsh, 'dsh', output, 'linux-' + arch), archive_candidate(oc, 'opencode', output, 'linux-' + arch)]
        (output / 'worker-release-candidates.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
