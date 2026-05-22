"""Offline KEK rotation: re-encrypt every at-rest secret under a new KEK.

Invoked by ``gargantua-admin rotate-kek``.  The flow is:

1. Operator generates a new KEK (e.g. ``generate-master-key --raw``).
2. Operator runs ``rotate-kek --from-key <old> --to-key <new>`` while the
   app is stopped or has secret-writing routes paused.  We don't enforce
   a global lock here — concurrent writes during rotation would be a
   real footgun, but trying to enforce it from the CLI would be brittle.
3. Operator swaps ``MASTER_KEY`` to the new KEK and restarts the app.

The rotation walks every row that holds ciphertext, decrypts with the
old key, re-encrypts with the new key, writes ciphertext / iv / kek_id
in place.  Everything happens inside a single SQLAlchemy transaction so
a mid-way failure rolls every row back — no half-rotated state.

Currently two tables hold secrets:

    * ``ai.mcp_server.env_vars`` (+ ``env_var_iv`` + ``env_var_kek_id``)
    * ``ai.mcp_server_child_resource.headers`` (+ ``headers_iv`` + ``headers_kek_id``)

If a future migration adds more, extend :func:`_encrypted_columns_by_table`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gargantua.db.models import MCPServer, MCPServerChildResource
from gargantua.secrets import (
    InvalidMasterKey,
    KekMismatch,
    decrypt_json_with_kek,
    encrypt_json_with_kek,
    kek_fingerprint,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RotationReport:
    """Per-run summary handed back to the CLI for human-readable output."""

    mcp_server_rotated: int = 0
    mcp_server_skipped_empty: int = 0
    mcp_server_skipped_already_new: int = 0
    child_resource_rotated: int = 0
    child_resource_skipped_empty: int = 0
    child_resource_skipped_already_new: int = 0
    dry_run: bool = False

    @property
    def total_rotated(self) -> int:
        return self.mcp_server_rotated + self.child_resource_rotated

    def __str__(self) -> str:
        verb = "would rotate" if self.dry_run else "rotated"
        return (
            f"mcp_server: {verb} {self.mcp_server_rotated}, "
            f"skipped empty {self.mcp_server_skipped_empty}, "
            f"already on new KEK {self.mcp_server_skipped_already_new}\n"
            f"mcp_server_child_resource: {verb} {self.child_resource_rotated}, "
            f"skipped empty {self.child_resource_skipped_empty}, "
            f"already on new KEK {self.child_resource_skipped_already_new}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rotate_all_secrets(
    session: Session,
    *,
    from_key: bytes,
    to_key: bytes,
    dry_run: bool = False,
) -> RotationReport:
    """Walk every encrypted row and rotate it from ``from_key`` to ``to_key``.

    Caller owns the transaction: this function flushes but never commits.
    The CLI commits if ``dry_run`` is False; tests use the same contract.

    Behaviour rules:

    * **Empty rows** (no ciphertext) are skipped.
    * Rows whose stored ``kek_id`` already matches ``to_key`` are skipped
      (idempotent — running the same rotation twice does nothing the
      second time).
    * Rows whose ``kek_id`` matches neither key raise
      :class:`KekMismatch`.  The CLI surfaces this with a non-zero exit
      code so the operator can investigate before the rotation
      half-finishes.
    * On any error mid-rotation we let the exception propagate.  Callers
      that own the txn should roll back.
    """
    if from_key == to_key:
        raise ValueError("from_key and to_key are identical; nothing to rotate")

    from_fp = kek_fingerprint(from_key)  # also validates length
    to_fp = kek_fingerprint(to_key)

    logger.info(
        "rotate-kek: %s from KEK %s to KEK %s",
        "DRY RUN" if dry_run else "applying",
        from_fp,
        to_fp,
    )

    server_stats = _rotate_table(
        session,
        model=MCPServer,
        ciphertext_attr="env_vars",
        iv_attr="env_var_iv",
        kek_id_attr="env_var_kek_id",
        from_key=from_key,
        to_key=to_key,
        from_fp=from_fp,
        to_fp=to_fp,
        dry_run=dry_run,
    )
    child_stats = _rotate_table(
        session,
        model=MCPServerChildResource,
        ciphertext_attr="headers",
        iv_attr="headers_iv",
        kek_id_attr="headers_kek_id",
        from_key=from_key,
        to_key=to_key,
        from_fp=from_fp,
        to_fp=to_fp,
        dry_run=dry_run,
    )

    return RotationReport(
        mcp_server_rotated=server_stats[0],
        mcp_server_skipped_empty=server_stats[1],
        mcp_server_skipped_already_new=server_stats[2],
        child_resource_rotated=child_stats[0],
        child_resource_skipped_empty=child_stats[1],
        child_resource_skipped_already_new=child_stats[2],
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Per-table worker
# ---------------------------------------------------------------------------


def _rotate_table(
    session: Session,
    *,
    model: type,
    ciphertext_attr: str,
    iv_attr: str,
    kek_id_attr: str,
    from_key: bytes,
    to_key: bytes,
    from_fp: str,
    to_fp: str,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Rotate one table.  Returns ``(rotated, skipped_empty, skipped_already_new)``."""
    rotated = 0
    skipped_empty = 0
    skipped_already_new = 0

    # Stream every row.  Tables holding at-rest secrets are operator-
    # curated configuration data, never user-generated; sizes stay small
    # enough that loading the row set into the session is fine.
    rows = session.execute(select(model)).scalars().all()

    for row in rows:
        ciphertext = getattr(row, ciphertext_attr)
        iv = getattr(row, iv_attr)
        kek_id = getattr(row, kek_id_attr)

        if ciphertext is None and iv is None and kek_id is None:
            skipped_empty += 1
            continue

        if ciphertext is None or iv is None or kek_id is None:
            raise InvalidMasterKey(
                f"{model.__tablename__} row {row.id!r}: "
                f"{ciphertext_attr}/{iv_attr}/{kek_id_attr} are inconsistent "
                f"(some null, some not).  Refusing to rotate ambiguous data."
            )

        if kek_id == to_fp:
            # Already rotated — idempotent path.
            skipped_already_new += 1
            continue

        if kek_id != from_fp:
            raise KekMismatch(
                f"{model.__tablename__} row {row.id!r} is encrypted under KEK "
                f"{kek_id!r}, which matches neither --from-key ({from_fp}) "
                f"nor --to-key ({to_fp}).  Investigate before re-running."
            )

        plaintext = decrypt_json_with_kek(ciphertext, iv, from_key)
        new_ct, new_iv, new_fp = encrypt_json_with_kek(plaintext, to_key)

        if not dry_run:
            session.execute(
                update(model)
                .where(model.id == row.id)
                .values(
                    {
                        ciphertext_attr: new_ct,
                        iv_attr: new_iv,
                        kek_id_attr: new_fp,
                    }
                )
            )
        rotated += 1

    if not dry_run:
        session.flush()
    logger.info(
        "rotate-kek/%s: rotated=%d skipped_empty=%d skipped_already_new=%d",
        model.__tablename__,
        rotated,
        skipped_empty,
        skipped_already_new,
    )
    return rotated, skipped_empty, skipped_already_new
