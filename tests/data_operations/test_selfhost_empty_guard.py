"""Empty init fails on unavailable SQL, any application objects or wrong PROD storage."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/data_operations/selfhost/init_guard_host.sh'


class EmptyInitializationGuardTests(unittest.TestCase):
    def execute(self, environment='uat', **changes):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)
            (binary / 'docker').write_text('''#!/bin/bash
set -eu
if [ "$1" = inspect ]; then
 case "$*" in *State.Status*) printf '%s\\n' "$CONTAINER_STATE" ;; *) printf '%s\\n' "$DATA_BIND" ;; esac
elif [[ "$*" == *pg_database* ]]; then
 [ "$EXISTENCE_ERROR" = 0 ] || exit 2
 printf '%s\\n' "$DATABASE_PRESENT"
else
 [ "$OBJECT_ERROR" = 0 ] || exit 2
 printf '%s\\n' "$OBJECT_COUNT"
fi
''')
            (binary / 'findmnt').write_text('''#!/bin/bash
case "$*" in *TARGET,FSTYPE*) printf '%s\\n' "$MOUNT_TARGET_TYPE" ;; *) printf '%s\\n' "$MOUNT_SOURCE" ;; esac
''')
            (binary / 'readlink').write_text('''#!/bin/bash
if [[ "${!#}" == /dev/disk/by-id/* ]]; then echo /dev/sdb; else printf '%s\\n' "${!#}"; fi
''')
            for path in binary.iterdir():
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(binary)+':'+os.environ['PATH'],
                       CONTAINER_STATE='running', DATA_BIND='bind:/data/postgresql',
                       EXISTENCE_ERROR='0', DATABASE_PRESENT='1', OBJECT_ERROR='0', OBJECT_COUNT='0',
                       MOUNT_TARGET_TYPE='/data ext4', MOUNT_SOURCE='/dev/sdb')
            env.update(changes)
            return subprocess.run(['bash', str(SCRIPT), environment], env=env,
                                  text=True, capture_output=True)

    def test_verified_absent_and_empty_database(self):
        for value in ('0', '1'):
            with self.subTest(value=value):
                self.assertEqual(self.execute(DATABASE_PRESENT=value).returncode, 0)

    def test_sql_failure_never_means_empty(self):
        for field in ('EXISTENCE_ERROR', 'OBJECT_ERROR'):
            with self.subTest(field=field):
                self.assertNotEqual(self.execute(**{field:'1'}).returncode, 0)

    def test_nonempty_or_unknown_object_count(self):
        for count in ('1', '25', '', 'unknown'):
            with self.subTest(count=count):
                self.assertNotEqual(self.execute(OBJECT_COUNT=count).returncode, 0)

    def test_unknown_database_presence(self):
        for value in ('', '2', 'unknown'):
            with self.subTest(value=value):
                self.assertNotEqual(self.execute(DATABASE_PRESENT=value).returncode, 0)

    def test_prod_requires_exact_independent_bind(self):
        self.assertEqual(self.execute('prod').returncode, 0)
        for changes in ({'DATA_BIND':'volume:/var/lib/docker/volumes/data'},
                        {'MOUNT_SOURCE':'/dev/sda'}, {'MOUNT_TARGET_TYPE':'/ xfs'},
                        {'DATA_BIND':'bind:/data/wrong'}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.execute('prod', **changes).returncode, 0)

    def test_container_must_be_running(self):
        self.assertNotEqual(self.execute(CONTAINER_STATE='exited').returncode, 0)


if __name__ == '__main__':
    unittest.main()
