"""AES-256-GCM helpers for at-rest secrets.

These tests exercise :mod:`gargantua.secrets` in isolation — no database,
no settings cache.  Each test passes an explicit KEK so the helpers
behave deterministically.

The on-disk shape is fixed by these tests:

    * ``iv``          == 12 bytes (AES-GCM standard nonce size)
    * ``kek_id``      == first 16 hex chars of SHA-256(KEK)
    * ``ciphertext``  == GCM ciphertext + 16-byte auth tag, concatenated.

Operators rely on those guarantees when they reach into the table
directly during incidents, so don't change the format casually.
"""

from __future__ import annotations

import base64
import os

import pytest

# Two distinct 32-byte keys we use across tests.
_KEY_A = bytes(range(32))
_KEY_B = bytes(reversed(range(32)))


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def test_kek_fingerprint_is_deterministic() -> None:
    from gargantua.secrets import kek_fingerprint

    assert kek_fingerprint(_KEY_A) == kek_fingerprint(_KEY_A)


def test_kek_fingerprint_differs_per_key() -> None:
    from gargantua.secrets import kek_fingerprint

    assert kek_fingerprint(_KEY_A) != kek_fingerprint(_KEY_B)


def test_kek_fingerprint_shape() -> None:
    """16 hex chars (64-bit prefix of SHA-256); fits in the kek_id column."""
    from gargantua.secrets import kek_fingerprint

    fp = kek_fingerprint(_KEY_A)
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# encrypt / decrypt round-trip with an explicit KEK
# ---------------------------------------------------------------------------


def test_encrypt_with_kek_returns_three_fields() -> None:
    from gargantua.secrets import encrypt_json_with_kek

    ciphertext, iv, kek_id = encrypt_json_with_kek({"a": 1, "b": "x"}, _KEY_A)
    assert isinstance(ciphertext, bytes) and len(ciphertext) > 0
    assert isinstance(iv, bytes) and len(iv) == 12
    assert isinstance(kek_id, str) and len(kek_id) == 16


def test_decrypt_with_kek_roundtrip() -> None:
    from gargantua.secrets import decrypt_json_with_kek, encrypt_json_with_kek

    payload = {"DATABASE_URI": "postgresql://...", "PORT": 5432, "ENABLED": True}
    ciphertext, iv, _ = encrypt_json_with_kek(payload, _KEY_A)
    assert decrypt_json_with_kek(ciphertext, iv, _KEY_A) == payload


def test_iv_is_fresh_per_encryption() -> None:
    """Two encryptions of the same plaintext must use distinct IVs."""
    from gargantua.secrets import encrypt_json_with_kek

    p = {"x": "y"}
    _, iv_a, _ = encrypt_json_with_kek(p, _KEY_A)
    _, iv_b, _ = encrypt_json_with_kek(p, _KEY_A)
    assert iv_a != iv_b


def test_encrypt_handles_empty_dict() -> None:
    from gargantua.secrets import decrypt_json_with_kek, encrypt_json_with_kek

    ciphertext, iv, _ = encrypt_json_with_kek({}, _KEY_A)
    assert decrypt_json_with_kek(ciphertext, iv, _KEY_A) == {}


def test_encrypt_handles_unicode() -> None:
    from gargantua.secrets import decrypt_json_with_kek, encrypt_json_with_kek

    payload = {"key": "héllo 🌍 wörld"}
    ciphertext, iv, _ = encrypt_json_with_kek(payload, _KEY_A)
    assert decrypt_json_with_kek(ciphertext, iv, _KEY_A) == payload


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


