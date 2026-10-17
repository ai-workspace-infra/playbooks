#!/usr/bin/env python3
"""Validate persisted Accounts SMTP configuration without sending a message.

Run as root on the control host. Secrets stay in process memory; output contains
only stage names and SMTP status codes, never credential values or server text.
"""
import argparse
import json
import smtplib
import ssl
import subprocess
import sys


KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USERNAME", "SMTP_PASSWORD")


def check(container, env_file):
    inspected = json.loads(subprocess.check_output(
        ["docker", "inspect", container], timeout=15))[0]
    runtime = dict(value.split("=", 1) for value in inspected["Config"]["Env"])
    persisted = {}
    with open(env_file, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if value[:1] == value[-1:] and value[:1] in ("'", '"'):
                    value = value[1:-1]
                persisted[key] = value
    for key in KEYS:
        if not runtime.get(key, "").strip() or "${" in runtime[key]:
            raise ValueError("missing_runtime_field:" + key)
        if runtime[key] != persisted.get(key):
            raise ValueError("persisted_runtime_mismatch:" + key)
    mode = runtime.get("SMTP_TLS_MODE", "auto").lower()
    if mode not in ("auto", "starttls", "implicit", "smtps"):
        raise ValueError("encrypted_smtp_required")
    context = ssl.create_default_context()
    host, port = runtime["SMTP_HOST"], int(runtime["SMTP_PORT"])
    if mode in ("implicit", "smtps"):
        client = smtplib.SMTP_SSL(host, port, timeout=12, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=12)
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    try:
        client.login(runtime["SMTP_USERNAME"], runtime["SMTP_PASSWORD"])
    finally:
        client.close()
    return {"ok": True, "stage": "authenticated_tls", "sent_messages": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="web-saas-accounts")
    parser.add_argument("--env-file", default="/etc/xcontrol/web-saas/accounts-smtp.env")
    args = parser.parse_args()
    try:
        result = check(args.container, args.env_file)
    except smtplib.SMTPResponseException as exc:
        result = {"ok": False, "stage": "smtp", "status": exc.smtp_code}
    except ValueError as exc:
        # All ValueErrors raised above contain field names, never field values.
        result = {"ok": False, "stage": "configuration", "reason": str(exc)}
    except Exception as exc:
        result = {"ok": False, "stage": "connection_or_runtime", "error_type": type(exc).__name__}
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
