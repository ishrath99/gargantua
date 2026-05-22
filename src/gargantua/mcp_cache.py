"""Process-wide cache of warm MCP tool handles.

Why this exists
---------------

Spawning an MCP server is expensive — for ``stdio`` types it forks a
subprocess and shakes hands over JSON-RPC; for ``sse`` /
``streamable_http`` it opens a long-lived HTTP connection.  Every agent
run that needs the same MCP server should reuse the same warm handle
instead of paying that cost again, and concurrent runs on the same
server should not race two separate spawns.

What this module does
---------------------

* **One handle per ``(server_id, child_resource_ids, version)``** —
  server rows carry a monotonic ``version`` column bumped on every
  edit; the cache key is the tuple ``(server_id, sorted_child_ids)``
  so two agents with the same parent MCP server but different
  per-agent child resource filters get distinct warm handles (a
  single handle can't expose two different tool surfaces).  When the
  cached version drifts from the DB, the next ``acquire`` rebuilds
  the entry for *that* key without touching sibling keys.
* **Per-key lock** — concurrent acquires on the same
  ``(server_id, child_resource_ids)`` serialize through an
  ``asyncio.Lock`` so a build only happens once, even under high
  concurrency.
* **Ref-counted leases** — every ``acquire`` returns a :class:`Lease`
  that the caller releases.  An entry is only eligible for the reaper
  once its ref-count drops to zero.
* **Orphan handling on version bump** — if a row is edited while
  callers still hold the old handle, the old entry is *detached* (no
  longer routes acquires) but kept alive until its last lease is
  released, then closed in the background.  This prevents the kind of
  "yanked the rug" bug where an in-flight agent run loses its tools.
* **Idle reaper** — a background task scans for entries with
  ``ref_count == 0`` whose ``last_used`` is older than
  ``idle_ttl`` and closes them.
* **Admin evict** — operators can force-close a handle (e.g. a
  misbehaving SSE connection) via :meth:`evict`, which closes
  **every** entry for a given ``server_id`` (across all child
  resource variants) regardless of outstanding leases.  Subsequent
  ``Lease.release()`` calls become no-ops since the entry is gone.

Dependency injection
--------------------

The cache is deliberately **decoupled** from the database and from
Agno.  Construction takes a single ``row_fetcher`` callable that
returns a :class:`BuildPlan` (a version number + a factory closure
that produces the actual tools handle).  Tests inject in-memory fakes;
production wires the fetcher to the DB + ``decrypt_env_vars`` + the
Agno ``MCPTools`` constructor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@runtime_checkable
class Closeable(Protocol):
    """Anything that owns external resources and can be cleaned up."""

    async def close(self) -> None: ...  # pragma: no cover — protocol


@dataclass(frozen=True)
class BuildPlan:
    """What ``row_fetcher`` returns for a known server.

    The ``factory`` closure embeds everything the cache shouldn't know
    about (decrypted env_vars, parent type info, transport mode, ...)
    and is only invoked when the cache decides a rebuild is needed.
    """

    version: int
    factory: Callable[[], Awaitable[Closeable]]


@dataclass(frozen=True)
class CacheSnapshot:
    """Read-only projection of one cache entry for ``/admin/mcp-cache``.

    Includes orphan entries (post-version-bump handles that are still
    waiting for the last lease to release) — operators benefit from
    seeing them so a stuck lease can be diagnosed.

    ``child_resource_ids`` is the (sorted) set of child resources this
    entry is bound to.  An empty list means the entry serves agents
    that don't reference any child resources for this server; a
    non-empty list means the entry is bound to a specific filter set.
    Two snapshots with the same ``server_id`` but different
    ``child_resource_ids`` are distinct entries.
    """

    server_id: UUID
    child_resource_ids: list[UUID]
    version: int
    ref_count: int
    last_used: datetime
    is_orphan: bool


class MCPCacheError(Exception):
    """Base class for typed errors raised by this module."""


class ServerNotFound(MCPCacheError):
    """``row_fetcher`` returned ``None`` for the requested ``server_id``."""


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


# Composite cache key: server_id plus the sorted tuple of child
# resource ids that scope the warm handle.  Empty tuple = bare server
# (no child filter).  Sorted form gives canonical hashing so
# ``(c1, c2)`` and ``(c2, c1)`` collapse to the same key.
_CacheKey = tuple[UUID, tuple[UUID, ...]]


def _make_key(server_id: UUID, child_resource_ids: tuple[UUID, ...]) -> _CacheKey:
    return (server_id, tuple(sorted(child_resource_ids)))


@dataclass
class _Entry:
    """A single cached tools handle.

    Lifecycle: created on first build, moved to ``_orphans`` on
    version bump, removed entirely once the deferred close runs.
    """

    server_id: UUID
    child_resource_ids: tuple[UUID, ...]
    version: int
    tools: Closeable
    ref_count: int
    last_used: datetime
    is_orphan: bool = False
    # Bound to the cache so ``Lease.release`` can operate without
    # holding a back-reference to the whole cache object.  Set by
    # ``MCPCache._install_entry``.
    _on_release: Callable[[_Entry], Awaitable[None]] | None = None


@dataclass
class Lease:
    """Handle returned by :meth:`MCPCache.acquire`.

    Pins the release to a specific cache entry so that a release issued
    *after* a version bump correctly decrements the orphaned old entry,
    not the freshly-built new one.

    ``release()`` is idempotent and safe to call after the underlying
    entry has been evicted or closed at shutdown.
    """

    tools: Closeable
    _entry: _Entry | None = field(repr=False, default=None)

    async def release(self) -> None:
        entry = self._entry
        if entry is None:
            return
        self._entry = None  # idempotent
        on_release = entry._on_release
        if on_release is None:
            return
        await on_release(entry)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


RowFetcher = Callable[[UUID, tuple[UUID, ...]], Awaitable["BuildPlan | None"]]
"""Signature: ``(server_id, child_resource_ids) -> BuildPlan | None``.

