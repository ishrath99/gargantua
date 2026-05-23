"""Unit tests for :mod:`gargantua.mcp_cache`.

The cache is the concurrency primitive that owns warm MCP tool handles
process-wide.  These tests run entirely against in-memory fakes — no
database, no subprocesses, no asyncio sleeps longer than a few ms —
so they pin down every interesting concurrency / lifecycle invariant
the registry and the runtime routes lean on.

Key design choice exercised here: ``acquire`` returns a :class:`Lease`,
not a bare handle.  ``Lease.release()`` is bound to the specific cache
entry the lease came from, so a release issued *after* a version bump
correctly decrements the **orphaned** old entry's ref-count — not the
freshly-built new entry.  A bare ``release(server_id)`` API would have
gotten this wrong silently, which is exactly the class of bug we don't
want in a long-running process that swaps MCP credentials live.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from gargantua.mcp_cache import (
    BuildPlan,
    MCPCache,
    ServerNotFound,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeCloseable:
    """Stand-in for an Agno :class:`MCPTools` handle.

    Records every ``close()`` call so the cache's lifecycle invariants
    can be asserted exactly (handles must be closed *once*, never zero
    or two times).
    """

    label: str
    close_calls: int = 0
    closed_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def close(self) -> None:
        self.close_calls += 1
        self.closed_event.set()


@dataclass
class FakeBackend:
    """Pretends to be the database side of the cache.

    Maps ``server_id -> (version, label)``.  Bump ``version`` to
    invalidate cached entries; remove the entry to simulate a deleted
    server (the fetcher then returns ``None`` and the cache must raise
    :class:`ServerNotFound`).
    """

    versions: dict[UUID, tuple[int, str]] = field(default_factory=dict)
    build_calls: list[tuple[UUID, int]] = field(default_factory=list)
    build_delay_s: float = 0.0

    def add(self, server_id: UUID, *, version: int = 1, label: str = "v1") -> None:
        self.versions[server_id] = (version, label)

    def bump(self, server_id: UUID, *, new_label: str) -> None:
        version, _ = self.versions[server_id]
        self.versions[server_id] = (version + 1, new_label)

    def remove(self, server_id: UUID) -> None:
        self.versions.pop(server_id, None)

    async def fetch(
        self,
        server_id: UUID,
        child_resource_ids: tuple[UUID, ...] = (),
    ) -> BuildPlan | None:
        if server_id not in self.versions:
            return None
        version, label = self.versions[server_id]

        async def factory() -> FakeCloseable:
            if self.build_delay_s:
                await asyncio.sleep(self.build_delay_s)
            self.build_calls.append((server_id, version))
            # Embed the child-resource set in the handle label so tests
            # that exercise the multi-child-set path can tell handles
            # apart visually.  Non-child-set tests still get the same
            # label they always did.
            label_with_children = (
                label
                if not child_resource_ids
                else f"{label}+children={sorted(child_resource_ids)}"
            )
            return FakeCloseable(label=label_with_children)

        return BuildPlan(version=version, factory=factory)


@dataclass
class FakeClock:
    """Monotonic UTC clock that only advances when tests say so.

    Lets us exercise idle-TTL behaviour deterministically without
    long ``asyncio.sleep`` calls in the test body.
    """

    now: datetime = field(default_factory=lambda: datetime(2024, 1, 1, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _make_cache(
    backend: FakeBackend,
    clock: FakeClock,
    *,
    idle_ttl: timedelta = timedelta(seconds=60),
    reap_interval: timedelta = timedelta(milliseconds=10),
) -> MCPCache:
    return MCPCache(
        row_fetcher=backend.fetch,
        idle_ttl=idle_ttl,
        reap_interval=reap_interval,
        clock=clock,
    )


@pytest.fixture
def cache(backend: FakeBackend, clock: FakeClock) -> MCPCache:
    return _make_cache(backend, clock)


# ---------------------------------------------------------------------------
# Acquire / release (via Lease)
# ---------------------------------------------------------------------------


async def test_acquire_builds_and_caches(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)

    lease = await cache.acquire(sid)
    assert isinstance(lease.tools, FakeCloseable)
    assert lease.tools.label == "v1"
    assert backend.build_calls == [(sid, 1)]
    await lease.release()


async def test_acquire_twice_returns_same_handle(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)

    a = await cache.acquire(sid)
    b = await cache.acquire(sid)
    assert a.tools is b.tools
    # Cache MUST NOT build twice when the version is unchanged.
    assert backend.build_calls == [(sid, 1)]
    await a.release()
    await b.release()


async def test_acquire_unknown_server_raises(cache: MCPCache) -> None:
    with pytest.raises(ServerNotFound):
        await cache.acquire(uuid4())


async def test_release_keeps_handle_warm(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)

    lease = await cache.acquire(sid)
    handle = lease.tools
    await lease.release()

    # The handle is still cached (ref_count == 0 but not yet reaped) and
    # the next acquire returns the same instance.
    lease2 = await cache.acquire(sid)
    assert lease2.tools is handle
    assert handle.close_calls == 0
    await lease2.release()


async def test_double_release_is_safe(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)

    lease = await cache.acquire(sid)
    await lease.release()
    await lease.release()  # idempotent; must not underflow ref_count

    snap = cache.inspect()
    assert len(snap) == 1
    assert snap[0].ref_count == 0


# ---------------------------------------------------------------------------
# Version invalidation
# ---------------------------------------------------------------------------


async def test_version_bump_triggers_rebuild_and_closes_old(
    cache: MCPCache, backend: FakeBackend
) -> None:
    sid = uuid4()
    backend.add(sid, version=1, label="v1")

    lease_old = await cache.acquire(sid)
    old_handle = lease_old.tools
    await lease_old.release()
    backend.bump(sid, new_label="v2")

    lease_new = await cache.acquire(sid)
    assert lease_new.tools is not old_handle
    assert lease_new.tools.label == "v2"
    assert backend.build_calls == [(sid, 1), (sid, 2)]

    # The old handle must be closed.  The cache schedules close in the
    # background since the orphan's ref_count was already zero — give
    # the task a tick to run.
    await asyncio.wait_for(old_handle.closed_event.wait(), timeout=1.0)
    assert old_handle.close_calls == 1
    await lease_new.release()


async def test_version_bump_with_active_lease_delays_close(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """An in-flight lease must NOT have its handle closed out from under
    it.  When a bump happens while A still holds the lease, the cache
    detaches the old entry as an *orphan* and only closes it when A's
    release drops its ref_count to zero."""
    sid = uuid4()
    backend.add(sid, version=1, label="v1")
    lease_a = await cache.acquire(sid)
    old_handle = lease_a.tools

    backend.bump(sid, new_label="v2")
    lease_b = await cache.acquire(sid)
    assert lease_b.tools is not old_handle

    # A still holds the old handle; close MUST NOT have fired yet.
    assert old_handle.close_calls == 0

    # A releases — orphan ref_count drops to zero and the deferred close
    # runs in the background.
    await lease_a.release()
    await asyncio.wait_for(old_handle.closed_event.wait(), timeout=1.0)
    assert old_handle.close_calls == 1

    # B is unaffected.
    assert lease_b.tools.close_calls == 0
    await lease_b.release()


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


async def test_evict_closes_and_returns_true(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)
    lease = await cache.acquire(sid)
    handle = lease.tools
    await lease.release()

    evicted = await cache.evict(sid)
    assert evicted is True
    assert handle.close_calls == 1

    assert cache.inspect() == []


async def test_evict_uncached_returns_false(cache: MCPCache) -> None:
    assert await cache.evict(uuid4()) is False


async def test_evict_while_held_still_closes(cache: MCPCache, backend: FakeBackend) -> None:
    """Evict is an admin override — the operator has decided this server
    is misbehaving and must be killed.  Close the handle even with
    outstanding leases.  Subsequent ``lease.release()`` calls become
    no-ops since the entry is already gone."""
    sid = uuid4()
    backend.add(sid)
    lease = await cache.acquire(sid)
    handle = lease.tools

    assert await cache.evict(sid) is True
    assert handle.close_calls == 1

    # release() on a lease whose entry was evicted must still be safe.
    await lease.release()

    # Next acquire rebuilds.
    lease2 = await cache.acquire(sid)
    assert lease2.tools is not handle
    await lease2.release()


async def test_acquire_after_evict_rebuilds_at_same_version(
    cache: MCPCache, backend: FakeBackend
) -> None:
    sid = uuid4()
    backend.add(sid, version=1, label="v1")
    lease = await cache.acquire(sid)
    await lease.release()
    await cache.evict(sid)

    lease2 = await cache.acquire(sid)
    assert lease2.tools.label == "v1"
    assert backend.build_calls == [(sid, 1), (sid, 1)]
    await lease2.release()


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------


async def test_reaper_closes_idle_entries(backend: FakeBackend, clock: FakeClock) -> None:
    cache = _make_cache(backend, clock, idle_ttl=timedelta(seconds=30))
    sid = uuid4()
    backend.add(sid)

    lease = await cache.acquire(sid)
    handle = lease.tools
    await lease.release()
    assert handle.close_calls == 0

    clock.advance(timedelta(seconds=31))
    await cache.start()
    try:
        await asyncio.wait_for(handle.closed_event.wait(), timeout=1.0)
    finally:
        await cache.stop()

    assert handle.close_calls == 1
    assert cache.inspect() == []


async def test_reaper_keeps_in_use_entries_alive(backend: FakeBackend, clock: FakeClock) -> None:
    cache = _make_cache(backend, clock, idle_ttl=timedelta(seconds=30))
    sid = uuid4()
    backend.add(sid)

    lease = await cache.acquire(sid)  # ref_count=1, NOT released
    handle = lease.tools
    clock.advance(timedelta(seconds=300))

    await cache.start()
    try:
        # Give the reaper several ticks to look at the entry.
        await asyncio.sleep(0.05)
        snap = cache.inspect()
        assert len(snap) == 1
        assert snap[0].ref_count == 1
        assert handle.close_calls == 0
    finally:
        await cache.stop()
    # stop() drains everything, including the still-held entry.
    assert handle.close_calls == 1
    await lease.release()  # no-op now; entry is gone


async def test_reaper_keeps_recently_used_entries(backend: FakeBackend, clock: FakeClock) -> None:
    cache = _make_cache(backend, clock, idle_ttl=timedelta(seconds=30))
    sid = uuid4()
    backend.add(sid)

    lease = await cache.acquire(sid)
    handle = lease.tools
    await lease.release()

    # Advance, but not past the TTL.
    clock.advance(timedelta(seconds=15))

    await cache.start()
    try:
        await asyncio.sleep(0.05)
        # Entry should still be there.
        snap = cache.inspect()
        assert len(snap) == 1
        assert snap[0].server_id == sid
        assert handle.close_calls == 0
    finally:
        await cache.stop()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_acquire_same_key_builds_once(
    backend: FakeBackend, clock: FakeClock
) -> None:
    """Two acquires racing on the same server_id must dedupe to one
    build (the per-key lock is the whole point of the cache)."""
    backend.build_delay_s = 0.02  # widen the race window
    cache = _make_cache(backend, clock)
    sid = uuid4()
    backend.add(sid)

    leases = await asyncio.gather(
        cache.acquire(sid),
        cache.acquire(sid),
        cache.acquire(sid),
    )
    assert leases[0].tools is leases[1].tools is leases[2].tools
    assert len(backend.build_calls) == 1

    snap = cache.inspect()
    assert snap[0].ref_count == 3

    for lease in leases:
        await lease.release()


async def test_concurrent_acquire_different_keys_runs_in_parallel(
    backend: FakeBackend, clock: FakeClock
) -> None:
    """Per-key locks must not serialize across keys; two slow builds for
    distinct servers should complete in roughly ``build_delay_s`` total,
    not ``2 * build_delay_s``."""
    backend.build_delay_s = 0.05
    cache = _make_cache(backend, clock)
    a, b = uuid4(), uuid4()
    backend.add(a)
    backend.add(b)

    loop = asyncio.get_running_loop()
    start = loop.time()
    lease_a, lease_b = await asyncio.gather(cache.acquire(a), cache.acquire(b))
    elapsed = loop.time() - start
    # Allow generous slack for CI; the point is to fail loudly if locks
    # got serialized (which would push elapsed >= 0.10).
    assert elapsed < 0.09, f"acquires serialized: elapsed={elapsed:.3f}s"
    await lease_a.release()
    await lease_b.release()


# ---------------------------------------------------------------------------
# Shutdown / drain
# ---------------------------------------------------------------------------


async def test_stop_closes_all_entries(backend: FakeBackend, clock: FakeClock) -> None:
    cache = _make_cache(backend, clock)
    sid1, sid2 = uuid4(), uuid4()
    backend.add(sid1)
    backend.add(sid2)

    lease1 = await cache.acquire(sid1)
    lease2 = await cache.acquire(sid2)
    h1, h2 = lease1.tools, lease2.tools

    await cache.start()
    await cache.stop()

    assert h1.close_calls == 1
    assert h2.close_calls == 1
    assert cache.inspect() == []
    # Releases after stop are no-ops.
    await lease1.release()
    await lease2.release()


async def test_stop_is_idempotent(backend: FakeBackend, clock: FakeClock) -> None:
    cache = _make_cache(backend, clock)
    await cache.start()
    await cache.stop()
    await cache.stop()  # must not raise


async def test_acquire_after_stop_raises(backend: FakeBackend, clock: FakeClock) -> None:
    cache = _make_cache(backend, clock)
    sid = uuid4()
    backend.add(sid)

    await cache.start()
    await cache.stop()

    with pytest.raises(RuntimeError):
        await cache.acquire(sid)


# ---------------------------------------------------------------------------
# Lease helper (context manager)
# ---------------------------------------------------------------------------


async def test_lease_context_manager(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)

    async with cache.lease(sid) as tools:
        assert isinstance(tools, FakeCloseable)
        snap = cache.inspect()
        assert snap[0].ref_count == 1

    snap = cache.inspect()
    assert snap[0].ref_count == 0


async def test_lease_releases_on_exception(cache: MCPCache, backend: FakeBackend) -> None:
    sid = uuid4()
    backend.add(sid)

    with pytest.raises(RuntimeError, match="boom"):
        async with cache.lease(sid):
            raise RuntimeError("boom")

    snap = cache.inspect()
    assert snap[0].ref_count == 0


# ---------------------------------------------------------------------------
# Inspect snapshot
# ---------------------------------------------------------------------------


async def test_inspect_returns_snapshot_not_live_state(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """Mutating the returned list must not affect the cache, and a later
    inspect must reflect post-mutation state."""
    sid = uuid4()
    backend.add(sid)
    lease = await cache.acquire(sid)

    snap1 = cache.inspect()
    snap1.clear()  # caller fiddles with the list — cache must be untouched

    snap2 = cache.inspect()
    assert len(snap2) == 1
    assert snap2[0].server_id == sid
    assert snap2[0].ref_count == 1

    await lease.release()


async def test_inspect_includes_orphans(cache: MCPCache, backend: FakeBackend) -> None:
    """Orphaned (post-bump) entries with outstanding leases should be
    visible to the operator so a stuck lease can be debugged."""
    sid = uuid4()
    backend.add(sid)
    lease_a = await cache.acquire(sid)
    backend.bump(sid, new_label="v2")
    lease_b = await cache.acquire(sid)

    snap = cache.inspect()
    # Two entries for the same sid: one current (v2) and one orphan (v1).
    by_version = {e.version: e for e in snap if e.server_id == sid}
    assert by_version[1].is_orphan is True
    assert by_version[1].ref_count == 1
    assert by_version[2].is_orphan is False
    assert by_version[2].ref_count == 1

    await lease_a.release()
    await lease_b.release()


# ---------------------------------------------------------------------------
# child_resource_ids — composite cache key
# ---------------------------------------------------------------------------
#
# Background: an agent's child_resource_ids select which sub-resources
# (e.g. swagger docs) of a multi-tool MCP server it sees.  Two agents
# with the same parent server_id but different child sets MUST get
# different warm handles, otherwise the second agent silently runs
# against the first agent's tool surface.  We encode this by extending
# the cache key from ``server_id`` to ``(server_id, sorted(child_ids))``.


async def test_acquire_with_different_child_sets_builds_separate_entries(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """Same server_id, different child_resource_ids -> distinct
    handles, distinct ref-counts, distinct entries in inspect()."""
    sid = uuid4()
    child_a, child_b = uuid4(), uuid4()
    backend.add(sid)

    lease_a = await cache.acquire(sid, child_resource_ids=(child_a,))
    lease_b = await cache.acquire(sid, child_resource_ids=(child_b,))

    assert lease_a.tools is not lease_b.tools
    # Two builds: one per (sid, child_set) pair.
    assert len(backend.build_calls) == 2

    snap = cache.inspect()
    # Compare as a set of (canonical) tuples so the assertion doesn't
    # depend on iteration order or UUID sort order.
    children_seen = {tuple(sorted(e.child_resource_ids)) for e in snap if e.server_id == sid}
    assert children_seen == {(child_a,), (child_b,)}

    await lease_a.release()
    await lease_b.release()


async def test_acquire_with_same_child_set_dedupes_within_lock(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """Same (server_id, child_resource_ids) -> same warm handle, no
    second build.  This is the whole reason we use a composite key
    instead of always rebuilding."""
    sid = uuid4()
    child = uuid4()
    backend.add(sid)

    a = await cache.acquire(sid, child_resource_ids=(child,))
    b = await cache.acquire(sid, child_resource_ids=(child,))

    assert a.tools is b.tools
    assert len(backend.build_calls) == 1
    await a.release()
    await b.release()


async def test_child_resource_id_order_is_normalized(cache: MCPCache, backend: FakeBackend) -> None:
    """``(c1, c2)`` and ``(c2, c1)`` are the *same* child set — the
    cache must hash to the same key regardless of the caller's order."""
    sid = uuid4()
    c1, c2 = uuid4(), uuid4()
    backend.add(sid)

    a = await cache.acquire(sid, child_resource_ids=(c1, c2))
    b = await cache.acquire(sid, child_resource_ids=(c2, c1))

    assert a.tools is b.tools
    assert len(backend.build_calls) == 1
    await a.release()
    await b.release()


