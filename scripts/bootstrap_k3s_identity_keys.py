#!/usr/bin/env python3
"""Initialize three K3s identity credential records in Vault KV v2."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROLES = ('root', 'operator', 'auditor')


def openssl(args, data):
    result = subprocess.run(['openssl', *args], input=data, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError('invalid client certificate or private key')
    return result.stdout


def validate_certificate(role, record):
    certificate = record['client_certificate']
    key = record['client_private_key']
    subject = openssl(['x509', '-noout', '-subject', '-nameopt', 'RFC2253'], certificate).strip()
    attributes = subject.removeprefix('subject=').split(',')
    if 'CN=' + role not in attributes or sum(x.startswith('CN=') for x in attributes) != 1:
        raise ValueError(role + ' certificate subject mismatch')
    if role != 'root' and 'O=system:masters' in attributes:
        raise ValueError(role + ' certificate must not use system:masters')
    openssl(['x509', '-noout', '-checkend', '0'], certificate)
    cert_public = openssl(['x509', '-pubkey', '-noout'], certificate)
    key_public = openssl(['pkey', '-pubout'], key)
    if cert_public != key_public:
        raise ValueError(role + ' certificate and private key do not match')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True, choices=('sit', 'uat', 'prod'))
    parser.add_argument('--cluster', required=True)
    parser.add_argument('--cluster-uid', required=True)
    for role in ROLES:
        parser.add_argument('--' + role + '-file', required=True, metavar='JSON')
    args = parser.parse_args()
    if not os.environ.get('VAULT_ADDR'):
        parser.error('VAULT_ADDR is required')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', args.cluster) or args.cluster == 'security':
        parser.error('cluster must be a lowercase DNS label other than security')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,127}', args.cluster_uid):
        parser.error('invalid cluster UID')
    try:
        records = {}
        for role in ROLES:
            record = json.loads(Path(getattr(args, role + '_file')).read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                raise ValueError(role + ' record must be a JSON object')
            if any(k in record for k in ('environment', 'cluster', 'cluster_uid', 'subject')):
                raise ValueError(role + ' record must not supply identity metadata')
            if record.get('auth_type') != 'client_certificate':
                raise ValueError(role + ' requires auth_type client_certificate')
            for key in ('client_private_key', 'client_certificate', 'owner', 'issued_at', 'rotation_due_at', 'credential_id'):
                if not isinstance(record.get(key), str) or not record[key].strip():
                    raise ValueError(role + ' missing ' + key)
            if set(record) != {'auth_type', 'client_private_key', 'client_certificate', 'owner', 'issued_at', 'rotation_due_at', 'credential_id'}:
                raise ValueError(role + ' contains unsupported fields')
            validate_certificate(role, record)
            record.update(environment=args.environment, cluster=args.cluster,
                          cluster_uid=args.cluster_uid, subject=role)
            records[role] = record
        for role in ROLES:
            path = f'{args.environment}/{args.cluster}/{role}'
            result = subprocess.run(['vault', 'kv', 'metadata', 'get', '-mount=kv', path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
            if result.returncode == 0:
                raise ValueError('destination already exists: kv/' + path)
            if result.returncode != 2 or 'No value found at ' not in result.stderr:
                raise ValueError('cannot confirm destination absence: kv/' + path)
        created = []
        for role in ROLES:
            path = f'{args.environment}/{args.cluster}/{role}'
            result = subprocess.run(['vault', 'kv', 'put', '-mount=kv', '-cas=0', path, '-'],
                                    input=json.dumps(records[role]), text=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if result.returncode:
                print('Write failed at kv/' + path + '; created: ' +
                      (', '.join(created) if created else 'none') + '. Inspect before retrying.', file=sys.stderr)
                return 1
            created.append('kv/' + path)
    except (OSError, UnicodeError, ValueError) as exc:
        print('Preflight failed: ' + str(exc), file=sys.stderr)
        return 1
    print(f'Initialized three identity records under kv/{args.environment}/{args.cluster}/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
