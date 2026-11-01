"""Local screen-lock service (round 22).

A browser-local password gate.  The password is never stored or returned:
the settings row keeps only ``pbkdf2_sha256$<iterations>$<salt>$<hash>``
(PBKDF2-HMAC-SHA256, random per-password salt, constant-time comparison).

State lives in this process so a page refresh or a newly opened tab cannot
make the lock disappear; the idle timer is evaluated here as well, which is
what makes ``idleMinutes`` authoritative rather than a client-side illusion.

Threat model: this is a shoulder-surfing / accidental-exposure guard, not an
adversarial boundary.  Anyone who can read the local data directory, copy the
loopback session token, or edit ``data.db`` can defeat it by design.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import threading
import time
from datetime import datetime
from typing import Any

PASSWORD_SCHEME = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 210_000
SALT_BYTES = 16
HASH_BYTES = 32
MIN_PASSWORD_LENGTH = 4
MAX_PASSWORD_LENGTH = 128
MAX_IDLE_MINUTES = 24 * 60

# Failure policy: the first two wrong attempts are answered directly; from the
# third one on the verifier backs off exponentially (2 s, 4 s, 8 s … 60 s).
FAILURES_BEFORE_BACKOFF = 3
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 60


class LockError(Exception):
    """An error with a stable machine-readable code and HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def hash_password(password: str) -> str:
    """Return the encoded PBKDF2 record for *password* (new random salt)."""
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, HASH_BYTES)
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password_hash(password: str, encoded: str) -> bool:
    """Constant-time check of *password* against an encoded hash record."""
    if not encoded:
        return False
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        rounds = int(iterations)
        if rounds < 1 or rounds > 5_000_000:
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"), validate=True)
        expected = base64.b64decode(digest_b64.encode("ascii"), validate=True)
        if len(salt) < 8 or len(expected) < 16:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def is_password_hash(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PASSWORD_SCHEME + "$")


