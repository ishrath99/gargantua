"""Admin CLI for gargantua.

Top-level commands:

    generate-master-key       Print a freshly generated KEK (base64).
    generate-jwt-keys         Write an RS256 keypair to disk.
    rotate-kek                Re-encrypt all stored secret values under a new KEK.
    seed-catalog              Insert the canonical MCP-server-type catalog rows.

Subcommand groups:

    user                      Manage user rows (create, list, set-role, deactivate, activate).
    audit                     Read the audit log.

Invoked via the ``gargantua-admin`` entry point installed by ``pyproject.toml``,
or directly with ``python -m gargantua.admin <subcommand>``.
"""

from __future__ import annotations

import base64
import secrets
import sys
from pathlib import Path

import typer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gargantua.cli_admin import audit_app, user_app

app = typer.Typer(
    name="gargantua-admin",
    help="Administrative utilities for gargantua.",
    no_args_is_help=True,
    add_completion=False,
)

# Subcommand groups: `gargantua-admin user ...`, `gargantua-admin audit ...`.
app.add_typer(user_app, name="user")
app.add_typer(audit_app, name="audit")


# ---------------------------------------------------------------------------
# generate-master-key
# ---------------------------------------------------------------------------


@app.command("generate-master-key")
def generate_master_key(
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Print only the base64 key, no surrounding instructions.",
    ),
) -> None:
    """Generate a fresh 32-byte AES-256-GCM KEK and print it as base64.

    Paste the printed value into ``MASTER_KEY`` in your ``.env`` file. Treat
    this value like a root credential: rotate via ``rotate-kek`` rather than
    re-running this command (which would orphan every existing ciphertext).
    """
    key = secrets.token_bytes(32)
    encoded = base64.b64encode(key).decode("ascii")

    if raw:
        typer.echo(encoded)
        return

    typer.echo("")
    typer.secho("Generated a new MASTER_KEY (KEK).", fg=typer.colors.GREEN, bold=True)
    typer.echo("Paste the following into your .env:")
    typer.echo("")
    typer.echo(f"  MASTER_KEY={encoded}")
    typer.echo("")
    typer.secho(
        "WARNING: losing this key means every encrypted secret in the database is "
        "unrecoverable. Back it up alongside (but separate from) your DB backups.",
        fg=typer.colors.YELLOW,
    )


# ---------------------------------------------------------------------------
# generate-jwt-keys
# ---------------------------------------------------------------------------