async def test_acquire_with_no_child_set_is_distinct_from_with_children(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """An agent that doesn't reference any child resources gets a
    *different* cache entry than one that references a specific child
    set.  Same parent server, but the tool surfaces differ."""
    sid = uuid4()
    child = uuid4()
    backend.add(sid)

    bare = await cache.acquire(sid)  # no child resources
    filtered = await cache.acquire(sid, child_resource_ids=(child,))

    assert bare.tools is not filtered.tools
    assert len(backend.build_calls) == 2

    await bare.release()
    await filtered.release()


async def test_inspect_surfaces_child_resource_ids(cache: MCPCache, backend: FakeBackend) -> None:
    """The admin /admin/mcp-cache snapshot has to be able to tell two
    entries for the same server apart by their child resource set."""
    sid = uuid4()
    c1 = uuid4()
    backend.add(sid)

    lease_bare = await cache.acquire(sid)
    lease_filtered = await cache.acquire(sid, child_resource_ids=(c1,))

    snap = cache.inspect()
    # Two entries for the same server_id, distinguished by
    # child_resource_ids.
    for_sid = [e for e in snap if e.server_id == sid]
    assert len(for_sid) == 2
    by_children = {tuple(sorted(e.child_resource_ids)): e for e in for_sid}
    assert () in by_children
    assert (c1,) in by_children
    # ref_counts are independent.
    assert by_children[()].ref_count == 1
    assert by_children[(c1,)].ref_count == 1

    await lease_bare.release()
    await lease_filtered.release()


async def test_evict_clears_all_child_variants_for_server(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """``evict(server_id)`` is "kill every warm handle for this
    server" — operator's intent when a server's config changes is
    that no stale entries for any child set remain."""
    sid = uuid4()
    c1, c2 = uuid4(), uuid4()
    backend.add(sid)

    lease_a = await cache.acquire(sid)
    lease_b = await cache.acquire(sid, child_resource_ids=(c1,))
    lease_c = await cache.acquire(sid, child_resource_ids=(c2,))
    handles = [lease_a.tools, lease_b.tools, lease_c.tools]

    evicted = await cache.evict(sid)
    assert evicted is True  # at least one entry was found

    # Every handle is closed, every entry is gone.
    for h in handles:
        assert h.close_calls == 1
    assert [e for e in cache.inspect() if e.server_id == sid] == []

    # Subsequent releases are no-ops (matches the existing single-key
    # evict-while-held contract).
    await lease_a.release()
    await lease_b.release()
    await lease_c.release()


async def test_version_bump_invalidates_entry_within_its_child_set(
    cache: MCPCache, backend: FakeBackend
) -> None:
    """A version bump on the server row should rebuild the entry for
    each affected child set independently — but NOT collapse them
    together (different child sets stay distinct after the bump)."""
    sid = uuid4()
    c1 = uuid4()
    backend.add(sid, version=1, label="v1")

    bare1 = await cache.acquire(sid)
    filtered1 = await cache.acquire(sid, child_resource_ids=(c1,))
    handle_bare_v1 = bare1.tools
    handle_filtered_v1 = filtered1.tools
    await bare1.release()
    await filtered1.release()

    backend.bump(sid, new_label="v2")

    bare2 = await cache.acquire(sid)
    filtered2 = await cache.acquire(sid, child_resource_ids=(c1,))

    # Both rebuilt — handles are different from v1.
    assert bare2.tools is not handle_bare_v1
    assert filtered2.tools is not handle_filtered_v1
    # And still distinct from each other after the bump.
    assert bare2.tools is not filtered2.tools

    await bare2.release()
    await filtered2.release()
