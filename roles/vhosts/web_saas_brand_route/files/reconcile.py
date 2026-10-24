#!/usr/bin/env python3
"""Preserve existing Caddy bytes; validate, restart only Caddy, probe and rollback."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

BEGIN = '# BEGIN managed web-saas brand route\n'
END = '# END managed web-saas brand route\n'


def command(*argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('owner command failed: ' + argv[0])
    return result.stdout


def render(current, domain):
    if current.count(BEGIN) > 1 or current.count(END) > 1:
        raise ValueError('ambiguous managed block')
    base = current
    if BEGIN in current:
        start = current.index(BEGIN)
        stop = current.index(END, start) + len(END)
        base = current[:start] + current[stop:]
    elif END in current:
        raise ValueError('incomplete managed block')
    if re.search(r'(?<![A-Za-z0-9.-])' + re.escape(domain) + r'(?![A-Za-z0-9.-])', base):
        raise ValueError('brand hostname already has an unmanaged route')
    if not base.endswith('\n'):
        raise ValueError('existing config must end with a newline')
    tls = '/etc/xcontrol/tls/' + domain + '/current/'
    block = (BEGIN + 'https://' + domain + ' {\n'
             '\ttls ' + tls + 'fullchain.pem ' + tls + 'key.pem\n'
             '\tencode zstd gzip\n\treverse_proxy console:3000\n}\n' + END)
    return base + block, base


def containers(excluded):
    names = command('docker', 'ps', '--format', '{{.Names}}').splitlines()
    return {name: command('docker', 'inspect', '--format', '{{.Id}} {{.State.StartedAt}}', name).strip()
            for name in names if name != excluded}


def probe(domain):
    return command('curl', '--silent', '--show-error', '--connect-timeout', '5', '--max-time', '15',
                   '--noproxy', '*',
                   '--resolve', domain + ':443:127.0.0.1', '--output', '/dev/null',
                   '--write-out', '%{http_code}', 'https://' + domain + '/').strip()


def verify_certificate(path, domain):
    hostname_check = command('openssl', 'x509', '-in', path, '-checkhost', domain, '-noout')
    if 'does match certificate' not in hostname_check:
        raise ValueError('brand certificate hostname mismatch')
    # OpenSSL treats -checkend as an early-return operation; do not combine it
    # with -checkhost or the hostname match result is never produced.
    command('openssl', 'x509', '-in', path, '-checkend', '1209600', '-noout')


def replace(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o644)
        stream.write(data.encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def reconcile(args):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+\.[a-z]{2,}', args.domain):
        raise ValueError('invalid domain')
    config = Path(args.config)
    if str(config) != '/etc/xcontrol/web-saas/Caddyfile' or config.is_symlink():
        raise ValueError('explicit managed config required')
    with open(str(config) + '.brand.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = config.read_text()
        candidate, base = render(current, args.domain)
        tls = '/etc/xcontrol/tls/' + args.domain + '/current/cert.pem'
        verify_certificate(tls, args.domain)
        before = {domain: probe(domain) for domain in args.probe_domains.split(',')}
        peers = containers(args.container)
        receipt = dict(changed=candidate != current, existing_domains=before,
                       base_sha256=hashlib.sha256(base.encode()).hexdigest())
        if candidate == current:
            receipt.update(brand_status=probe(args.domain), peers_unchanged=True)
            return receipt
        backup = config.parent / 'backups' / ('Caddyfile.' + hashlib.sha256(current.encode()).hexdigest())
        backup.parent.mkdir(mode=0o700, exist_ok=True)
        if not backup.exists():
            backup.write_text(current)
            os.chmod(backup, 0o600)
        staged = None
        container_stage = '/tmp/Caddyfile-brand-validation'
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, delete=False) as stream:
                stream.write(candidate)
                staged = Path(stream.name)
            command('docker', 'cp', str(staged), args.container + ':' + container_stage)
            command('docker', 'exec', args.container, 'caddy', 'validate', '--config', container_stage, '--adapter', 'caddyfile')
            replace(config, candidate)
            try:
                command('docker', 'restart', '--time', '10', args.container)
                for attempt in range(10):
                    try:
                        brand = probe(args.domain)
                        after = {domain: probe(domain) for domain in before}
                        if brand not in ('200', '301', '302', '307', '308') or after != before:
                            raise RuntimeError('domain status changed')
                        if containers(args.container) != peers:
                            raise RuntimeError('other service process changed')
                        receipt.update(brand_status=brand, existing_domains=after, peers_unchanged=True,
                                       config_sha256=hashlib.sha256(candidate.encode()).hexdigest(), backup=str(backup))
                        return receipt
                    except RuntimeError:
                        if attempt == 9:
                            raise
                        time.sleep(1)
            except Exception:
                replace(config, current)
                command('docker', 'restart', '--time', '10', args.container)
                raise RuntimeError('brand deployment failed; previous Caddy config restored')
        finally:
            if staged:
                staged.unlink(missing_ok=True)
            subprocess.run(['docker', 'exec', args.container, 'rm', '-f', container_stage], capture_output=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--probe-domains', required=True)
    try:
        print(json.dumps(reconcile(parser.parse_args()), sort_keys=True))
    except Exception as error:
        print('Brand route reconciliation failed: ' + str(error), file=__import__('sys').stderr)
        raise SystemExit(1)
