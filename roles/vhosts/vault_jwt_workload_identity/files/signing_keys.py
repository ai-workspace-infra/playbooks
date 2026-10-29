"""Validate Kubernetes discovery/JWKS and encode RSA verification keys for Vault.

The caller must obtain both documents through the authenticated, pinned cluster
connection; this module does not establish trust in an arbitrary input file.
"""
import base64
import json


def _length(n):
    if n < 128:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag, value):
    return bytes([tag]) + _length(len(value)) + value


def _integer(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big") or b"\0"
    if raw[0] & 0x80:
        raw = b"\0" + raw
    return _tlv(2, raw)


def _number(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("invalid JWK integer")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid JWK encoding") from exc
    return int.from_bytes(raw, "big")


def _rsa_pem(key):
    if key.get("kty") != "RSA" or key.get("use", "sig") != "sig" or key.get("alg", "RS256") != "RS256":
        raise ValueError("unsupported signing key")
    if "d" in key or "p" in key or "q" in key:
        raise ValueError("private JWK rejected")
    modulus, exponent = _number(key.get("n")), _number(key.get("e"))
    if not 2048 <= modulus.bit_length() <= 8192 or exponent < 3 or exponent % 2 == 0:
        raise ValueError("invalid RSA key")
    rsa = _tlv(0x30, _integer(modulus) + _integer(exponent))
    algorithm = bytes.fromhex("300d06092a864886f70d0101010500")
    spki = _tlv(0x30, algorithm + _tlv(3, b"\0" + rsa))
    b64 = base64.b64encode(spki).decode("ascii")
    lines = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return "-----BEGIN PUBLIC KEY-----\n" + lines + "\n-----END PUBLIC KEY-----\n"


def discovery_contract(discovery_json, jwks_json):
    """Return exact issuer and PEM keys, failing closed on malformed discovery."""
    discovery, jwks = json.loads(discovery_json), json.loads(jwks_json)
    issuer = discovery.get("issuer")
    if not isinstance(issuer, str) or not issuer or len(issuer) > 2048 or any(c.isspace() for c in issuer):
        raise ValueError("invalid issuer")
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
        raise ValueError("invalid JWK set")
    ids = [key.get("kid") for key in keys if isinstance(key, dict)]
    if len(ids) != len(keys) or any(not isinstance(kid, str) or not kid for kid in ids) or len(set(ids)) != len(ids):
        raise ValueError("missing or duplicate key ID")
    pems = [_rsa_pem(key) for key in keys]
    return issuer, pems
