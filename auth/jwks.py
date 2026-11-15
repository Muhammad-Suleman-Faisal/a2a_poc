"""RSA key generation and JWKS publishing for IAM tokens."""

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import InvalidTokenError
import jwt

KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY_PATH = KEYS_DIR / "iam_private_key.pem"
PUBLIC_KEY_PATH = KEYS_DIR / "iam_public_key.pem"
ISSUER = "a2a-poc-iam"


def _b64url(data: bytes) -> str:
    """Encode bytes in JWT-compatible base64url form."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _ensure_keys() -> None:
    """Create a fresh RSA keypair on first run if files are missing."""
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists():
        return

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PRIVATE_KEY_PATH.write_bytes(private_bytes)
    PUBLIC_KEY_PATH.write_bytes(public_bytes)


def get_private_key() -> str:
    """Return the PEM-encoded RSA private key used to sign IAM JWTs."""
    _ensure_keys()
    return PRIVATE_KEY_PATH.read_text(encoding="utf-8")


def get_public_key() -> str:
    """Return the PEM-encoded RSA public key used for JWKS verification."""
    _ensure_keys()
    return PUBLIC_KEY_PATH.read_text(encoding="utf-8")


def get_public_jwk() -> dict:
    """Return the public JWK object for the currently configured signing key."""
    public_key = __import__("cryptography.hazmat.primitives.serialization", fromlist=["load_pem_public_key"]).load_pem_public_key(get_public_key().encode("utf-8"))
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": "a2a-iam-rsa-1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def jwks_document() -> dict:
    """Create the public JWKS document consumed by agents and clients."""
    return {"keys": [get_public_jwk()]}


def jwt_encode(payload: dict, algorithm: str = "RS256") -> str:
    """Encode an IAM payload using the configured RSA private key."""
    if algorithm != "RS256":
        raise ValueError("Unsupported JWT signing algorithm for IAM: %s" % algorithm)
    return jwt.encode(payload, get_private_key(), algorithm=algorithm,
                      headers={"kid": get_public_jwk()["kid"]})


def jwt_decode(token: str, algorithms: list[str], issuer: str, options: dict | None = None) -> dict:
    """Verify and decode an IAM JWT using the published RSA public key."""
    public_key = get_public_key()
    return jwt.decode(
        token,
        public_key,
        algorithms=algorithms,
        issuer=issuer,
        options=options or {},
    )
