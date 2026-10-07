"""Exercise owner arguments against the fixed Accounts binary without a DB."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/data_operations/selfhost'))
import full_business_host as owner


def main():
    binary = os.environ['MIGRATECTL_BIN']
    spec = {'transfer': {'schema_sha256': owner.ACCOUNTS_SQL_SHA256,
                         'billing_schema_sha256': owner.BILLING_SQL_SHA256}}
    environment = os.environ.copy()
    environment.pop('NATIVE_SOURCE_DSN', None)
    environment.pop('NATIVE_TARGET_DSN', None)
    arguments = owner.migration_args(spec, 'core_users')
    for operation in ('copy-core-users', 'compare-core-users'):
        result = subprocess.run([binary, operation, *arguments[1:]],
                                env=environment, capture_output=True, text=True, timeout=10)
        assert result.returncode == 1, 'CLI must stop before opening a DB connection'
        assert 'source or target DSN environment variable is empty' in result.stderr, \
            'Fixed Accounts CLI rejected owner arguments before the DSN guard'
        assert 'unknown flag' not in result.stderr
    print('Fixed Accounts binary accepts owner core copy/compare arguments; no database accessed.')


if __name__ == '__main__':
    main()
