"""Local sign-in credentials (SECURITY-UI-WORKORDER §2.2, §2.3).

This is **not** an account system. There is no server to register with,
nothing is transmitted anywhere, and the credential exists for exactly one
purpose: to unlock this install on a machine somebody else also uses. The
security boundary is still the token check on every `/api` route (S1a) — a
login screen that only gates the UI is cosmetic, because anything on the
machine can talk to the API directly. Signing in is a way to *obtain* the
token, not a substitute for checking it.

Three modes, per §2.2:

    none    the default. The server mints a token and injects it into the
            HTML it serves, so the desktop app is one click and still
            authenticated on the wire. No login screen.
    local   opt-in, for a shared machine. The login screen is shown and a
            correct credential is exchanged for the session token.
    off     `--no-auth`. No token at all. CI and the test suite's own
            negative cases; the CLI prints a banner naming what it disabled.

Storage: `<cache_root>/auth.json`, mode 0600, holding a per-install random
salt and an scrypt digest. Never a plaintext password, and never a bare
hash — a bare SHA-256 of a human-chosen password is recoverable at a rate
of billions of guesses a second on commodity hardware, which is the whole
reason memory-hard KDFs exist. `hashlib.scrypt` is in the standard library,
so this adds no dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

AUTH_FILE = "auth.json"

#: scrypt work factors. n=2^15 with r=8 costs ~32 MiB and ~100 ms per guess
#: on this class of machine — imperceptible on the one sign-in a session
#: actually performs, and ruinous for anyone grinding the file offline.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
#: OpenSSL refuses a memory cost it was not told to expect.
SCRYPT_MAXMEM = 256 * 1024 * 1024

SALT_BYTES = 16

#: Failed attempts before the throttle bites, and how long it holds off.
#: The point is not to stop a determined attacker — scrypt does that — but
#: to stop the local form being a free, fast oracle (§2.3).
FREE_ATTEMPTS = 3
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0


class AuthError(Exception):
    """A sign-in that should be reported to the person, not logged and
    swallowed. The message is written to be read by them."""


def auth_path(cache_root: str | os.PathLike) -> Path:
    return Path(cache_root) / AUTH_FILE


def has_credential(cache_root: str | os.PathLike) -> bool:
    return _read(cache_root) is not None


def _read(cache_root: str | os.PathLike) -> dict | None:
    try:
        with open(auth_path(cache_root), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or "hash" not in doc or "salt" not in doc:
        return None
    return doc


def _derive(password: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=params.get("n", SCRYPT_N), r=params.get("r", SCRYPT_R),
        p=params.get("p", SCRYPT_P), dklen=params.get("dklen", SCRYPT_DKLEN),
        maxmem=SCRYPT_MAXMEM)


def set_credential(cache_root: str | os.PathLike,
                   username: str, password: str) -> Path:
    """Write (or replace) this install's credential.

    Raises `AuthError` on anything a person can fix, so the CLI and the API
    can both surface the same text.
    """
    username = username.strip()
    if not username:
        raise AuthError("Username must not be empty.")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")

    salt = secrets.token_bytes(SALT_BYTES)
    params = {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
              "dklen": SCRYPT_DKLEN}
    doc = {
        "version": 1,
        "kdf": "scrypt",
        "username": username,
        "salt": salt.hex(),
        "hash": _derive(password, salt, params).hex(),
        **params,
    }

    path = auth_path(cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with the restrictive mode rather than chmod-ing afterwards:
    # between the two there is a window where the digest is world-readable.
    # (On Windows the mode is largely advisory, but the file also lives
    # under the user's own profile, which is where the cache already is.)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)
    return path


def verify(cache_root: str | os.PathLike, username: str,
           password: str) -> bool:
    """Is this the credential for this install?

    Returns False rather than raising for a wrong password: the caller
    decides what to tell the person, and it must not be which half was
    wrong. A username-only mismatch is compared anyway so that a wrong
    username does not answer measurably faster than a wrong password.
    """
    doc = _read(cache_root)
    if doc is None:
        return False
    try:
        salt = bytes.fromhex(doc["salt"])
        expected = bytes.fromhex(doc["hash"])
    except (ValueError, TypeError):
        return False
    got = _derive(password, salt, doc)
    ok_pw = hmac.compare_digest(got, expected)
    ok_user = hmac.compare_digest(str(doc.get("username", "")).encode(),
                                  username.strip().encode())
    return ok_pw and ok_user


class Throttle:
    """Exponential back-off on failed sign-ins.

    In memory on purpose: this bounds an *online* attacker, and an attacker
    who can restart the server to clear it can also read `auth.json`. The
    offline defence is scrypt, and it is the one that matters.
    """

    def __init__(self, now=time.monotonic):
        self._lock = threading.Lock()
        self._failures = 0
        self._blocked_until = 0.0
        self._now = now

    def retry_after(self) -> float:
        """Seconds the caller must wait, or 0 if it may try now."""
        with self._lock:
            return max(0.0, self._blocked_until - self._now())

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            over = self._failures - FREE_ATTEMPTS
            if over > 0:
                delay = min(BACKOFF_BASE_S * (2 ** (over - 1)), BACKOFF_MAX_S)
                self._blocked_until = self._now() + delay

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._blocked_until = 0.0
