from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NonIaCSSHAccessContractTest(unittest.TestCase):
    def test_playbook_installs_key_before_password_hardening_can_lock_out_release(self) -> None:
        playbook = (ROOT / "prepare_non_iac_ssh_access.yml").read_text()
        self.assertIn("SSH_PUBLIC_DEPLOY_KEY", playbook)
        self.assertIn("ansible.posix.authorized_key", playbook)
        self.assertIn("chattr", playbook)
        self.assertIn("authorized_keys", playbook)


if __name__ == "__main__":
    unittest.main()
