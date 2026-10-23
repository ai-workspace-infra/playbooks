#!/usr/bin/env python3
"""Target-only persistent listener config and actual process/SNI verification."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def require(value):
    if not value:
        raise ValueError('Candidate listener target guard failed')


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    require(result.returncode == 0)
    return result.stdout.strip()


def private(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts and path.resolve() == path)
    for parent in (path, *path.parents):
        info = parent.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022 and not parent.is_symlink())
    require(path.is_file() and path.stat().st_mode & 0o077 == 0)
    return path


def write(path, value):
    if path.exists():
        private(path)
        if path.read_text() == value:
            return False
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        os.fchmod(handle.fileno(), 0o600)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return True


def route_config(domain, cidrs):
    return '@migration_api {\n host ' + domain + '\n path /api/migrations/v1 /api/migrations/v1/*\n}\nhandle @migration_api {\n' + \
        ' @migration_allowed remote_ip ' + ' '.join(cidrs) + '\n handle @migration_allowed {\n' + \
        '  reverse_proxy 127.0.0.1:9097\n }\n respond 403\n}\n'


def execute(request):
    validated, values = request['validated'], request['values']
    contract = validated['contract']
    cfg = validated['declaration']['listener']
    root = Path('/etc/xcontrol/migration-api')
    state = Path(contract['resolved']['data_disk']['data_path']).parent / 'control-plane-owner.json'
    record = json.loads(private(state).read_text())
    require(record.get('initialized') is True and record['binding']['target'] == contract['target']['host'] and
            record['binding']['environment'] == contract['environment'] and
            record['binding']['accounts'] == contract['release']['accounts'] and
            record['binding']['resource_id'] == contract['resolved']['resource_id'] and
            record['binding']['services'] == contract['release']['services'] and
            record.get('billing_schema_sha256') == contract['release']['accounts']['billing_schema_sha256'])
    require(contract['execution']['mode'] == 'candidate')
    if cfg['apply_enabled']:
        recovery = validated['recovery']
        receipt = private(recovery['backup_receipt'])
        proof_file = private(recovery['owner_proof'])
        require(hashlib.sha256(receipt.read_bytes()).hexdigest() == recovery['backup_receipt_sha256'] and
                hashlib.sha256(proof_file.read_bytes()).hexdigest() == recovery['owner_proof_sha256'])
        proof = json.loads(proof_file.read_text())
        require(proof['owner_sha'] == recovery['owner_sha'] and proof['plan_id'] == recovery['plan_id'] and
                proof['plan_sha256'] == recovery['plan_sha256'] and proof['receipt'] == json.loads(receipt.read_text()) and
                proof['target_writers_paused'] is True and proof['kind'] == 'playbooks-api-backup-proof')
        archive = private(recovery['backup_archive'])
        require(str(archive) == proof['backup']['archive_path'])
        # Reuse the actual native backup/restore evidence adapter rather than
        # accepting a JSON boolean. It rehashes the archive and verifies DB identity.
        import api_backup_receipt
        plan = dict(proof['fresh_plan'], plan_id=proof['plan_id'], plan_sha256=proof['plan_sha256'])
        fresh, _ = api_backup_receipt.build({'backup': proof['backup'], 'restore': proof['restore'],
            'plan': plan, 'fresh_plan': proof['fresh_plan'], 'plan_id': proof['plan_id'],
            'plan_sha256': proof['plan_sha256'], 'owner_sha': proof['owner_sha']})
        for key in ('backup_sha256', 'target_identity_sha256', 'target_schema_sha256', 'target_baseline_sha256'):
            require(fresh[key] == proof['receipt'][key])
        from datetime import datetime, timezone
        require(datetime.fromisoformat(proof['receipt']['expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc))
    if request['action'] == 'prepare':
        require(root.parent.resolve() == root.parent and root.parent.stat().st_uid == 0 and
                root.parent.stat().st_mode & 0o022 == 0)
        root.mkdir(mode=0o700, exist_ok=True)
        require(root.resolve() == root and root.stat().st_uid == 0 and root.stat().st_mode & 0o077 == 0)
        stage = Path(validated['stage'])
        require(stage.parent.resolve() == stage.parent)
        stage.mkdir(mode=0o700, exist_ok=True)
        require(stage.resolve() == stage and stage.stat().st_uid == 0 and stage.stat().st_mode & 0o077 == 0)
        changed = write(root / 'listener.env', ''.join(key + '=' + json.dumps(value) + '\n' for key, value in sorted(values.items())))
        route = route_config(contract['target']['accounts_domain'], cfg['allowed_client_cidrs'])
        changed = write(root / 'route.caddy', route) or changed
        return {'changed': changed}
    require(request['action'] in ('verify', 'refresh'))
    info = json.loads(command(['docker', 'inspect', 'web-saas-migration-listener']))[0]
    caddy = json.loads(command(['docker', 'inspect', 'web-saas-caddy']))[0]
    require(info['State']['Running'] and info['Config']['Image'] == contract['release']['services']['accounts'] and
            info['HostConfig']['NetworkMode'] == 'container:' + caddy['Id'] and
            info['Config']['Entrypoint'] == ['/usr/local/bin/migratectl'] and info['Config']['Cmd'] == request['arguments'])
    actual = dict(line.split('=', 1) for line in info['Config'].get('Env', []) if '=' in line)
    require(all(actual.get(k) == v for k, v in values.items()))
    if request['action'] == 'refresh':
        # Validate the new bind contents before restarting. Doco-CD owns Compose;
        # this is only the native changed-config reload, never a new definition.
        command(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--volumes-from', 'web-saas-caddy', '--mount',
            'type=bind,source=' + str(root / 'route.caddy') + ',target=/etc/caddy/migration-listener.caddy,readonly',
            '--entrypoint', 'caddy', caddy['Config']['Image'], 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
        command(['docker', 'restart', 'web-saas-caddy'])
        command(['docker', 'restart', 'web-saas-migration-listener'])
        return {'changed': True}
    command(['docker', 'exec', 'web-saas-caddy', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
    # Unauthenticated capability probe must reach this API and refuse auth.
    # No POST, token generation, custom trust bypass or redirects.
    domain = contract['target']['accounts_domain']
    code = command(['docker', 'exec', 'web-saas-migration-listener', 'curl', '--silent', '--show-error', '--noproxy', '*', '--proto', '=https', '--max-time', '15',
        '--max-redirs', '0', '--resolve', domain + ':443:127.0.0.1', '--output', '/dev/null', '--write-out', '%{http_code}',
        'https://' + domain + '/api/migrations/v1/plans/00000000-0000-4000-8000-000000000001'])
    require(code == '401')
    return {'listener_verified': True, 'source_actions': False, 'business_acceptance': False}


if __name__ == '__main__':
    try:
        print(json.dumps(execute(json.load(sys.stdin))))
    except Exception:
        print('Target listener operation refused; private values withheld.', file=sys.stderr)
        sys.exit(2)
