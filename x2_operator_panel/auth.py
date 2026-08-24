"""Password-hash and short-lived session helpers for the local panel."""

from __future__ import annotations

from collections import deque
import hashlib
import hmac
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass


_HASH_PREFIX = "pbkdf2_sha256"
_HASH_ITERATIONS = 310_000
_MAX_HASH_ITERATIONS = 2_000_000


def create_password_hash(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS
    )
    return f"{_HASH_PREFIX}${_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = stored_hash.split("$", 3)
        if algorithm != _HASH_PREFIX:
            return False
        rounds = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (TypeError, ValueError):
        return False
    if (
        rounds < 100_000
        or rounds > _MAX_HASH_ITERATIONS
        or not 8 <= len(salt) <= 64
        or len(expected) != hashlib.sha256().digest_size
    ):
        return False
    calculated = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, rounds
    )
    return hmac.compare_digest(calculated, expected)


@dataclass(frozen=True)
class Session:
    token: str
    expires_at: float


class SessionStore:
    """A single-operator session store. A new login supersedes the old one."""

    def __init__(self, password_hash: str | None, ttl_sec: float) -> None:
        if ttl_sec <= 0.0:
            raise ValueError("Session TTL must be positive")
        self._password_hash = password_hash or ""
        self._ttl_sec = ttl_sec
        self._session: Session | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._password_hash)

    def login(self, password: str) -> Session | None:
        if not self.configured or not verify_password(password, self._password_hash):
            return None
        session = Session(secrets.token_urlsafe(32), time.monotonic() + self._ttl_sec)
        with self._lock:
            self._session = session
        return session

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            if self._session is None or self._session.expires_at <= time.monotonic():
                self._session = None
                return False
            return hmac.compare_digest(token, self._session.token)


class LoginAttemptLimiter:
    """Bound expensive password verification by source and globally."""

    def __init__(
        self,
        per_source_limit: int = 5,
        global_limit: int = 30,
        window_sec: float = 60.0,
    ) -> None:
        if per_source_limit < 1 or global_limit < 1 or window_sec <= 0.0:
            raise ValueError("Login rate-limit values must be positive")
        self._per_source_limit = per_source_limit
        self._global_limit = global_limit
        self._window_sec = window_sec
        self._by_source: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def consume(self, source: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self._window_sec
        with self._lock:
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            for old_source in [
                key
                for key, values in self._by_source.items()
                if not values or values[-1] <= cutoff
            ]:
                self._by_source.pop(old_source, None)
            attempts = self._by_source.setdefault(source, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self._per_source_limit or len(self._global) >= self._global_limit:
                oldest = (
                    attempts[0]
                    if len(attempts) >= self._per_source_limit
                    else self._global[0]
                )
                retry_after = max(1, int(oldest + self._window_sec - now) + 1)
                return False, retry_after
            attempts.append(now)
            self._global.append(now)
            return True, 0


def main() -> None:
    password = os.environ.get("X2_OPERATOR_PANEL_PASSWORD")
    if password is None:
        password = input("Operator panel password: ")
    try:
        print(create_password_hash(password))
    except ValueError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from error
