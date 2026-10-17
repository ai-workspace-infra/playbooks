import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "smtp_check", Path(__file__).parents[1] / "scripts/check-accounts-smtp.py")
smtp_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smtp_check)


class SMTPPreflightTest(unittest.TestCase):
    def run_check(self, runtime, persisted):
        payload = [{"Config": {"Env": [key + "=" + value for key, value in runtime.items()]}}]
        smtp = Mock()
        with patch.object(smtp_check.subprocess, "check_output", return_value=json.dumps(payload)), \
                patch("builtins.open", return_value=io.StringIO(persisted)), \
                patch.object(smtp_check.smtplib, "SMTP", return_value=smtp):
            result = smtp_check.check("accounts", "runtime.env")
        return result, smtp

    def test_rejects_persisted_credential_drift_before_connecting(self):
        env = {key: "fixture" for key in smtp_check.KEYS}
        persisted = "\n".join(key + "='fixture'" for key in smtp_check.KEYS if key != "SMTP_PASSWORD")
        with self.assertRaisesRegex(ValueError, "persisted_runtime_mismatch:SMTP_PASSWORD"):
            self.run_check(env, persisted)

    def test_authenticates_with_verified_tls_without_sending_message(self):
        env = dict(zip(smtp_check.KEYS, ("smtp.example.test", "587", "sender@example.test", "user", "secret")))
        result, smtp = self.run_check(env, "\n".join(key + "='" + value + "'" for key, value in env.items()))
        self.assertTrue(result["ok"])
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("user", "secret")
        smtp.sendmail.assert_not_called()
        smtp.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
