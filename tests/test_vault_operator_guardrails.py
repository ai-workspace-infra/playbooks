import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / "roles/vhosts/vault/files/init_vault_admin.sh"
DATA = ROOT / "roles/vhosts/vault/files/vault_operator_data.sh"
ROLE = (ROOT / "roles/vhosts/vault/tasks/main.yml").read_text()


class VaultOperatorGuardrailsTests(unittest.TestCase):
    def test_admin_setup_rejects_secret_arguments_and_never_rotates_totp(self):
        script = ADMIN.read_text()
        self.assertNotIn("identity/mfa/method/totp/admin-destroy", script)
        self.assertIn("enforcement_json", script)
        self.assertIn("umask 077", script)
        self.assertIn("VAULT_ADMIN_PASSWORD", ROLE)
        self.assertNotIn("--password {{", ROLE)
        self.assertNotIn("--root-token {{", ROLE)
        result = subprocess.run(["bash", str(ADMIN), "--password", "test"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Refusing secret command-line arguments", result.stderr)

    def test_admin_first_enrollment_outputs_private_qr_and_rerun_preserves_totp(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            fake_vault = location / "vault"
            fake_vault.write_text(r'''#!/usr/bin/env python3
import base64
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
command = " ".join(args)
with open(os.environ["MOCK_VAULT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(command + "\n")
if args[:2] == ["auth", "list"]:
    print(json.dumps({"userpass/": {"accessor": "auth_userpass_1"}}))
elif args[:2] == ["secrets", "list"]:
    print(json.dumps({"kv/": {}}))
elif args[:2] == ["list", "-format=json"]:
    print(json.dumps(["method-1"] if args[2] == "identity/mfa/method/totp" else ["alias-1"]))
elif args[:2] == ["read", "-format=json"]:
    path = args[2]
    if path == "identity/mfa/method/totp/method-1":
        print(json.dumps({"data": {"method_name": "vault-admin-totp"}}))
    elif path == "identity/entity/name/admin":
        print(json.dumps({"data": {"id": "entity-1"}}))
    elif path == "identity/entity-alias/id/alias-1":
        print(json.dumps({"data": {"name": "admin", "mount_accessor": "auth_userpass_1", "canonical_id": "entity-1"}}))
    elif path == "identity/mfa/login-enforcement/admin-userpass":
        if not Path(os.environ["MOCK_ENFORCEMENT"]).exists():
            sys.exit(2)
        print(json.dumps({"data": {"mfa_method_ids": ["method-1"], "auth_method_accessors": ["auth_userpass_1"]}}))
    else:
        sys.exit(3)
elif args[:2] == ["read", "auth/userpass/users/admin"]:
    if not Path(os.environ["MOCK_USER"]).exists():
        sys.exit(2)
elif args[:3] == ["write", "-format=json", "identity/mfa/method/totp/admin-generate"]:
    print(json.dumps({"data": {"barcode": base64.b64encode(b"mock-qr").decode(), "url": "otpauth://totp/mock"}}))
elif args[:2] == ["write", "identity/mfa/login-enforcement/admin-userpass"]:
    Path(os.environ["MOCK_ENFORCEMENT"]).touch()
elif args[:2] == ["write", "auth/userpass/users/admin"]:
    Path(os.environ["MOCK_USER"]).touch()
elif args[0] in {"status", "auth", "secrets", "policy", "write"}:
    pass
else:
    sys.exit(4)
''')
            fake_vault.chmod(0o700)
            log = location / "calls.log"
            enrollment = location / "enrollment"
            env = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                   "HOME": directory, "VAULT_TOKEN": "mock-only", "VAULT_ADMIN_PASSWORD": "mock-only",
                   "MOCK_VAULT_LOG": str(log), "MOCK_ENFORCEMENT": str(location / "enforcement"),
                   "MOCK_USER": str(location / "user-created")}
            command = ["bash", str(ADMIN), "--output-dir", str(enrollment)]
            first = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            qr = enrollment / "vault-admin-totp.png"
            self.assertEqual(qr.read_bytes(), b"mock-qr")
            self.assertEqual(qr.stat().st_mode & 0o777, 0o600)
            self.assertEqual(enrollment.stat().st_mode & 0o777, 0o700)
            second = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(log.read_text().count("identity/mfa/method/totp/admin-generate"), 1)
            self.assertEqual(log.read_text().count("write auth/userpass/users/admin"), 1)
            self.assertNotIn("admin-destroy", log.read_text())

    def test_snapshot_export_is_private_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            fake_vault = location / "vault"
            fake_vault.write_text(
                "#!/bin/sh\n"
                "if [ \"$4\" = save ]; then printf snapshot > \"$5\"; exit 0; fi\n"
                "if [ \"$4\" = inspect ]; then exit 0; fi\n"
                "exit 22\n"
            )
            fake_vault.chmod(0o700)
            env = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                   "VAULT_ADDR": "http://127.0.0.1:8200", "VAULT_TOKEN": "mock-only"}
            output = location / "backup.snap"
            first = subprocess.run(["bash", str(DATA), "snapshot-save", "--output", str(output)], env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(output.read_bytes(), b"snapshot")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            second = subprocess.run(["bash", str(DATA), "snapshot-save", "--output", str(output)], env=env, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(output.read_bytes(), b"snapshot")

    def test_restore_and_migration_require_explicit_operator_gates(self):
        script = DATA.read_text()
        self.assertIn("--confirm-cluster-id", script)
        self.assertIn("MIGRATE-POSTGRESQL-TO-RAFT", script)
        self.assertIn("os.listdir(destination)", script)
        self.assertNotIn("VAULT_ROOT_TOKEN", script)


if __name__ == "__main__":
    unittest.main()
