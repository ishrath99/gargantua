"""RS256 JWT mint + verify.

Targets ``gargantua.auth.jwt``:

    mint_access(subject, scopes, *, key, ttl_seconds, iss, aud) -> str
    mint_refresh(subject, *,        key, ttl_seconds, iss, aud) -> str
    decode(token, *, key, iss, aud) -> dict          # raises InvalidToken on failure

The settings layer threads issuer / audience / TTLs in.  These tests pin the
*shape* of the produced token (claims, expiry, type) and the *behaviour*
(expiry rejection, audience/issuer enforcement, signature wrong-key rejection).
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="module")
def keypair(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """A single RS256 keypair shared by every test in this module."""
    d = tmp_path_factory.mktemp("jwt-keys")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (d / "priv.pem").write_bytes(private)
    (d / "pub.pem").write_bytes(public)
    return {"private": private, "public": public, "dir": str(d)}


@pytest.fixture
def other_public_key() -> bytes:
    """An unrelated public key used to assert signature checks."""
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# ---------------------------------------------------------------------------
# mint_access
# ---------------------------------------------------------------------------


def test_mint_access_round_trips_subject_and_scopes(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import decode, mint_access

    token = mint_access(
        subject="alice",
        scopes=["agent_os:admin", "agent_os:user"],
        private_key=keypair["private"],
        ttl_seconds=300,
        issuer="gargantua",
        audience="gargantua",
    )
    claims = decode(token, public_key=keypair["public"], issuer="gargantua", audience="gargantua")
    assert claims["sub"] == "alice"
    assert claims["scopes"] == ["agent_os:admin", "agent_os:user"]
    assert claims["iss"] == "gargantua"
    assert claims["aud"] == "gargantua"
    assert claims["typ"] == "access"
    assert claims["exp"] > claims["iat"]


def test_mint_access_uses_rs256_header(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import mint_access

    token = mint_access(
        subject="alice",
        scopes=[],
        private_key=keypair["private"],
        ttl_seconds=60,
        issuer="gargantua",
        audience="gargantua",
    )
    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"


def test_mint_refresh_marks_typ_refresh(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import decode, mint_refresh

    token = mint_refresh(
        subject="alice",
        private_key=keypair["private"],
        ttl_seconds=600,
        issuer="gargantua",
        audience="gargantua",
    )
    claims = decode(token, public_key=keypair["public"], issuer="gargantua", audience="gargantua")
    assert claims["typ"] == "refresh"
    assert claims["sub"] == "alice"
    assert "scopes" not in claims, "refresh tokens carry no authority of their own"


# ---------------------------------------------------------------------------
# decode failure modes
# ---------------------------------------------------------------------------


def test_decode_rejects_expired_token(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import InvalidToken, decode, mint_access

    token = mint_access(
        subject="alice",
        scopes=[],
        private_key=keypair["private"],
        ttl_seconds=1,
        issuer="gargantua",
        audience="gargantua",
    )
    time.sleep(1.2)
    with pytest.raises(InvalidToken):
        # leeway=0 — exercise strict expiry semantics; the production default
        # of 10s would still accept a token that ran out 1.2s ago.
        decode(
            token,
            public_key=keypair["public"],
            issuer="gargantua",
            audience="gargantua",
            leeway=0,
        )


def test_decode_rejects_wrong_audience(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import InvalidToken, decode, mint_access

    token = mint_access(
        subject="alice",
        scopes=[],
        private_key=keypair["private"],
        ttl_seconds=60,
        issuer="gargantua",
        audience="some-other-app",
    )
    with pytest.raises(InvalidToken):
        decode(token, public_key=keypair["public"], issuer="gargantua", audience="gargantua")


def test_decode_rejects_wrong_issuer(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import InvalidToken, decode, mint_access

    token = mint_access(
        subject="alice",
        scopes=[],
        private_key=keypair["private"],
        ttl_seconds=60,
        issuer="wrong-iss",
        audience="gargantua",
    )
    with pytest.raises(InvalidToken):
        decode(token, public_key=keypair["public"], issuer="gargantua", audience="gargantua")


def test_decode_rejects_wrong_signature(keypair: dict[str, bytes], other_public_key: bytes) -> None:
    from gargantua.auth.jwt import InvalidToken, decode, mint_access

    token = mint_access(
        subject="alice",
        scopes=[],
        private_key=keypair["private"],
        ttl_seconds=60,
        issuer="gargantua",
        audience="gargantua",
    )
    with pytest.raises(InvalidToken):
        decode(token, public_key=other_public_key, issuer="gargantua", audience="gargantua")


def test_decode_rejects_garbage_token(keypair: dict[str, bytes]) -> None:
    from gargantua.auth.jwt import InvalidToken, decode

    with pytest.raises(InvalidToken):
        decode("not.a.jwt", public_key=keypair["public"], issuer="gargantua", audience="gargantua")
