"""``gargantua-admin user`` and ``gargantua-admin audit`` subcommands.

Lives in its own module so :mod:`gargantua.admin` (the Typer entry point)
stays scannable.  Wired into the root Typer app via ``add_typer`` calls
inside ``admin.py``.

Design notes:

* Every command talks to Postgres directly through a sync SQLAlchemy
  engine built from ``Settings.database_url``.  This avoids a running
  HTTP server (ops-emergency friendly) and works even before the FastAPI
  app has been started for the first time (e.g. immediately after
  ``alembic upgrade head``).
* Mutating commands write an audit row with ``actor_id=None`` to mark the
  action as system-driven (no JWT in this context).
* Domain errors from the repo (``DuplicateUsername``, ``LastAdminError``,
  ``UserNotFound``) are translated to non-zero exit codes and a clear
  message on stderr.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

import typer
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from gargantua.db.models import User
from gargantua.repo import audit as audit_repo
from gargantua.repo import users as users_repo
from gargantua.settings import get_settings


user_app = typer.Typer(
    name="user",
    help="Manage users (create, list, change role, deactivate, activate).",
    no_args_is_help=True,
    add_completion=False,
)


audit_app = typer.Typer(
    name="audit",
    help="Read the audit log.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Engine + session helpers
# ---------------------------------------------------------------------------


_ENGINE: Engine | None = None


def _build_engine() -> Engine:
    """Build a sync SQLAlchemy engine targeting ``Settings.database_url``."""
    settings = get_settings()
    return create_engine(str(settings.database_url), future=True)


def _get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = _build_engine()
    return _ENGINE


def _reset_engine() -> None:
    """Drop the cached engine.  Used by tests after monkeypatching ``DATABASE_URL``."""
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None


@contextmanager
def _session() -> Iterator[Session]:
    """Yield a session bound to the cached engine; closed on exit."""
    sm = sessionmaker(bind=_get_engine(), expire_on_commit=False, future=True)
    with sm() as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_to_audit_dict(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
    }


def _print_user_row(user: User) -> None:
    state = "ACTIVE" if user.is_active else "INACTIVE"
    typer.echo(f"{user.id}  {user.role:<5}  {state:<8}  {user.username}")


def _print_user_header() -> None:
    typer.echo(
        f"{'id':<36}  {'role':<5}  {'state':<8}  username"
    )
    typer.echo("-" * 80)


# ---------------------------------------------------------------------------
# user create
# ---------------------------------------------------------------------------


@user_app.command("create")
def user_create(
    username: str = typer.Option(..., "--username", "-u", help="Unique username."),
    role: str = typer.Option(
        "user", "--role", "-r", help="Role: 'admin' or 'user'."
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help=(
            "Plaintext password.  Omit to be prompted interactively "
            "(recommended — avoids leaking into shell history)."
        ),
    ),
) -> None:
    """Insert a new user.

    Records a ``user.create`` audit entry attributed to the system
    (``actor_id = NULL``).  Fails with exit code 2 on duplicate
    usernames, 3 on invalid roles.
    """
    if password is None:
        password = typer.prompt(
            "Password", hide_input=True, confirmation_prompt=True
        )

    with _session() as s:
        try:
            user = users_repo.create_user(
                s, username=username, password=password, role=role
            )
        except users_repo.DuplicateUsername:
            typer.secho(
                f"User '{username}' already exists.", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=2)
        except users_repo.InvalidRole as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=3)

        audit_repo.record(
            s,
            actor_id=None,
            action="user.create",
            target_type="user",
            target_id=user.id,
            before=None,
            after=_user_to_audit_dict(user),
        )
        s.commit()

    typer.secho(f"Created user {user.username} (id={user.id})", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# user list
# ---------------------------------------------------------------------------


@user_app.command("list")
def user_list(
    role: str | None = typer.Option(
        None, "--role", "-r", help="Filter by role ('admin' or 'user')."
    ),
    search: str | None = typer.Option(
        None, "--search", "-s", help="Substring match on username."
    ),
    include_inactive: bool = typer.Option(
        False, "--include-inactive", help="Include deactivated users."
    ),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=500),
) -> None:
    """Print users matching the given filters."""
    with _session() as s:
        rows, total = users_repo.list_users(
            s,
            page=1,
            page_size=limit,
            role=role,
            search=search,
            include_inactive=include_inactive,
        )

    if not rows:
        typer.echo("No users matched the filter.")
        return

    _print_user_header()
    for u in rows:
        _print_user_row(u)
    typer.echo(f"\n{len(rows)} of {total} shown.")


# ---------------------------------------------------------------------------
# user set-role
# ---------------------------------------------------------------------------


@user_app.command("set-role")
def user_set_role(
    username: str = typer.Option(..., "--username", "-u"),
    role: str = typer.Option(..., "--role", "-r", help="'admin' or 'user'."),
) -> None:
    """Change a user's role.

    Refuses to demote the last active admin (exit code 4).
    """
    with _session() as s:
        user = users_repo.get_by_username(s, username)
        if user is None:
            typer.secho(
                f"No user named '{username}'.", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=2)

        before = _user_to_audit_dict(user)
        if user.role == role:
            typer.echo(f"User '{username}' already has role '{role}'; no change.")
            return

        try:
            user = users_repo.set_role(s, user_id=user.id, new_role=role)
        except users_repo.InvalidRole as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=3)
        except users_repo.LastAdminError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=4)

        audit_repo.record(
            s,
            actor_id=None,
            action="user.role_update",
            target_type="user",
            target_id=user.id,
            before=before,
            after=_user_to_audit_dict(user),
        )
        s.commit()

    typer.secho(
        f"Updated role for '{username}': {before['role']} -> {user.role}",
        fg=typer.colors.GREEN,
    )


# ---------------------------------------------------------------------------
# user deactivate / activate
# ---------------------------------------------------------------------------


def _set_active_command(username: str, *, is_active: bool) -> None:
    with _session() as s:
        user = users_repo.get_by_username(s, username)
        if user is None:
            typer.secho(
                f"No user named '{username}'.", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=2)

        before = _user_to_audit_dict(user)
        if user.is_active is is_active:
            verb = "active" if is_active else "inactive"
            typer.echo(f"User '{username}' is already {verb}; no change.")
            return

        try:
            user = users_repo.set_active(s, user_id=user.id, is_active=is_active)
        except users_repo.LastAdminError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=4)

        audit_repo.record(
            s,
            actor_id=None,
            action="user.activate" if is_active else "user.deactivate",
            target_type="user",
            target_id=user.id,
            before=before,
            after=_user_to_audit_dict(user),
        )
        s.commit()

    verb = "activated" if is_active else "deactivated"
    typer.secho(f"{verb.capitalize()} user '{username}'.", fg=typer.colors.GREEN)


@user_app.command("deactivate")
def user_deactivate(
    username: str = typer.Option(..., "--username", "-u"),
) -> None:
    """Set ``is_active=false`` on a user (login + refresh stop working)."""
    _set_active_command(username, is_active=False)


@user_app.command("activate")
def user_activate(
    username: str = typer.Option(..., "--username", "-u"),
) -> None:
    """Set ``is_active=true`` on a user (restores login + refresh)."""
    _set_active_command(username, is_active=True)


# ---------------------------------------------------------------------------
# audit list
# ---------------------------------------------------------------------------


@audit_app.command("list")
def audit_list(
    actor_id: UUID | None = typer.Option(None, "--actor-id"),
    target_type: str | None = typer.Option(None, "--target-type"),
    target_id: UUID | None = typer.Option(None, "--target-id"),
    action: str | None = typer.Option(None, "--action"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=500),
) -> None:
    """Print the most recent audit entries matching the filters."""
    with _session() as s:
        rows, total = audit_repo.list_audit(
            s,
            page=1,
            page_size=limit,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
        )

    if not rows:
        typer.echo("No audit entries matched the filter.")
        return

    typer.echo(
        f"{'id':<8}  {'when (UTC)':<20}  {'actor':<36}  {'action':<24}  target"
    )
    typer.echo("-" * 120)
    for r in rows:
        when = r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "-"
        actor = str(r.actor_id) if r.actor_id else "<system>"
        target = (
            f"{r.target_type}:{r.target_id}" if r.target_id else r.target_type
        )
        typer.echo(
            f"{r.id:<8}  {when:<20}  {actor:<36}  {r.action:<24}  {target}"
        )
    typer.echo(f"\n{len(rows)} of {total} shown.")
