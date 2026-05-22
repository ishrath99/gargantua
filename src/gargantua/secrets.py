"""AES-256-GCM at-rest encryption for operator-supplied secrets.

We encrypt MCP server env vars and child-resource headers under a single
master key (the KEK).  The schema columns are:

* ``mcp_server.env_vars``         — ciphertext (LargeBinary)
* ``mcp_server.env_var_iv``       — 12-byte nonce
* ``mcp_server.env_var_kek_id``   — :func:`kek_fingerprint` of the KEK used

…and analogous columns on ``mcp_server_child_resource`` for ``headers``.

Why not classic envelope encryption (per-record DEK encrypted by KEK)?
Our payloads are small (env-var dicts, a handful of HTTP headers),
mostly written once and read often, and rotation is operator-initiated
(``rotate-kek``).  Direct AES-GCM under the KEK gets us:

* one IV per row (random per-encryption — never reuse for AES-GCM!),
* GCM's built-in authentication (tamper detection without an extra MAC),
* a deterministic ``kek_id`` so a row knows which KEK can decrypt it.

If we ever need per-record key isolation we can layer DEKs in later
without changing the column shape.

The wire format stored in the ``*_iv`` and ciphertext columns is fixed
by :mod:`tests.test_secrets`; treat it as a contract operators rely on.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from typing import Any, Final, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: AES-GCM requires a 96-bit (12-byte) nonce.
_IV_BYTES: Final[int] = 12

#: Length (in hex chars) of the KEK fingerprint we store as ``kek_id``.
#: 16 hex chars = 64 bits — collision-resistant for a tiny set of KEKs in
#: rotation, and well under the ``String(64)`` column limit.
_KEK_ID_HEX_CHARS: Final[int] = 16

#: AES-256 requires exactly 32 bytes of key material.
_KEK_BYTES: Final[int] = 32


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SecretsError(Exception):
    """Base class for all errors raised by :mod:`gargantua.secrets`."""


class MasterKeyNotConfigured(SecretsError):
    """``MASTER_KEY`` is empty.  Encryption / decryption can't run."""


class InvalidMasterKey(SecretsError):
    """``MASTER_KEY`` is set but not a base64-encoded 32-byte value."""


class KekMismatch(SecretsError):
    """The ciphertext was encrypted under a KEK that isn't the active one.

    Raised by :func:`decrypt_json` when the row's stored ``kek_id`` doesn't
    match the fingerprint of the currently configured master key.  The
    remedy is to run ``gargantua-admin rotate-kek`` to re-encrypt the row
    under the active KEK (or to load the matching legacy KEK and use the
    explicit-key helpers below).
    """


# ---------------------------------------------------------------------------
# Active-KEK accessors
# ---------------------------------------------------------------------------


def active_kek() -> bytes:
    """Return the active KEK bytes, decoding from base64 in settings.

    Raises:
        MasterKeyNotConfigured: when ``MASTER_KEY`` is unset / empty.
        InvalidMasterKey: when the value isn't valid base64 or isn't 32 bytes.
    """
    from gargantua.settings import get_settings

    raw = get_settings().master_key
    if not raw:
        raise MasterKeyNotConfigured(
            "MASTER_KEY is not set.  Generate one with "
            "`gargantua-admin generate-master-key --raw` and put it in your .env."
        )

    try:
        decoded = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidMasterKey("MASTER_KEY is set but is not valid base64.") from exc

    if len(decoded) != _KEK_BYTES:
        raise InvalidMasterKey(
            f"MASTER_KEY decoded to {len(decoded)} bytes; AES-256 requires exactly {_KEK_BYTES}."
        )
    return decoded


