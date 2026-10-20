"""No deployment path may regenerate alternate business credentials."""
import importlib.util
from pathlib import Path
import unittest
import urllib.parse
import yaml

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('business_database', ROOT/'roles/web_saas_business_database/files/business_database.py')
db=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(db)


class BusinessDatabaseContractTests(unittest.TestCase):
    def spec(self):
        return dict(target='web-saas-prod-v4', action='prepare', password='fixture-only', contract=dict(
            role='account_user',database='account',host='postgres',port=5432,sslmode='disable',backend_rls_bypass=True,
            credential=dict(vault_path='kv/prod/databases',key='account_pg_password')))

    def test_only_the_existing_business_identity_is_allowed(self):
        db.validate(self.spec())
        for role in (*db.RETIRED, 'postgres', 'readonly_release'):
            spec=self.spec();spec['contract']['role']=role
            with self.assertRaises(ValueError):db.validate(spec)

    def test_no_wrong_credential_owner_or_multiline_fallback(self):
        spec=self.spec();spec['contract']['credential']['key']='postgres_root_password'
        with self.assertRaises(ValueError):db.validate(spec)
        for password in ('', 'line\nbreak', 'null\0value'):
            spec=self.spec();spec['password']=password
            with self.assertRaises(ValueError):db.validate(spec)

    def test_environment_encodes_special_characters_without_background_override(self):
        password="fixt$'\\%\"!@/plus"
        env=dict(line.split('=',1) for line in db.env_content(password).splitlines())
        uri=urllib.parse.urlparse(env['DATABASE_URL'])
        self.assertEqual(uri.username,'account_user')
        self.assertEqual(urllib.parse.unquote(uri.password),password)
        self.assertEqual(uri.hostname,'postgres')
        self.assertNotIn('DATABASE_BACKGROUND_WRITERS',env)

    def test_no_alternate_role_generation_or_env_injection_survives(self):
        for folder in ('web_saas_generation_recovery','web_saas_generation_fresh',
                       'web_saas_generation_refresh','web_saas_stripe_sandbox'):
            for file in (ROOT/'roles'/folder).rglob('*'):
                if file.suffix not in ('.py','.yml','.j2'):continue
                text=file.read_text()
                for role in db.RETIRED:
                    self.assertNotIn(role,text,str(file))
                self.assertNotIn('RECOVERY_APP_PASSWORD',text,str(file))
                self.assertNotIn('generation-app.env',text,str(file))
                self.assertNotIn('generation-auth.env',text,str(file))

    def test_secret_read_and_execution_are_suppressed_in_logs(self):
        tasks=yaml.safe_load((ROOT/'roles/web_saas_business_database/tasks/main.yml').read_text())
        for name in ('Read the existing business password from Vault only at execution',
                     'Prepare or retire only the declared application identity'):
            task=next(t for t in tasks if t['name']==name)
            self.assertTrue(task['no_log'])


if __name__=='__main__':unittest.main()
