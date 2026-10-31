#!/usr/bin/env python3
"""Master-local K3s client issuance and create-only Vault KV v2 archival."""
import argparse
import base64
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROLES = ('root', 'operator', 'auditor')


def run(*args, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise ValueError('command failed: ' + args[0])
    return result.stdout.strip()


def private_write(path, value):
    # Refuse to follow existing symlinks; never relax permissions on a target.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def directory(path):
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    stat = path.lstat()
    if path.is_symlink() or not path.is_dir() or stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
        raise ValueError('unsafe private state directory')


class Vault:
    def __init__(self, address, token):
        url = urllib.parse.urlsplit(address)
        if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('VAULT_ADDR must be an HTTPS origin')
        if not token:
            raise ValueError('runtime VAULT_TOKEN is required')
        self.address, self.token = address.rstrip('/'), token
        # No redirect handler: never forward the scoped credential to a redirect.
        self.client = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=os.environ.get('VAULT_CACERT') or None)),
            NoRedirect())

    def request(self, path, payload=None):
        request = urllib.request.Request(self.address + '/v1/' + path,
                                        data=None if payload is None else json.dumps(payload).encode(),
                                        headers={'X-Vault-Token': self.token, 'Content-Type': 'application/json'})
        try:
            with self.client.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and payload is None:
                return None
            raise ValueError('Vault request failed with HTTP ' + str(exc.code)) from None
        except (OSError, ValueError):
            raise ValueError('Vault request failed') from None

    def archive(self, path, record):
        existing = self.request('kv/data/' + path)
        if existing is not None:
            if existing['data']['data'] != record:
                raise ValueError('Vault destination differs; explicit rotation required: kv/' + path)
            return False
        # CAS=0 also refuses tombstoned records and concurrent writers.
        self.request('kv/data/' + path, {'options': {'cas': 0}, 'data': record})
        verified = self.request('kv/data/' + path)
        if verified is None or verified['data']['data'] != record:
            raise ValueError('Vault read-back verification failed')
        return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify(role, folder, client_ca):
    cert, key = folder / 'client.crt', folder / 'client.key'
    run('openssl', 'verify', '-purpose', 'sslclient', '-CAfile', str(client_ca), str(cert))
    run('openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '300')
    subject = run('openssl', 'x509', '-in', str(cert), '-noout', '-subject', '-nameopt', 'RFC2253')
    if subject.removeprefix('subject=').strip() != 'CN=' + role:
        raise ValueError('unexpected certificate subject or group')
    public = run('openssl', 'x509', '-in', str(cert), '-pubkey', '-noout')
    if public != run('openssl', 'pkey', '-in', str(key), '-pubout'):
        raise ValueError('certificate private key mismatch')


def issue(role, folder, ca, ca_key, days):
    run('openssl', 'x509', '-in', str(ca), '-noout', '-checkend', str(days * 86400))
    run('openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', str(folder / 'client.key'))
    os.chmod(folder / 'client.key', 0o600)
    run('openssl', 'req', '-new', '-key', str(folder / 'client.key'), '-subj', '/CN=' + role, '-out', str(folder / 'client.csr'))
    private_write(folder / 'extensions.cnf', 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n')
    run('openssl', 'x509', '-req', '-in', str(folder / 'client.csr'), '-CA', str(ca), '-CAkey', str(ca_key),
        '-set_serial', '0x' + secrets.token_hex(16), '-days', str(days), '-sha256',
        '-extfile', str(folder / 'extensions.cnf'), '-out', str(folder / 'client.crt'))
    verify(role, folder, ca)


def prepare(args, role):
    folder = args.state_dir / role
    directory(folder)
    record_file = folder / 'record.json'
    binding = {'environment': args.environment, 'cluster': args.cluster, 'cluster_uid': args.cluster_uid, 'subject': role}
    if record_file.exists():
        record = json.loads(record_file.read_text())
        if any(record.get(k) != v for k, v in binding.items()):
            raise ValueError('local credential target differs')
        verify(role, folder, args.client_ca)
        if record['client_certificate'] != (folder / 'client.crt').read_text() or record['client_private_key'] != (folder / 'client.key').read_text():
            raise ValueError('local record and credentials differ')
        if record['kubeconfig'] != (folder / 'kubeconfig').read_text():
            raise ValueError('local kubeconfig differs')
        config = json.loads(record['kubeconfig'])
        if config['clusters'][0]['cluster'] != {'server': args.api_server, 'certificate-authority-data': base64.b64encode(args.server_ca.read_bytes()).decode()}:
            raise ValueError('API endpoint or server trust changed')
        return record
    # An interrupted issuance is not silently overwritten; an admin must inspect it.
    if any(folder.iterdir()):
        raise ValueError('incomplete local issuance; inspect state directory')
    issue(role, folder, args.client_ca, args.client_ca_key, args.days)
    record = dict(binding, auth_type='client_certificate', client_private_key=(folder / 'client.key').read_text(),
                  client_certificate=(folder / 'client.crt').read_text(), owner='k3s-platform-bootstrap',
                  credential_id=run('openssl', 'x509', '-in', str(folder / 'client.crt'), '-noout', '-serial'),
                  issued_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  rotation_due_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=args.days - 1)).isoformat())
    config = {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': args.cluster, 'cluster': {
        'server': args.api_server, 'certificate-authority-data': base64.b64encode(args.server_ca.read_bytes()).decode()}}],
        'users': [{'name': role, 'user': {'client-certificate-data': base64.b64encode(record['client_certificate'].encode()).decode(),
                                       'client-key-data': base64.b64encode(record['client_private_key'].encode()).decode()}}],
        'contexts': [{'name': role, 'context': {'cluster': args.cluster, 'user': role}}], 'current-context': role}
    record['kubeconfig'] = json.dumps(config)
    private_write(folder / 'kubeconfig', record['kubeconfig'])
    private_write(record_file, json.dumps(record))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=('sit', 'uat', 'prod'), required=True)
    parser.add_argument('--cluster', required=True)
    parser.add_argument('--cluster-uid', required=True)
    parser.add_argument('--api-server', required=True)
    parser.add_argument('--admin-kubeconfig', required=True)
    parser.add_argument('--client-ca', type=Path, required=True)
    parser.add_argument('--client-ca-key', type=Path, required=True)
    parser.add_argument('--server-ca', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError('run on the master as root')
        os.umask(0o077)
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', args.cluster) or args.cluster == 'security':
            raise ValueError('invalid cluster name')
        if not 2 <= args.days <= 30:
            raise ValueError('certificate lifetime must be 2..30 days')
        if not args.api_server.startswith('https://'):
            raise ValueError('HTTPS Kubernetes endpoint required')
        actual = run('kubectl', '--kubeconfig', args.admin_kubeconfig, 'get', 'namespace', 'kube-system', '-o', 'jsonpath={.metadata.uid}')
        if actual != args.cluster_uid or not actual:
            raise ValueError('actual cluster UID differs')
        vault = Vault(os.environ.get('VAULT_ADDR', ''), os.environ.get('VAULT_TOKEN', ''))
        directory(args.state_dir)
        with os.fdopen(os.open(args.state_dir / 'bootstrap.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            records = {role: prepare(args, role) for role in ROLES}
            changes = []
            for role, record in records.items():
                if vault.archive(args.environment + '/' + args.cluster + '/' + role, record):
                    changes.append(role)
        print(json.dumps({'result': 'CREDENTIALS_ARCHIVED', 'cluster_uid': actual, 'created': changes}))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Identity bootstrap failed; inspect target, trust, Vault access and protected local state. Existing credentials were not rotated.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