def kek_fingerprint(kek: bytes) -> str:
    """Return a stable, short identifier for a KEK.

    We use the first :data:`_KEK_ID_HEX_CHARS` hex chars of SHA-256(KEK).
    This is deterministic (same KEK -> same id), short enough for the
    ``kek_id`` column, and collision-resistant for the small number of
    KEKs an operator keeps around mid-rotation.

    Important: this is **not** a secret.  It's stored alongside every
    ciphertext so a row knows which KEK can decrypt it.  Treat it like a
    key id, not key material.
    """
    if len(kek) != _KEK_BYTES:
        raise InvalidMasterKey(f"KEK must be {_KEK_BYTES} bytes, got {len(kek)}")
    return hashlib.sha256(kek).hexdigest()[:_KEK_ID_HEX_CHARS]


def active_kek_fingerprint() -> str:
    """Convenience: fingerprint of whatever :func:`active_kek` returns."""
    return kek_fingerprint(active_kek())


# ---------------------------------------------------------------------------
# Encrypt / decrypt with an explicit KEK
# ---------------------------------------------------------------------------


def encrypt_json_with_kek(value: dict[str, Any], kek: bytes) -> tuple[bytes, bytes, str]:
    """Encrypt ``value`` (JSON-serialised) under ``kek`` using AES-256-GCM.

    Returns ``(ciphertext, iv, kek_id)``.  ``iv`` is a fresh random
    12-byte nonce (never reuse an IV under AES-GCM!).  ``ciphertext``
    includes the GCM authentication tag, so any tamper attempt at
    decrypt time raises :class:`~cryptography.exceptions.InvalidTag`.

    Use this for offline operations (``rotate-kek``, tests) where the
    KEK is passed explicitly rather than taken from settings.
    """
    if len(kek) != _KEK_BYTES:
        raise InvalidMasterKey(f"KEK must be {_KEK_BYTES} bytes, got {len(kek)}")

    iv = os.urandom(_IV_BYTES)
    aesgcm = AESGCM(kek)
    plaintext = json.dumps(value, separators=(",", ":")).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, associated_data=None)
    return ciphertext, iv, kek_fingerprint(kek)


def decrypt_json_with_kek(ciphertext: bytes, iv: bytes, kek: bytes) -> dict[str, Any]:
    """Inverse of :func:`encrypt_json_with_kek`.

    Raises:
        InvalidTag: ciphertext or IV was modified, or the wrong KEK was
            passed (AES-GCM treats all three as the same failure mode).
    """
    if len(kek) != _KEK_BYTES:
        raise InvalidMasterKey(f"KEK must be {_KEK_BYTES} bytes, got {len(kek)}")
    aesgcm = AESGCM(kek)
    plaintext = aesgcm.decrypt(iv, ciphertext, associated_data=None)
    return cast(dict[str, Any], json.loads(plaintext.decode("utf-8")))


# ---------------------------------------------------------------------------
# Encrypt / decrypt using the active KEK (the common case)
# ---------------------------------------------------------------------------


def encrypt_json(value: dict[str, Any]) -> tuple[bytes, bytes, str]:
    """Encrypt under the active master key (from settings).

    Routes / repo writes should use this; tests and offline tools
    typically prefer :func:`encrypt_json_with_kek` so they can isolate
    the KEK from env state.
    """
    return encrypt_json_with_kek(value, active_kek())


def decrypt_json(ciphertext: bytes, iv: bytes, kek_id: str) -> dict[str, Any]:
    """Decrypt under the active master key, asserting kek_id matches.

    Raises:
        KekMismatch: the row's ``kek_id`` doesn't match
            :func:`active_kek_fingerprint`.  The operator should either
            run ``rotate-kek`` to migrate the row, or temporarily switch
            ``MASTER_KEY`` to the legacy KEK and decrypt it offline.
    """
    active = active_kek_fingerprint()
    if kek_id != active:
        raise KekMismatch(
            f"Ciphertext was encrypted under KEK {kek_id!r} but the active "
            f"KEK is {active!r}.  Run `gargantua-admin rotate-kek --from-key "
            f"<old-base64> --to-key <new-base64>` to re-encrypt every row "
            f"under the new KEK."
        )
    return decrypt_json_with_kek(ciphertext, iv, active_kek())
