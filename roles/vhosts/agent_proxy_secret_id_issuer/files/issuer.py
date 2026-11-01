#!/usr/bin/env python3
"""mTLS-gated, response-wrapped Vault AppRole SecretID issuer."""
import hashlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise ValueError("missing " + name)
    return value


def vault_request(path, payload=None, token=None, wrap=False):
    address = required("VAULT_ADDR").rstrip("/")
    if not address.startswith("https://"):
        raise ValueError("Vault address must use HTTPS")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    if wrap:
        headers["X-Vault-Wrap-TTL"] = "60s"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(address + "/v1/" + path, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def issue():
    mount = required("ISSUER_VAULT_KUBERNETES_MOUNT")
    role = required("ISSUER_VAULT_KUBERNETES_ROLE")
    approle = required("ISSUER_VAULT_APPROLE")
    if any(not c.isalnum() and c not in "-_" for c in mount + role + approle):
        raise ValueError("invalid Vault role or mount")
    jwt = Path(required("ISSUER_SERVICE_ACCOUNT_TOKEN_FILE")).read_text().strip()
    token = vault_request("auth/" + mount + "/login", {"role": role, "jwt": jwt})["auth"]["client_token"]
    role_id = vault_request("auth/approle/role/" + approle + "/role-id", token=token)["data"]["role_id"]
    wrapped = vault_request("auth/approle/role/" + approle + "/secret-id", {}, token, True)["wrap_info"]["token"]
    return {"role_id": role_id, "wrapping_token": wrapped}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path != "/v1/secret-id":
            self.send_error(404)
            return
        try:
            peer = self.connection.getpeercert(binary_form=True)
            allowed = {line.strip().lower() for line in Path(required("ISSUER_CLIENT_FINGERPRINTS_FILE")).read_text().splitlines() if line.strip()}
            if not peer or hashlib.sha256(peer).hexdigest() not in allowed:
                self.send_error(403)
                return
            data = json.dumps(issue(), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            self.send_error(503)


def main():
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(required("ISSUER_SERVER_CERT"), required("ISSUER_SERVER_KEY"))
    context.load_verify_locations(cafile=required("ISSUER_CLIENT_CA"))
    context.verify_mode = ssl.CERT_REQUIRED
    server = ThreadingHTTPServer((required("ISSUER_LISTEN_HOST"), int(required("ISSUER_LISTEN_PORT"))), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print("issuer startup failed (" + type(exc).__name__ + ")", file=sys.stderr)
        sys.exit(1)
