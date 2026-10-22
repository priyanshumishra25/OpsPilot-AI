"""Argon2id password hashing.

Cost parameters are explicit (not library defaults): production uses the OWASP-recommended Argon2id minimum
(19 MiB memory, 2 iterations, 1 lane). `OPSPILOT_ARGON2_PROFILE=test` selects a cheap profile for test suites only.
Async callers must use `ahash_password` / `averify_password`, which run the CPU- and memory-heavy work in a worker
thread instead of blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# (memory_cost KiB, time_cost, parallelism)
_PROFILES: dict[str, tuple[int, int, int]] = {"default": (19 * 1024, 2, 1), "test": (1024, 1, 1)}
_memory, _time, _lanes = _PROFILES[os.environ.get("OPSPILOT_ARGON2_PROFILE", "default")]
_hasher = PasswordHasher(memory_cost=_memory, time_cost=_time, parallelism=_lanes)

# Verified against when the user does not exist, so login timing does not reveal account existence.
_DUMMY_HASH = _hasher.hash("opspilot-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def ahash_password(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def averify_password(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash uses different parameters than the configured profile."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