Returning ``None`` means the server (or the parent of one of the
children) is missing / archived; the cache raises
:class:`ServerNotFound`.

The ``child_resource_ids`` arg is passed through verbatim from
:meth:`MCPCache.acquire`.  Production fetchers use it to load + decrypt
the child resource rows and pack them into the build factory closure;
test fetchers can ignore it.
"""

Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    return datetime.now(tz=UTC)


class MCPCache:
    """Process-wide cache of warm MCP tool handles.

    Construction is cheap; call :meth:`start` from the app lifespan to
    spin up the idle reaper, and :meth:`stop` on shutdown to drain.
    """

    def __init__(
        self,
        *,
        row_fetcher: RowFetcher,
        idle_ttl: timedelta = timedelta(minutes=5),
        reap_interval: timedelta = timedelta(seconds=30),
        clock: Clock = _default_clock,
    ) -> None:
        self._fetch = row_fetcher
        self._idle_ttl = idle_ttl
        self._reap_interval = reap_interval
        self._clock = clock

        # Current entries: at most one per (server_id, child_set).
        # Orphans live outside this map.
        self._entries: dict[_CacheKey, _Entry] = {}
        self._orphans: list[_Entry] = []

        # Per-key locks gate ``acquire`` so two callers can't both
        # trigger a build for the same (server_id, child_set).
        # Created lazily; never deleted (the keyspace is bounded by
        # ``MCP servers x distinct child sets`` so leak is benign).
        self._key_locks: dict[_CacheKey, asyncio.Lock] = {}
        # Coarse lock around the maps themselves.  Held only for the
        # brief window of looking up / inserting per-key locks and
        # snapshotting the entries dict.  All slow work (building
        # tools, closing) happens *outside* this lock.
        self._mutex = asyncio.Lock()

        self._reaper_task: asyncio.Task[None] | None = None
        # Fire-and-forget orphan-close tasks. We keep a strong reference so
        # they can't be garbage-collected mid-await; the done-callback drops
        # the entry once the close has finished.
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._closing = False

    # ----------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Spin up the idle-reaper background task.  Idempotent."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        self._closing = False
        self._reaper_task = asyncio.create_task(self._reaper_loop(), name="mcp-cache-reaper")

    async def stop(self) -> None:
        """Cancel the reaper, close every live handle, mark the cache closed.

        After ``stop()`` returns, any subsequent :meth:`acquire` raises
        ``RuntimeError`` — by that point we're in shutdown and the
        application should be tearing down anyway.
        """
        self._closing = True

        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None

        async with self._mutex:
            doomed = list(self._entries.values()) + list(self._orphans)
            self._entries.clear()
            self._orphans.clear()

        await self._close_handles(doomed)

        # Drain any in-flight orphan-close tasks scheduled by _orphan_locked
        # before the caller proceeds with the rest of the shutdown.
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    # ------------------------------------------------------------- acquire

    async def acquire(
        self,
        server_id: UUID,
        child_resource_ids: tuple[UUID, ...] = (),
    ) -> Lease:
        """Build-or-return the warm handle for ``(server_id, child_resource_ids)``.

        ``child_resource_ids`` defaults to ``()`` (no child filter) so
        existing call sites that don't care about child resources keep
        working unchanged.  A non-empty value scopes the cache entry
        to that specific filter set — two agents with the same parent
        server but different child sets get distinct handles.

        Raises :class:`ServerNotFound` if the row fetcher returns
        ``None`` (server was deleted / disabled / never existed), or
        ``RuntimeError`` if the cache is already stopped.
        """
        if self._closing:
            raise RuntimeError("MCPCache has been stopped")

        # Normalize the child set so callers don't have to worry about
        # ordering: ``(c1, c2)`` and ``(c2, c1)`` collapse to the same
        # canonical key.
        normalized_children = tuple(sorted(child_resource_ids))
        key = (server_id, normalized_children)

        plan = await self._fetch(server_id, normalized_children)
        if plan is None:
            raise ServerNotFound(str(server_id))

        # Per-key lock serializes acquires on the same key (so a build
        # only runs once even under concurrency).
        key_lock = await self._get_key_lock(key)
        async with key_lock:
            # Re-fetch under the lock?  Not necessary — we tolerate a
            # microsecond-stale version; the next acquire will catch
            # any bump that landed in the meantime.
            entry = self._entries.get(key)
            if entry is None or entry.version != plan.version:
                if entry is not None:
                    # Version changed (or entry disappeared between
                    # the get and now — possible only via evict).
                    self._orphan_locked(entry)
                tools = await plan.factory()
                entry = self._install_entry(server_id, normalized_children, plan.version, tools)
            entry.ref_count += 1
            entry.last_used = self._clock()
            return Lease(tools=entry.tools, _entry=entry)

    @contextlib.asynccontextmanager
    async def lease(
        self,
        server_id: UUID,
        child_resource_ids: tuple[UUID, ...] = (),
    ) -> AsyncIterator[Closeable]:
        """Async-context-manager wrapper around :meth:`acquire`.

        Releases on normal exit *and* on exception, so callers don't
        have to write the try/finally themselves.
        """
        leased = await self.acquire(server_id, child_resource_ids)
        try:
            yield leased.tools
        finally:
            await leased.release()

    # -------------------------------------------------------------- evict

    async def evict(self, server_id: UUID) -> bool:
        """Force-close **every** cached handle for ``server_id``.

        Crosses all child-resource variants: if the server has three
        warm handles (one bare, two with different child sets), all
        three are closed.  Operator intent: "this server changed,
        nothing about it is fresh anymore".

        Returns ``True`` if at least one entry was found and closed,
        ``False`` if the server wasn't cached.  Outstanding leases on
        evicted entries will find their ``release()`` is a no-op.
        """
        async with self._mutex:
            matching_keys = [key for key in self._entries if key[0] == server_id]
            removed = [self._entries.pop(key) for key in matching_keys]
        if not removed:
            return False
        for entry in removed:
            # Mark zeroed so a concurrent release on the same lease
            # can't later try to re-orphan it.
            entry._on_release = None
        await self._close_handles(removed)
        return True

    # ------------------------------------------------------------- inspect

    def inspect(self) -> list[CacheSnapshot]:
        """Snapshot of every live entry (current + orphans)."""
        out = [
            CacheSnapshot(
                server_id=e.server_id,
                child_resource_ids=list(e.child_resource_ids),
                version=e.version,
                ref_count=e.ref_count,
                last_used=e.last_used,
                is_orphan=False,
            )
            for e in self._entries.values()
        ]
        out.extend(
            CacheSnapshot(
                server_id=e.server_id,
                child_resource_ids=list(e.child_resource_ids),
                version=e.version,
                ref_count=e.ref_count,
                last_used=e.last_used,
                is_orphan=True,
            )
            for e in self._orphans
        )
        return out

    # ----------------------------------------------------------- internals

    async def _get_key_lock(self, key: _CacheKey) -> asyncio.Lock:
        async with self._mutex:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    def _install_entry(
        self,
        server_id: UUID,
        child_resource_ids: tuple[UUID, ...],
        version: int,
        tools: Closeable,
    ) -> _Entry:
        """Create and store a fresh entry as the current one for the key.

        Assumes the per-key lock is held (or no contention is possible,
        e.g. during ``stop``).
        """
        entry = _Entry(
            server_id=server_id,
            child_resource_ids=child_resource_ids,
            version=version,
            tools=tools,
            ref_count=0,
            last_used=self._clock(),
        )
        entry._on_release = self._on_release
        self._entries[(server_id, child_resource_ids)] = entry
        return entry

    def _orphan_locked(self, entry: _Entry) -> None:
        """Detach ``entry`` from ``_entries`` and stash it for deferred close.

        Caller must hold the per-key lock.  If the orphan's ref_count
        is already zero, schedule the close immediately.
        """
        entry.is_orphan = True
        # _on_release stays bound to ``self._on_release`` so the lease
        # callback continues to decrement this entry's ref_count.
        self._entries.pop((entry.server_id, entry.child_resource_ids), None)
        if entry.ref_count == 0:
            task = asyncio.create_task(
                self._close_handles([entry]),
                name=f"mcp-cache-close-orphan-{entry.server_id}",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:
            self._orphans.append(entry)

    async def _on_release(self, entry: _Entry) -> None:
        """Lease-release callback.

        Decrements the entry's ref-count, updates ``last_used``, and —
        if this was an orphan and the count just hit zero — schedules
        the deferred close.
        """
        async with self._mutex:
            if entry.ref_count > 0:
                entry.ref_count -= 1
                entry.last_used = self._clock()
            if entry.is_orphan and entry.ref_count == 0:
                try:
                    self._orphans.remove(entry)
                except ValueError:
                    # Already removed (concurrent stop?).
                    return
                to_close: list[_Entry] = [entry]
            else:
                to_close = []
        if to_close:
            await self._close_handles(to_close)

    async def _reaper_loop(self) -> None:
        """Background coroutine: every ``reap_interval``, close idle entries.

        Idle = ``ref_count == 0`` AND ``last_used`` older than
        ``idle_ttl``.  Errors during close are logged but never
        propagated — the reaper must keep running.
        """
        interval = self._reap_interval.total_seconds()
        try:
            while True:
                await asyncio.sleep(interval)
                await self._reap_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("mcp-cache reaper crashed; restarting on next start()")

    async def _reap_once(self) -> None:
        now = self._clock()
        async with self._mutex:
            doomed: list[_Entry] = []
            for key, entry in list(self._entries.items()):
                if entry.ref_count == 0 and (now - entry.last_used) > self._idle_ttl:
                    self._entries.pop(key, None)
                    doomed.append(entry)
        if doomed:
            await self._close_handles(doomed)

    async def _close_handles(self, entries: list[_Entry]) -> None:
        """Close a batch of detached entries, logging (never raising) on error."""
        for entry in entries:
            try:
                await entry.tools.close()
            except Exception:
                logger.exception(
                    "mcp-cache: error closing handle for server_id=%s version=%d",
                    entry.server_id,
                    entry.version,
                )
            entry._on_release = None  # break the lease-callback cycle


# ---------------------------------------------------------------------------
# Production wiring: DB row -> BuildPlan
# ---------------------------------------------------------------------------
#
# Everything above is a pure concurrency primitive with no DB or Agno
# knowledge.  The helper below glues it to the rest of the app:
#
# * :func:`make_row_fetcher` produces a :type:`RowFetcher` that loads
#   the ``MCPServer`` row, decrypts ``env_vars``, and packages a
#   :class:`BuildPlan` whose factory closure embeds the decrypted env.
# * The actual ``MCPTools`` construction is injected via the
#   ``tools_builder`` argument.  Production wires this to
#   :func:`gargantua.mcp_tools.build_mcp_tools`; tests pass in fakes.


@dataclass(frozen=True)
class ChildResourceData:
    """Decrypted child resource passed into the tools builder.

    A small immutable record so the builder doesn't need to touch
    SQLA rows (which would risk lazy-loading against a closed session)
    or the secrets module (which the cache layer doesn't depend on).
    """

    id: UUID
    parent_mcp_server_id: UUID
    type: str
    name: str
    url: str
    headers: dict[str, Any]
    enabled: bool


ToolsBuilder = Callable[
    [Any, dict[str, Any], Any | None, list[ChildResourceData]],
    Awaitable[Closeable],
]
"""Signature: ``(mcp_server_row, plaintext_env_vars, mcp_server_type_row,
child_resources) -> tools``.

``child_resources`` is the list of child resource records (already
decrypted) whose ``parent_mcp_server_id`` matches this server.  An
empty list means the agent didn't reference any children of this
server, and the builder should produce a "bare" tools handle.

The cache treats the returned object as a :class:`Closeable` — the only
contract it relies on.
"""

AsyncSessionFactory = Callable[[], Any]
"""Anything callable as ``factory()`` returning an async-context-manager
yielding an :class:`AsyncSession`.  Matches what
:func:`gargantua.db.session.get_session_factory` returns."""


def make_row_fetcher(
    session_factory: AsyncSessionFactory,
    tools_builder: ToolsBuilder,
) -> RowFetcher:
    """Build a :type:`RowFetcher` backed by the live database.

    Lifetime / threading notes
    --------------------------

    * The session is opened only long enough to read the row, decrypt
      its secrets, pull the type record, and load + decrypt any child
      resources the caller asked for.  Rows are then *detached* so the
      closure that builds the tools doesn't accidentally lazy-load
      anything against a closed session.
    * Archived servers are treated as "not found" — you can't spin up a
      retired server even if its row still exists.
    * Missing / disabled / wrong-parent child resources are also
      treated as "not found" (return ``None``).  Rationale: the cache
      key includes child_resource_ids; if any of them is invalid, the
      entry would be permanently bound to a broken set.  Surface
      ``ServerNotFound`` to the route layer and let it map to 503.
    * KEK-mismatch errors (secret encrypted under a different KEK)
      surface as ``None`` from the fetcher, which the cache reports as
      :class:`ServerNotFound`.  The operator gets a clear 5xx and an
      exception log; the right recovery is to finish ``rotate-kek``.
    """
    # Local imports defer the DB dependency so the cache primitive
    # above stays import-cheap for unit tests.
    from gargantua.db.models import (
        MCPServer,
        MCPServerChildResource,
        MCPServerType,
    )
    from gargantua.repo.mcp_child_resources import decrypt_headers
    from gargantua.repo.mcp_servers import decrypt_env_vars
    from gargantua.secrets import KekMismatch

    async def fetch(server_id: UUID, child_resource_ids: tuple[UUID, ...]) -> BuildPlan | None:
        async with session_factory() as session:
            row = await session.get(MCPServer, server_id)
            if row is None or row.archived_at is not None:
                return None
            try:
                plaintext = decrypt_env_vars(row)
            except KekMismatch:
                logger.exception(
                    "mcp-cache: cannot decrypt server %s under current KEK; "
                    "treating as missing until rotate-kek completes",
                    server_id,
                )
                return None
            type_row = await session.get(MCPServerType, row.type_id)
            version = row.version

            # Load + validate child resources.  Each must exist, be
            # enabled, and belong to *this* server.  Surface "not
            # found" if any check fails — the cache key encodes the
            # child set so a broken set means a broken entry.
            child_records: list[ChildResourceData] = []
            for child_id in child_resource_ids:
                child_row = await session.get(MCPServerChildResource, child_id)
                if (
                    child_row is None
                    or not child_row.enabled
                    or child_row.parent_mcp_server_id != server_id
                ):
                    logger.warning(
                        "mcp-cache: child %s missing/disabled/wrong-parent "
                        "for server %s; treating as ServerNotFound",
                        child_id,
                        server_id,
                    )
                    return None
                try:
                    child_headers = decrypt_headers(child_row)
                except KekMismatch:
                    logger.exception(
                        "mcp-cache: child %s headers can't be decrypted under the active KEK",
                        child_id,
                    )
                    return None
                child_records.append(
                    ChildResourceData(
                        id=child_row.id,
                        parent_mcp_server_id=child_row.parent_mcp_server_id,
                        type=child_row.type,
                        name=child_row.name,
                        url=child_row.url,
                        headers=child_headers,
                        enabled=child_row.enabled,
                    )
                )

            # Detach so the closure below can read attributes after the
            # session is closed.
            session.expunge(row)
            if type_row is not None:
                session.expunge(type_row)

        async def factory() -> Closeable:
            return await tools_builder(row, plaintext, type_row, child_records)

        return BuildPlan(version=version, factory=factory)

    return fetch
