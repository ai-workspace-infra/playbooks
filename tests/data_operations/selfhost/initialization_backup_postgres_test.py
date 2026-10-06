#!/usr/bin/env python3
"""Disposable PostgreSQL encrypted initialization round trip, never live UAT."""
import json,os,pathlib,re,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
CONTAINER=os.environ.get('TEST_POSTGRES_CONTAINER','')


@unittest.skipUnless(os.environ.get('GITHUB_ACTIONS')=='true' and re.fullmatch(r'[0-9a-f]{64}',CONTAINER),'disposable CI service required')
class InitializationBackupRoundTrip(unittest.TestCase):
    def test_absent_ledger_empty_subscription_is_backed_up_without_qualification(self):
        real_docker=shutil.which('docker');db='ci_init_backup_'+os.environ['GITHUB_RUN_ID']
        def q(database,sql):
            r=subprocess.run([real_docker,'exec','-i',CONTAINER,'psql','-U','postgres','-d',database,'-XAtq','-v','ON_ERROR_STOP=1'],input=sql,text=True,capture_output=True,timeout=30)
            if r.returncode:raise AssertionError('Disposable initialization fixture failed')
            return r.stdout.strip()
        q('postgres','CREATE DATABASE '+db)
        try:
            q(db,"CREATE TABLE users(id bigserial PRIMARY KEY,password_hash text,quota bigint); INSERT INTO users(password_hash,quota) VALUES ('private-fixture-marker',777); CREATE TABLE subscriptions(id bigserial PRIMARY KEY); CREATE TABLE billing_ledger(id bigserial PRIMARY KEY,user_id bigint REFERENCES users(id),amount bigint); INSERT INTO billing_ledger(user_id,amount) VALUES (1,19);")
            with tempfile.TemporaryDirectory() as tmp:
                directory=pathlib.Path(tmp)
                (directory/'docker').write_text('#!/bin/bash\nset -euo pipefail\nargs=("$@")\nfor i in "${!args[@]}"; do\n if [[ ${args[$i]} == web-saas-postgresql ]]; then args[$i]="$TEST_POSTGRES_CONTAINER"; fi\n if [[ ${args[$i]} == account ]]; then args[$i]="$TEST_FIXTURE_DATABASE"; fi\ndone\nexec "$TEST_REAL_DOCKER" "${args[@]}"\n')
                (directory/'findmnt').write_text('#!/bin/bash\ncase "$3" in TARGET) echo /data;; FSTYPE) echo ext4;; SOURCE) echo /dev/ci-backup-disk;; *) exit 1;; esac\n')
                (directory/'readlink').write_text('#!/bin/bash\nif [[ "$2" == /dev/ci-backup-disk || "$2" == /dev/disk/by-id/google-web-saas-uat-upgrade-data ]]; then echo /dev/ci-backup-disk; else exec /usr/bin/readlink "$@"; fi\n')
                for path in directory.iterdir():path.chmod(0o700)
                env=dict(os.environ,PATH=tmp+':'+os.environ['PATH'],TEST_REAL_DOCKER=real_docker,TEST_FIXTURE_DATABASE=db)
                r=subprocess.run(['bash',str(ROOT/'scripts/data_operations/selfhost/initialization_backup_host.sh')],input='disposable-only-encryption-key-32-characters\n',text=True,capture_output=True,env=env,timeout=90)
                self.assertEqual(r.returncode,0,'Initialization round trip failed; fixture output withheld')
                receipt=json.loads(r.stdout.strip().splitlines()[-1]);archive=pathlib.Path(receipt['archive_path'])
                self.assertTrue(receipt['restored_data_matches']);self.assertTrue(receipt['isolated_restore_verified'])
                self.assertFalse(receipt['business_acceptance']);self.assertEqual(receipt['migration_ledger'],'absent');self.assertEqual(receipt['subscriptions'],0)
                self.assertNotIn(b'private-fixture-marker',archive.read_bytes());self.assertEqual(archive.stat().st_mode&0o777,0o600)
                self.assertEqual(q(db,'SELECT quota FROM users WHERE id=1'),'777')
                self.assertEqual(q('postgres',"SELECT count(*) FROM pg_database WHERE datname LIKE 'release_initialization_verify_%'"),'0')
        finally:q('postgres','DROP DATABASE '+db)


if __name__=='__main__':unittest.main()
