"""Isolated tests for roles/vhosts/vault_ssh_ca_trust.

Runs the role with a local connection against a temporary directory tree.
Uses the real sshd (`-t`/`-T` only, never started) when VAULT_SSH_TEST_SSHD is
set, otherwise a fake. No real host, Vault or production data is touched.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SSHD = os.environ.get("VAULT_SSH_TEST_SSHD") or str(HERE / "fake_sshd.py")


def keygen(path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return Path(str(path) + ".pub").read_text().strip()


def fingerprint(pub):
    return subprocess.run(["ssh-keygen", "-lf", str(pub)], check=True, capture_output=True,
                          text=True).stdout.split()[1]


class Role(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ssh = self.tmp / "ssh"
        (self.ssh / "sshd_config.d").mkdir(parents=True)
        (self.tmp / "sudoers.d").mkdir()
        self.hostkey = self.tmp / "hostkey"
        keygen(self.hostkey)
        self.write_main()
        self.ca1 = keygen(self.tmp / "ca1")
        self.ca2 = keygen(self.tmp / "ca2")
        self.reload_marker = self.tmp / "reloaded"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_main(self, prefix=""):
        (self.ssh / "sshd_config").write_text(
            f"{prefix}HostKey {self.hostkey}\nInclude {self.ssh}/sshd_config.d/*.conf\n")

    def run_role(self, **extra):
        v = {"vault_ssh_ca_trust_ssh_dir": str(self.ssh),
             "vault_ssh_ca_trust_sudoers_dir": str(self.tmp / "sudoers.d"),
             "vault_ssh_ca_trust_sshd_bin": SSHD,
             "vault_ssh_ca_trust_visudo_bin": str(HERE / "fake_visudo.py"),
             "vault_ssh_ca_trust_manage_account": False,
             "vault_ssh_ca_trust_reload_command": f"sh -c 'echo x >> {self.reload_marker}'",
             "vault_ssh_ca_trust_public_key": self.ca1}
        v.update(extra)
        env = dict(os.environ, ANSIBLE_ROLES_PATH=str(ROOT / "roles"), ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"),
                   ANSIBLE_CALLBACKS_ENABLED="timer", ANSIBLE_FACT_CACHING="memory")
        return subprocess.run(["ansible-playbook", "-i", "localhost,", str(HERE / "play.yml"),
                               "-e", json.dumps(v)], env=env, capture_output=True, text=True)

    def reloads(self):
        return len(self.reload_marker.read_text().splitlines()) if self.reload_marker.exists() else 0

    def snapshot(self):
        return {str(p.relative_to(self.tmp)): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}

    def ok(self, **extra):
        r = self.run_role(**extra)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        return r

    def rejected(self, task, **extra):
        before = self.snapshot()
        r = self.run_role(**extra)
        self.assertNotEqual(r.returncode, 0, r.stdout[-3000:])
        head = r.stdout.split("fatal:")[0]
        self.assertIn(task, head.rsplit("TASK [", 1)[-1].split("]")[0], r.stdout[-3000:])
        self.assertEqual(self.snapshot(), before, "reject path mutated files")
        self.assertEqual(self.reloads(), 0, "reject path reloaded sshd")
        return r

    # -- happy paths --------------------------------------------------------
    def test_fresh_install_reloads_once_and_grants_no_sudo(self):
        self.ok()
        self.assertEqual((self.ssh / "vault-user-ca.pub").read_text(), self.ca1 + "\n")
        self.assertIn("TrustedUserCAKeys", (self.ssh / "sshd_config.d/20-vault-migration-ca.conf").read_text())
        self.assertEqual(self.reloads(), 1)
        self.assertEqual(list((self.tmp / "sudoers.d").iterdir()), [])

    def test_rerun_is_idempotent_without_reload(self):
        self.ok()
        self.ok()
        self.assertEqual(self.reloads(), 1)

    def test_contracted_ca_rotation_reloads_on_pubkey_change_alone(self):
        self.ok()
        fp = fingerprint(self.ssh / "vault-user-ca.pub")
        self.ok(vault_ssh_ca_trust_public_key=self.ca2, vault_ssh_ca_trust_rotate_from_fingerprint=fp,
                vault_ssh_ca_trust_rotate_confirm="ROTATE-VAULT-SSH-CA-localhost")
        self.assertEqual((self.ssh / "vault-user-ca.pub").read_text(), self.ca2 + "\n")
        self.assertEqual(self.reloads(), 2)

    def test_explicit_management_sudo(self):
        self.ok(vault_ssh_ca_trust_management_purpose="vault-legacy-migration",
                vault_ssh_ca_trust_grant_sudo_confirm="GRANT-NOPASSWD-ALL-vault-migrate")
        self.assertEqual((self.tmp / "sudoers.d/vault-migration-vault-migrate").read_text(),
                         "vault-migrate ALL=(ALL) NOPASSWD: ALL\n")

    # -- reject paths: originals preserved, no reload -----------------------
    def seed_existing(self, ca, dropin=None):
        (self.ssh / "vault-user-ca.pub").write_text(ca + "\n")
        if dropin is not None:
            (self.ssh / "sshd_config.d/20-vault-migration-ca.conf").write_text(dropin)

    def test_different_ca_rejected_by_default(self):
        self.seed_existing(self.ca2)
        self.rejected("different CA")

    def test_rotation_with_wrong_fingerprint_or_confirm_rejected(self):
        self.seed_existing(self.ca2)
        self.rejected("different CA", vault_ssh_ca_trust_rotate_from_fingerprint="SHA256:wrong",
                      vault_ssh_ca_trust_rotate_confirm="ROTATE-VAULT-SSH-CA-localhost")
        self.rejected("different CA", vault_ssh_ca_trust_rotate_from_fingerprint=fingerprint(self.ssh / "vault-user-ca.pub"))

    def test_differing_dropin_rejected_before_ca_written(self):
        (self.ssh / "sshd_config.d/20-vault-migration-ca.conf").write_text("TrustedUserCAKeys /other\n")
        self.rejected("differing reviewed drop-in")
        self.assertFalse((self.ssh / "vault-user-ca.pub").exists())

    def test_shadowed_candidate_rejected(self):
        self.write_main(prefix="TrustedUserCAKeys /etc/ssh/someone-else.pub\n")
        self.rejected("must be effective")

    def test_invalid_candidate_config_rejected(self):
        (self.ssh / "sshd_config.d/10-broken.conf").write_text("NotARealOption yes\n")
        self.rejected("must pass sshd -t")

    def test_sudo_requires_purpose_and_exact_confirm(self):
        self.rejected("management sudo", vault_ssh_ca_trust_management_purpose="vault-legacy-migration")
        self.rejected("management sudo", vault_ssh_ca_trust_management_purpose="anything",
                      vault_ssh_ca_trust_grant_sudo_confirm="GRANT-NOPASSWD-ALL-vault-migrate")

    def test_legacy_default_sudo_switch_rejected(self):
        self.rejected("removed default-sudo", vault_ssh_ca_trust_sudo=True)


if __name__ == "__main__":
    unittest.main()
