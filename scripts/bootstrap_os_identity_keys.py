#!/usr/bin/env python3
"""Initialize one environment's OS SSH public keys without overwriting Vault data."""
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
        for first, second in (('root', 'operator'), ('root', 'auditor'), ('operator', 'auditor')):
            if fingerprints[first] & fingerprints[second]:
                raise ValueError('public keys must be distinct across roles')
        result = subprocess.run(['vault', 'kv', 'put', '-mount=kv', '-cas=0',
                                 args.environment + '/security', '-'],
                                input=json.dumps(records), text=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            print('Vault initialization failed; verify access and that the record does not already exist.',
                  file=sys.stderr)
            return 1
    except (OSError, UnicodeError, ValueError) as exc:
        print('Preflight failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Initialized kv/' + args.environment + '/security with root, operator and auditor public keys.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
