"""IAM login, refresh rotation, logout, and SQLite persistence."""

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from audit.audit import write_event

from auth.jwks import get_private_key, get_public_jwk, jwt_decode, jwt_encode
from auth.rbac import scopes_for_role

ISSUER = "a2a-poc-iam"
ACCESS_TTL = 600
REFRESH_TTL = 7 * 24 * 60 * 60
PASSWORD_HASHER = PasswordHasher()
router = APIRouter(prefix="/auth", tags=["IAM"])


class LoginRequest(BaseModel):
    """Credentials submitted to create an IAM session."""

    username: str
    password: str


class RefreshRequest(BaseModel):
    """Refresh token submitted for one-time rotation."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Optional refresh token to revoke when ending a session."""

    refresh_token: str | None = None


def database_path() -> Path:
    """Resolve the configured SQLite file path, defaulting inside auth/."""
    configured = os.getenv("IAM_DB_PATH")
    return Path(configured) if configured else Path(__file__).with_name("iam.db")


def database_connection() -> Iterator[sqlite3.Connection]:
    """Open a transactional SQLite connection and always close it."""
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Provide a managed database transaction to IAM operations."""
    yield from database_connection()


def required_setting(name: str) -> str:
    """Return a required IAM environment value or fail startup explicitly."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required IAM setting: {name}")
    return value


def token_secret() -> str:
    """Read the temporary IAM HS256 key from environment configuration."""
    return required_setting("IAM_TOKEN_SECRET")


def token_algorithm() -> str:
    """Return the JWT algorithm for the active auth mode."""
    return "RS256" if os.getenv("IAM_USE_RS256", "true").strip().lower() not in {"0", "false", "no"} else "HS256"


def seed_users(connection: sqlite3.Connection) -> None:
    """Create missing demo accounts from environment-provided credentials."""
    accounts = (
        (
            required_setting("IAM_ADMIN_USERNAME"),
            required_setting("IAM_ADMIN_PASSWORD"),
            "admin",
        ),
        (
            required_setting("IAM_USER_USERNAME"),
            required_setting("IAM_USER_PASSWORD"),
            "user",
        ),
        (
            required_setting("IAM_VIEWER_USERNAME"),
            required_setting("IAM_VIEWER_PASSWORD"),
            "viewer",
        ),
    )
    for username, password, role in accounts:
        existing = connection.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO users (username, password_hash, role, active) "
                "VALUES (?, ?, ?, 1)",
                (username, PASSWORD_HASHER.hash(password), role),
            )


def initialize_iam() -> None:
    """Create IAM tables and seed demo accounts when IAM is enabled."""
    token_secret()
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'user', 'viewer')),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                jti TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                failures INTEGER NOT NULL DEFAULT 0,
                locked_until INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        seed_users(connection)


def encode_token(user: sqlite3.Row, token_use: str, ttl: int) -> tuple[str, str, int]:
    """Sign a short-lived access or refresh token and return its JTI."""
    issued_at = int(time.time())
    expires_at = issued_at + ttl
    jti = secrets.token_urlsafe(24)
    payload = {
        "iss": ISSUER,
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "scope": " ".join(sorted(scopes_for_role(user["role"]))),
        "iat": issued_at,
        "exp": expires_at,
        "jti": jti,
        "token_use": token_use,
    }
    token = jwt_encode(payload, algorithm=token_algorithm())
    return token, jti, expires_at


def issue_session(connection: sqlite3.Connection, user: sqlite3.Row) -> dict:
    """Create access and refresh tokens and persist refresh-token state."""
    access_token, _, _ = encode_token(user, "access", ACCESS_TTL)
    refresh_token, refresh_jti, refresh_expiry = encode_token(
        user, "refresh", REFRESH_TTL
    )
    connection.execute(
        "INSERT INTO refresh_tokens (jti, user_id, expires_at) VALUES (?, ?, ?)",
        (refresh_jti, user["id"], refresh_expiry),
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "username": user["username"],
        "role": user["role"],
    }


def decode_iam_token(token: str, expected_use: str) -> dict:
    """Verify IAM token signature, issuer, expiry, and intended use."""
    try:
        claims = jwt_decode(
            token,
            algorithms=["HS256", "RS256"],
            issuer=ISSUER,
            options={"require": ["sub", "jti", "exp", "iat", "token_use"]},
        )
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="invalid_token") from error
    if claims.get("token_use") != expected_use:
        raise HTTPException(status_code=401, detail="invalid_token_type")
    return claims


