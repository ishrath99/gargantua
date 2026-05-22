"""Argon2id password hashing utilities.

Targets ``gargantua.auth.password`` — three pure functions, no DB:

    hash_password(plain) -> str   ::  argon2id hash, salted, time/mem-hardened.
    verify_password(plain, hashed) -> bool
    needs_rehash(hashed) -> bool  ::  True if the stored hash is older than the
                                       current argon2 parameters.
"""

from __future__ import annotations

import pytest


def test_hash_password_returns_argon2id_string() -> None:
    from gargantua.auth.password import hash_password

    h = hash_password("hunter2")
    assert h.startswith("$argon2id$"), "hash should be argon2id PHC-formatted"
    # Same input twice -> different hashes (salt randomness).
    assert hash_password("hunter2") != h


def test_verify_password_accepts_correct_plaintext() -> None:
    from gargantua.auth.password import hash_password, verify_password

    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_password_rejects_wrong_plaintext() -> None:
    from gargantua.auth.password import hash_password, verify_password

    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_verify_password_rejects_malformed_hash_without_raising() -> None:
    from gargantua.auth.password import verify_password

    # Garbage hash must not raise; just returns False so callers can treat it
    # as an authentication failure.
    assert verify_password("anything", "not-a-valid-hash") is False
    assert verify_password("anything", "") is False


def test_hash_rejects_empty_password() -> None:
    from gargantua.auth.password import hash_password

    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash_is_false_for_freshly_minted_hash() -> None:
    from gargantua.auth.password import hash_password, needs_rehash

    h = hash_password("whatever")
    assert needs_rehash(h) is False


def test_needs_rehash_is_true_for_weaker_params() -> None:
    """A hash made with old parameters should be flagged for rehash on next login."""
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16)
    legacy_hash = weak.hash("whatever")

    from gargantua.auth.password import needs_rehash

    assert needs_rehash(legacy_hash) is True
