"""Argon2id password hashing.

Three pure helpers wrap ``argon2-cffi``'s :class:`PasswordHasher`:

  * :func:`hash_password` — generate a fresh PHC-formatted argon2id hash.
  * :func:`verify_password` — constant-time check that swallows malformed input.
  * :func:`needs_rehash`    — flag stored hashes whose parameters have drifted.

The parameters below follow OWASP's 2024 ``argon2id`` guidance (m=64 MiB,
t=3, p=4) and are deliberately documented here so a future bump to e.g.
m=96 MiB also bumps ``needs_rehash`` for existing stored credentials.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP 2024 baseline for argon2id.  See:
#     https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
)


def hash_password(plain: str) -> str:
    """Return a freshly-salted argon2id hash for *plain*.

    Raises ``ValueError`` if *plain* is empty.  We refuse empty passwords at
    the hashing boundary so an upstream bug can't silently create accounts
    with effectively no password.
    """
    if not plain:
        raise ValueError("password must be non-empty")
    return _HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return whether *plain* matches *hashed*.  Never raises."""
    if not plain or not hashed:
        return False
    try:
        return _HASHER.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """Whether *hashed* was produced with parameters weaker than the current ones.

    Call after a successful ``verify_password`` to opportunistically upgrade
    stored hashes when policy tightens.
    """
    try:
        return _HASHER.check_needs_rehash(hashed)
    except InvalidHashError:
        # Treat a malformed/unknown hash as "needs rehash" so the caller will
        # replace it on the next login.
        return True
