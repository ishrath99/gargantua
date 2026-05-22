"""Repository layer — single source of truth for DB access patterns.

Every module here exposes a set of plain functions that take a
SQLAlchemy ``Session`` / ``AsyncSession`` and return ORM rows.  Callers
(HTTP routes, CLI commands, scheduled jobs) own the transaction:

* Repo functions **do not commit**.
* Repo functions **may flush** when they need to detect a server-side
  error early (e.g. unique-violation on insert).
* Repo functions raise typed exceptions for domain-level errors
  (``DuplicateUsername``, ``LastAdminError``, …) so callers translate
  them into the right HTTP status / CLI exit code.

This indirection keeps SQL out of routes and makes both the HTTP and
CLI surfaces share identical behaviour.
"""

from __future__ import annotations
