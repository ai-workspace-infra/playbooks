#!/usr/bin/env python3
"""Initialize environment OS SSH key records without overwriting Vault data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def public_keys(path):
    lines = [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if not lines:
        raise ValueError('public key file is empty')
    fingerprints = []
    for line in lines:
        result = subprocess.run(['ssh-keygen', '-lf', '-'], input=line + '\n',
                                text=True, capture_output=True, check=False)
        if result.returncode != 0 or len(result.stdout.split()) < 2:
            raise ValueError('invalid SSH public key')
        fingerprints.append(result.stdout.split()[1])
    return '\n'.join(lines), set(fingerprints)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True, choices=('sit', 'uat', 'prod'))
    for name in ('root', 'operator', 'auditor'):
        parser.add_argument('--' + name + '-public-keys', required=True, metavar='FILE')
    args = parser.parse_args()
    if not os.environ.get('VAULT_ADDR'):
        parser.error('VAULT_ADDR is required')
    try:
        records, fingerprints = {}, {}
        for name in ('root', 'operator', 'auditor'):
            records[name], fingerprints[name] = public_keys(getattr(args, name + '_public_keys'))
        shared = any(fingerprints[first] & fingerprints[second] for first, second in
                     (('root', 'operator'), ('root', 'auditor'), ('operator', 'auditor')))
        if shared:
            print('Warning: shared SSH keys can authenticate to multiple roles; '
                  'role separation does not isolate key holders.', file=sys.stderr)
        # Confirm every destination is absent before the first write. Permission
        # and network errors are not evidence of absence; CAS still protects
        # each write against a concurrent creation after this preflight.
        for name in ('root', 'operator', 'auditor'):
            path = args.environment + '/security/' + name
            result = subprocess.run(['vault', 'kv', 'metadata', 'get', '-mount=kv', path],
                                    text=True, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, check=False)
            if result.returncode == 0:
                raise ValueError('destination already exists: kv/' + path)
            if result.returncode != 2 or 'No value found at ' not in result.stderr:
                raise ValueError('cannot confirm destination absence: kv/' + path)
        created = []
        for name in ('root', 'operator', 'auditor'):
            path = args.environment + '/security/' + name
            result = subprocess.run(['vault', 'kv', 'put', '-mount=kv', '-cas=0', path, '-'],
                                    input=json.dumps({'authorized_keys': records[name]}), text=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
            if result.returncode:
                print('Vault initialization failed at kv/' + path + '; created: ' +
                      (', '.join(created) if created else 'none') +
                      '. Inspect these paths before retrying.', file=sys.stderr)
                return 1
            created.append('kv/' + path)
    except (OSError, UnicodeError, ValueError) as exc:
        print('Preflight failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Initialized root, operator and auditor public-key records under kv/' + args.environment + '/security/.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