class LockService:
    """Process-wide lock state plus the persistent password hash."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._locked = False
        self._locked_at: str | None = None
        self._failed_attempts = 0
        self._next_attempt_at = 0.0
        self._last_activity = time.monotonic()

    # ------------------------------------------------------------- settings
    def _settings(self) -> dict:
        lock = (self.store.get_settings() or {}).get("lock") or {}
        return lock if isinstance(lock, dict) else {}

    def _enabled(self) -> bool:
        return bool(self._settings().get("enabled"))

    def _hash(self) -> str:
        value = self._settings().get("passwordHash") or ""
        return value if is_password_hash(value) else ""

    def _idle_minutes(self) -> float:
        try:
            value = float(self._settings().get("idleMinutes") or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(value, MAX_IDLE_MINUTES))

    def has_password(self) -> bool:
        return bool(self._hash())

    # --------------------------------------------------------------- status
    def _retry_after(self) -> int:
        remaining = self._next_attempt_at - time.monotonic()
        return max(0, int(math.ceil(remaining))) if remaining > 0 else 0

    def _maybe_idle_lock(self) -> None:
        if self._locked or not self._enabled() or not self._hash():
            return
        idle = self._idle_minutes()
        if idle <= 0:
            return
        if time.monotonic() - self._last_activity >= idle * 60:
            self._locked = True
            self._locked_at = datetime.now().astimezone().isoformat(timespec="seconds")

    def status(self) -> dict:
        with self._lock:
            self._maybe_idle_lock()
            return {
                "enabled": self._enabled(),
                "hasPassword": bool(self._hash()),
                "locked": self._locked,
                "idleMinutes": self._idle_minutes(),
                "lockedAt": self._locked_at,
                "failedAttempts": self._failed_attempts,
                "retryAfter": self._retry_after(),
                "minPasswordLength": MIN_PASSWORD_LENGTH,
                "maxPasswordLength": MAX_PASSWORD_LENGTH,
                "scheme": PASSWORD_SCHEME,
            }

    def _set_settings(self, patch: dict) -> None:
        self.store.update_settings({"lock": patch})

    # ------------------------------------------------------------ operations
    def configure(self, enabled: Any = None, idle_minutes: Any = None) -> dict:
        with self._lock:
            patch: dict[str, Any] = {}
            if enabled is not None:
                if not isinstance(enabled, bool):
                    raise LockError("LOCK_INVALID_CONFIG", "enabled 必须是布尔值")
                patch["enabled"] = enabled
            if idle_minutes is not None:
                try:
                    idle = float(idle_minutes)
                except (TypeError, ValueError):
                    raise LockError("LOCK_INVALID_CONFIG", "idleMinutes 必须是数字")
                if math.isnan(idle) or math.isinf(idle) or idle < 0 or idle > MAX_IDLE_MINUTES:
                    raise LockError(
                        "LOCK_INVALID_CONFIG", f"idleMinutes 必须在 0–{MAX_IDLE_MINUTES} 之间"
                    )
                if idle > 0 and idle < 0.01:
                    idle = 0.01
                patch["idleMinutes"] = idle
            if not patch:
                raise LockError("LOCK_INVALID_CONFIG", "没有可更新的配置项")
            if patch.get("enabled") is True and not self._hash():
                raise LockError(
                    "LOCK_PASSWORD_REQUIRED",
                    "请先设置锁屏密码，再启用锁屏",
                    status=409,
                )
            self._set_settings(patch)
            if "idleMinutes" in patch:
                # Give the new threshold a full window instead of locking at once.
                self._last_activity = time.monotonic()
            if patch.get("enabled") is False:
                self._locked = False
                self._locked_at = None
                self._reset_failures()
            return self.status()

    def set_password(self, old_password: str, new_password: str) -> dict:
        with self._lock:
            if not isinstance(new_password, str):
                raise LockError("LOCK_INVALID_PASSWORD", "新口令格式无效")
            password = new_password
            if len(password) < MIN_PASSWORD_LENGTH:
                raise LockError(
                    "LOCK_PASSWORD_TOO_SHORT",
                    f"锁屏口令至少 {MIN_PASSWORD_LENGTH} 个字符",
                )
            if len(password) > MAX_PASSWORD_LENGTH:
                raise LockError(
                    "LOCK_PASSWORD_TOO_LONG",
                    f"锁屏口令最多 {MAX_PASSWORD_LENGTH} 个字符",
                )
            if self._hash():
                self._guard_attempt()
                if not verify_password_hash(old_password or "", self._hash()):
                    self._record_failure()
                    raise LockError(
                        "LOCK_BAD_OLD_PASSWORD",
                        "当前口令不正确",
                        status=401,
                        failedAttempts=self._failed_attempts,
                        retryAfter=self._retry_after(),
                    )
            self._set_settings({"passwordHash": hash_password(password), "enabled": True})
            self._reset_failures()
            self._last_activity = time.monotonic()
            return self.status()

    def clear_password(self, old_password: str) -> dict:
        with self._lock:
            if not self._hash():
                self._set_settings({"enabled": False, "passwordHash": ""})
                self._locked = False
                self._locked_at = None
                self._reset_failures()
                return self.status()
            self._guard_attempt()
            if not verify_password_hash(old_password or "", self._hash()):
                self._record_failure()
                raise LockError(
                    "LOCK_BAD_OLD_PASSWORD",
                    "当前口令不正确",
                    status=401,
                    failedAttempts=self._failed_attempts,
                    retryAfter=self._retry_after(),
                )
            self._set_settings({"enabled": False, "passwordHash": ""})
            self._locked = False
            self._locked_at = None
            self._reset_failures()
            return self.status()

    def lock_now(self) -> dict:
        with self._lock:
            if not self._enabled() or not self._hash():
                raise LockError(
                    "LOCK_NOT_CONFIGURED",
                    "锁屏尚未启用或未设置口令",
                    status=409,
                )
            self._locked = True
            self._locked_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self._reset_failures()
            return self.status()

    def verify(self, password: str) -> dict:
        with self._lock:
            if not self._locked:
                raise LockError("LOCK_NOT_LOCKED", "当前未锁定", status=409)
            self._guard_attempt()
            if not verify_password_hash(password or "", self._hash()):
                self._record_failure()
                raise LockError(
                    "LOCK_BAD_PASSWORD",
                    "锁屏口令不正确",
                    status=401,
                    failedAttempts=self._failed_attempts,
                    retryAfter=self._retry_after(),
                )
            self._locked = False
            self._locked_at = None
            self._reset_failures()
            self._last_activity = time.monotonic()
            return self.status()

    def activity(self) -> dict:
        with self._lock:
            self._last_activity = time.monotonic()
            # Do not unlock anything here; just refresh the idle window.
            return self.status()

    # -------------------------------------------------------------- failures
    def _guard_attempt(self) -> None:
        retry = self._retry_after()
        if retry > 0:
            raise LockError(
                "LOCK_RATE_LIMITED",
                f"尝试过于频繁，请 {retry} 秒后再试",
                status=429,
                retryAfter=retry,
                failedAttempts=self._failed_attempts,
            )

    def _record_failure(self) -> None:
        self._failed_attempts += 1
        if self._failed_attempts >= FAILURES_BEFORE_BACKOFF:
            exponent = self._failed_attempts - FAILURES_BEFORE_BACKOFF
            delay = min(BACKOFF_BASE_SECONDS ** (exponent + 1), BACKOFF_MAX_SECONDS)
            self._next_attempt_at = time.monotonic() + delay

    def _reset_failures(self) -> None:
        self._failed_attempts = 0
        self._next_attempt_at = 0.0