@app.command("generate-jwt-keys")
def generate_jwt_keys(
    out_dir: Path = typer.Option(
        Path("./secrets"),
        "--out-dir",
        "-o",
        help="Directory to write jwt_private.pem and jwt_public.pem.",
    ),
    private_name: str = typer.Option("jwt_private.pem", "--private-name"),
    public_name: str = typer.Option("jwt_public.pem", "--public-name"),
    key_size: int = typer.Option(
        2048,
        "--key-size",
        help="RSA key size in bits.",
        min=2048,
        max=4096,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing key files if they exist.",
    ),
) -> None:
    """Generate an RS256 (RSA) keypair for signing/verifying access tokens.

    The private key is used by ``/auth/login`` to mint JWTs. The public key is
    handed to Agno's ``AgentOS(authorization_config=...)`` so it can verify the
    bearer token on every request.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / private_name
    public_path = out_dir / public_name

    if (private_path.exists() or public_path.exists()) and not force:
        typer.secho(
            f"Refusing to overwrite existing key files in {out_dir} (use --force).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)
    public_path.write_bytes(public_pem)
    public_path.chmod(0o644)

    typer.echo("")
    typer.secho("Generated a new RS256 JWT keypair.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Private key: {private_path}")
    typer.echo(f"  Public key:  {public_path}")
    typer.echo("")
    typer.echo("Update .env if the paths differ from the defaults:")
    typer.echo(f"  JWT_PRIVATE_KEY_PATH={private_path}")
    typer.echo(f"  JWT_PUBLIC_KEY_PATH={public_path}")


# ---------------------------------------------------------------------------
# rotate-kek
# ---------------------------------------------------------------------------


@app.command("rotate-kek")
def rotate_kek(
    from_key_b64: str = typer.Option(
        ...,
        "--from-key",
        help=(
            "Base64-encoded current KEK (the one every stored secret is "
            "presently encrypted under)."
        ),
    ),
    to_key_b64: str = typer.Option(
        ...,
        "--to-key",
        help=(
            "Base64-encoded new KEK.  Generate with "
            "`gargantua-admin generate-master-key --raw`."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report what would be rotated without writing to the DB.",
    ),
) -> None:
    """Re-encrypt every at-rest secret under a new KEK.

    Run with the app stopped (or at least with no admins writing
    secrets) so an in-flight write can't end up encrypted under the old
    KEK after the rotation finishes.

    Exit codes:

    * ``0`` — success.
    * ``2`` — bad input (invalid base64, wrong key length, identical keys).
    * ``3`` — a row was found under a KEK that's neither --from-key nor
      --to-key; investigate before re-running.
    """
    import base64

    from gargantua.cli_admin import _session  # reuse the sync engine cache
    from gargantua.rotation import rotate_all_secrets
    from gargantua.secrets import InvalidMasterKey, KekMismatch

    try:
        from_key = base64.b64decode(from_key_b64, validate=True)
        to_key = base64.b64decode(to_key_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        typer.secho(
            f"Invalid base64 in --from-key or --to-key: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if len(from_key) != 32 or len(to_key) != 32:
        typer.secho(
            "Both keys must decode to exactly 32 bytes (AES-256).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if from_key == to_key:
        typer.secho(
            "--from-key and --to-key are identical; nothing to do.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        with _session() as s:
            report = rotate_all_secrets(
                s, from_key=from_key, to_key=to_key, dry_run=dry_run
            )
            if not dry_run:
                s.commit()
            else:
                s.rollback()
    except KekMismatch as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3)
    except InvalidMasterKey as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    typer.echo("")
    typer.echo(str(report))
    typer.echo("")
    if dry_run:
        typer.secho(
            f"Dry run: would rotate {report.total_rotated} row(s).  "
            f"Re-run without --dry-run to apply.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            f"Done — rotated {report.total_rotated} row(s).  "
            f"Update MASTER_KEY in your environment to the new KEK and "
            f"restart the app.",
            fg=typer.colors.GREEN,
        )


# ---------------------------------------------------------------------------
# seed-catalog
# ---------------------------------------------------------------------------


@app.command("seed-catalog")
def seed_catalog(
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help=(
            "If a canonical type already exists with drifted fields, "
            "update it back to the canonical definition.  Rows that "
            "match exactly are left alone; no audit row is written for "
            "a no-op update."
        ),
    ),
) -> None:
    """Insert the canonical MCP-server-type catalog rows.

    Idempotent by ``slug``: re-running with the same canonical set
    is a no-op.  System-driven actions get ``actor_id = NULL`` in the
    audit log.  Run after ``alembic upgrade head`` on a fresh install,
    or after a release that bumps the canonical catalog.
    """
    from gargantua.catalog_seed import CANONICAL_TYPES
    from gargantua.cli_admin import _session
    from gargantua.repo import audit as audit_repo
    from gargantua.repo import mcp_server_types as types_repo

    inserted = 0
    updated = 0
    skipped = 0

    with _session() as s:
        for spec in CANONICAL_TYPES:
            existing = types_repo.get_by_slug(s, spec["slug"])

            if existing is None:
                # Fresh insert -> create + audit.
                row = types_repo.create(s, **spec)
                audit_repo.record(
                    s,
                    actor_id=None,
                    action="mcp_server_type.create",
                    target_type="mcp_server_type",
                    target_id=row.id,
                    before=None,
                    after=_type_dict_for_audit(row),
                )
                inserted += 1
                continue

            if not overwrite:
                skipped += 1
                continue

            # Overwrite path — only writes if at least one canonical
            # field differs from the stored row.
            changes = _collect_drifts(existing, spec)
            if not changes:
                skipped += 1
                continue

            before = _type_dict_for_audit(existing)
            row = types_repo.update(s, type_id=existing.id, **changes)
            audit_repo.record(
                s,
                actor_id=None,
                action="mcp_server_type.update",
                target_type="mcp_server_type",
                target_id=row.id,
                before=before,
                after=_type_dict_for_audit(row),
            )
            updated += 1

        s.commit()

    typer.echo("")
    typer.secho(
        f"Catalog seed complete: {inserted} inserted, {updated} updated, "
        f"{skipped} skipped (already canonical).",
        fg=typer.colors.GREEN,
    )


def _collect_drifts(existing, spec: dict) -> dict:
    """Return only the spec fields that differ from the existing row.

    Used by ``--overwrite`` to write the smallest possible update,
    keep audit rows precise, and avoid version-bumping a row whose
    state already matches the canonical definition.
    """
    candidates = {
        "name": spec.get("name"),
        "description": spec.get("description"),
        "mode": spec.get("mode"),
        "default_command": spec.get("default_command"),
        "default_args": spec.get("default_args", []),
        "config_schema": spec.get("config_schema", []),
        "default_env_vars": spec.get("default_env_vars", {}),
        "optional_env_vars": spec.get("optional_env_vars", {}),
        "default_swagger_url": spec.get("default_swagger_url"),
        "supports_swagger_child": spec.get("supports_swagger_child", False),
    }
    drifts = {}
    for k, v in candidates.items():
        if getattr(existing, k) != v:
            drifts[k] = v
    return drifts


def _type_dict_for_audit(row) -> dict:
    """Mirror of the route-side audit projection but local to the CLI."""
    return {
        "id": str(row.id),
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "mode": row.mode,
        "default_command": row.default_command,
        "default_args": row.default_args,
        "config_schema": row.config_schema,
        "default_env_vars": row.default_env_vars,
        "optional_env_vars": row.optional_env_vars,
        "default_swagger_url": row.default_swagger_url,
        "supports_swagger_child": row.supports_swagger_child,
        "version": row.version,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


if __name__ == "__main__":  # pragma: no cover — exercised via `python -m`.
    sys.exit(app())