def record_login_failure(connection: sqlite3.Connection, username: str) -> bool:
    """Record a failed login and apply an increasing lockout after five failures."""
    row = connection.execute(
        "SELECT failures FROM login_attempts WHERE username = ?",
        (username,),
    ).fetchone()
    failures = (row["failures"] if row else 0) + 1
    locked_until = 0
    if failures >= 5 and failures % 5 == 0:
        lockout_seconds = min(60 * (2 ** ((failures // 5) - 1)), 3600)
        locked_until = int(time.time()) + lockout_seconds
    connection.execute(
        "INSERT INTO login_attempts (username, failures, locked_until) "
        "VALUES (?, ?, ?) ON CONFLICT(username) DO UPDATE SET "
        "failures = excluded.failures, locked_until = excluded.locked_until",
        (username, failures, locked_until),
    )
    return locked_until > 0


def active_user(connection: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    """Load an active account or reject tokens belonging to disabled users."""
    user = connection.execute(
        "SELECT id, username, role, active FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user is None or not user["active"]:
        raise HTTPException(status_code=401, detail="inactive_user")
    return user


def revoke_claims(connection: sqlite3.Connection, claims: dict) -> None:
    """Persist a token JTI denylist entry until the token expires."""
    connection.execute(
        "INSERT OR IGNORE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
        (claims["jti"], int(claims["exp"])),
    )


def decode_access_token(token: str) -> dict:
    """Verify a user access token before token exchange."""
    return decode_iam_token(token, "access")


def is_revoked(jti: str) -> bool:
    """Return whether an access-token JTI is on the revocation denylist."""
    with db() as connection:
        row = connection.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ? AND expires_at > ?",
            (jti, int(time.time())),
        ).fetchone()
    return row is not None


def get_user(user_id: str) -> sqlite3.Row | None:
    """Load a user by id, including inactive accounts for exchange checks."""
    with db() as connection:
        return connection.execute(
            "SELECT id, username, role, active FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def sign_token(claims: dict) -> str:
    """Sign an exchanged agent token with the configured RS256 key and key id."""
    return jwt.encode(claims, get_private_key(), algorithm="RS256",
                      headers={"kid": get_public_jwk()["kid"]})


@router.post("/login")
def login(request: LoginRequest) -> dict:
    """Authenticate a seeded account and issue a new IAM session."""
    username = request.username.strip()
    failure_status: int | None = None
    response: dict | None = None
    failure_event = "login_fail"
    with db() as connection:
        attempt = connection.execute(
            "SELECT locked_until FROM login_attempts WHERE username = ?",
            (username,),
        ).fetchone()
        if attempt and attempt["locked_until"] > int(time.time()):
            failure_status = 429
            failure_event = "lockout"
        else:
            user = connection.execute(
                "SELECT id, username, password_hash, role, active "
                "FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
            valid_password = False
            if user is not None and user["active"]:
                try:
                    valid_password = PASSWORD_HASHER.verify(
                        user["password_hash"], request.password
                    )
                except (VerifyMismatchError, InvalidHashError):
                    valid_password = False

            if not valid_password:
                locked = record_login_failure(connection, username)
                failure_status = 429 if locked else 401
                failure_event = "lockout" if locked else "login_fail"
            else:
                connection.execute(
                    "DELETE FROM login_attempts WHERE username = ?", (username,)
                )
                response = issue_session(connection, user)

    if failure_status:
        if os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            write_event("auth", actor=username, event="login_fail",
                        decision="deny", status=failure_status, reason="invalid_credentials")
            if failure_event == "lockout":
                write_event("auth", actor=username, event="lockout",
                            decision="deny", status=429, reason="too many failed logins")
        detail = "login_temporarily_locked" if failure_status == 429 else "invalid_credentials"
        raise HTTPException(status_code=failure_status, detail=detail)
    if response is None:
        raise RuntimeError("Login completed without a response or authentication error")
    if os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        write_event("auth", actor=response.get("username"), role=response.get("role"),
                    event="login_ok", decision="allow", status=200)
    return response


@router.post("/refresh")
def refresh(request: RefreshRequest, x_correlation_id: str | None = Header(default=None)) -> dict:
    """Rotate a valid refresh token once and issue a fresh token pair."""
    claims = decode_iam_token(request.refresh_token, "refresh")
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        stored = connection.execute(
            "SELECT user_id, expires_at, revoked_at FROM refresh_tokens WHERE jti = ?",
            (claims["jti"],),
        ).fetchone()
        if (
            stored is None
            or stored["revoked_at"] is not None
            or stored["expires_at"] <= int(time.time())
        ):
            raise HTTPException(status_code=401, detail="refresh_token_revoked")
        user = active_user(connection, claims["sub"])
        if stored["user_id"] != user["id"]:
            raise HTTPException(status_code=401, detail="invalid_token")
        connection.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE jti = ?",
            (int(time.time()), claims["jti"]),
        )
        revoke_claims(connection, claims)
        result = issue_session(connection, user)
    if os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        write_event("auth", cid=x_correlation_id, actor=user["username"], role=user["role"], event="refresh",
                    decision="allow", status=200)
    return result


@router.post("/logout")
def logout(
    request: LogoutRequest,
    authorization: str | None = Header(default=None),
    x_correlation_id: str | None = Header(default=None),
) -> dict:
    """Revoke the supplied refresh token and optional access-token JTI."""
    with db() as connection:
        if authorization and authorization.startswith("Bearer "):
            access_claims = decode_iam_token(authorization[7:], "access")
            revoke_claims(connection, access_claims)

        if request.refresh_token:
            refresh_claims = decode_iam_token(request.refresh_token, "refresh")
            stored = connection.execute(
                "SELECT jti FROM refresh_tokens WHERE jti = ?",
                (refresh_claims["jti"],),
            ).fetchone()
            if stored:
                now = int(time.time())
                connection.execute(
                    "UPDATE refresh_tokens SET revoked_at = ? WHERE jti = ?",
                    (now, refresh_claims["jti"]),
                )
                revoke_claims(connection, refresh_claims)

    if os.getenv("IAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        write_event("auth", cid=x_correlation_id, event="logout", decision="allow", status=200)
    return {"logged_out": True}