def test_decrypt_rejects_modified_ciphertext() -> None:
    from gargantua.secrets import decrypt_json_with_kek, encrypt_json_with_kek

    ciphertext, iv, _ = encrypt_json_with_kek({"a": 1}, _KEY_A)
    # Flip a bit in the middle of the ciphertext.
    tampered = bytearray(ciphertext)
    tampered[len(tampered) // 2] ^= 0x01

    with pytest.raises(Exception):  # InvalidTag from cryptography
        decrypt_json_with_kek(bytes(tampered), iv, _KEY_A)


def test_decrypt_rejects_modified_iv() -> None:
    from gargantua.secrets import decrypt_json_with_kek, encrypt_json_with_kek

    ciphertext, iv, _ = encrypt_json_with_kek({"a": 1}, _KEY_A)
    tampered_iv = bytearray(iv)
    tampered_iv[0] ^= 0x01

    with pytest.raises(Exception):
        decrypt_json_with_kek(ciphertext, bytes(tampered_iv), _KEY_A)


def test_decrypt_with_wrong_kek_raises() -> None:
    from gargantua.secrets import decrypt_json_with_kek, encrypt_json_with_kek

    ciphertext, iv, _ = encrypt_json_with_kek({"a": 1}, _KEY_A)
    with pytest.raises(Exception):
        decrypt_json_with_kek(ciphertext, iv, _KEY_B)


# ---------------------------------------------------------------------------
# Settings-bound helpers
# ---------------------------------------------------------------------------


def _reset_settings() -> None:
    from gargantua.settings import get_settings

    get_settings.cache_clear()


def test_active_kek_raises_when_master_key_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gargantua.secrets import MasterKeyNotConfigured, active_kek

    monkeypatch.delenv("MASTER_KEY", raising=False)
    _reset_settings()

    with pytest.raises(MasterKeyNotConfigured):
        active_kek()


def test_active_kek_decodes_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    from gargantua.secrets import active_kek

    raw = os.urandom(32)
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw).decode("ascii"))
    _reset_settings()

    assert active_kek() == raw


def test_active_kek_rejects_short_master_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AES-256 requires exactly 32 bytes — anything else is misconfigured."""
    from gargantua.secrets import InvalidMasterKey, active_kek

    too_short = os.urandom(16)
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(too_short).decode("ascii"))
    _reset_settings()

    with pytest.raises(InvalidMasterKey):
        active_kek()


def test_active_kek_rejects_non_base64_master_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gargantua.secrets import InvalidMasterKey, active_kek

    monkeypatch.setenv("MASTER_KEY", "not!!base64!!")
    _reset_settings()

    with pytest.raises(InvalidMasterKey):
        active_kek()


def test_encrypt_json_uses_active_kek(monkeypatch: pytest.MonkeyPatch) -> None:
    from gargantua.secrets import (
        decrypt_json_with_kek,
        encrypt_json,
        kek_fingerprint,
    )

    raw = os.urandom(32)
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw).decode("ascii"))
    _reset_settings()

    ciphertext, iv, kek_id = encrypt_json({"DATABASE_URI": "x"})
    assert kek_id == kek_fingerprint(raw)
    assert decrypt_json_with_kek(ciphertext, iv, raw) == {"DATABASE_URI": "x"}


def test_decrypt_json_with_matching_active_kek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gargantua.secrets import decrypt_json, encrypt_json

    raw = os.urandom(32)
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(raw).decode("ascii"))
    _reset_settings()

    ciphertext, iv, kek_id = encrypt_json({"k": "v"})
    assert decrypt_json(ciphertext, iv, kek_id) == {"k": "v"}


def test_decrypt_json_raises_kek_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decrypting a row encrypted under a different KEK must fail loudly.

    Operators should reach for ``rotate-kek``, not silently get garbage.
    """
    from gargantua.secrets import (
        KekMismatch,
        decrypt_json,
        encrypt_json_with_kek,
        kek_fingerprint,
    )

    active_raw = os.urandom(32)
    other_raw = os.urandom(32)
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(active_raw).decode("ascii"))
    _reset_settings()

    # Encrypted under a *different* key — kek_id won't match the active one.
    ciphertext, iv, _ = encrypt_json_with_kek({"x": 1}, other_raw)

    with pytest.raises(KekMismatch) as excinfo:
        decrypt_json(ciphertext, iv, kek_fingerprint(other_raw))
    # Error message should mention both fingerprints so the operator can act.
    assert kek_fingerprint(other_raw) in str(excinfo.value)
    assert kek_fingerprint(active_raw) in str(excinfo.value)
