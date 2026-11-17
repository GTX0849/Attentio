"""Passwords, session tokens and the login rate limit, as plain functions (standard library only).

Passwords are hashed with scrypt and a random salt per account, and compared with
`hmac.compare_digest`. A session token is random and only its SHA-256 digest is stored, so a copy
of the database cannot be used to sign in. Nothing here logs or returns a password.
"""

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.config import load_config

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
SCHEME = "scrypt"


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=64 * 1024 * 1024
    )


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash` (salt and hash in hex), with a new random salt every time."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True when `password` matches `stored`. A damaged or unknown stored value never matches."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != SCHEME:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
        actual = _derive(password, salt, n, r, p)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


# A hash of a password nobody has, checked when the email is unknown so that an unknown user
# costs the same time as a wrong password.
_DECOY = hash_password(secrets.token_urlsafe(16))


def verify_decoy(password: str) -> None:
    verify_password(password, _DECOY)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same_secret(supplied: str, expected: str) -> bool:
    """Constant-time comparison of two secrets (for the service key)."""
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


class LoginLimiter:
    """Failed sign-ins per email inside a sliding window. Kept in memory, case-insensitive."""

    def __init__(self, max_failures: int, window: timedelta) -> None:
        self._max = max_failures
        self._window = window
        self._failures: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def _recent(self, email: str, now: datetime) -> deque[datetime]:
        failures = self._failures[email.lower()]
        while failures and failures[0] <= now - self._window:
            failures.popleft()
        if not failures:
            self._failures.pop(email.lower(), None)
        return failures

    def blocked(self, email: str, now: datetime) -> bool:
        return len(self._recent(email, now)) >= self._max

    def record_failure(self, email: str, now: datetime) -> None:
        self._recent(email, now)
        self._failures[email.lower()].append(now)

    def reset(self, email: str) -> None:
        self._failures.pop(email.lower(), None)


@dataclass(frozen=True)
class AuthSettings:
    session_hours: float
    max_failures: int
    window_minutes: float


def auth_settings() -> AuthSettings:
    """The `auth:` section of the shared config (12 hours, 5 failures per 5 minutes when absent)."""
    section = load_config().raw.get("auth") or {}
    return AuthSettings(
        session_hours=float(section.get("session_hours", 12)),
        max_failures=int(section.get("login_max_failures", 5)),
        window_minutes=float(section.get("login_window_minutes", 5)),
    )
