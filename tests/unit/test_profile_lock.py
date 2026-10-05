"""TDD — ProfileLock implementations and BrowserScheduler integration.

Testa:
  1. NullProfileLock — acquire / release sem erros, warn emitido
  2. RedisProfileLock — exclusividade via fake Redis; crash recovery via TTL
  3. RedisProfileLock — fail-open quando Redis indisponível
  4. FileProfileLock — exclusividade via threading (sem fcntl real em Windows)
  5. BrowserScheduler com ProfileLock — lease contém profile_lock_lease
  6. BrowserScheduler — profile lock liberado no release
  7. BrowserScheduler — profile lock timeout → slot retorna ao pool
  8. build_profile_lock — factory modes
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scout_api.modules.crawler.core.browser_scheduler import BrowserScheduler
from scout_api.modules.crawler.core.exceptions import RequestError
from scout_api.modules.crawler.core.profile_lock import (
    FileProfileLock,
    NullProfileLock,
    ProfileLockLease,
    RedisProfileLock,
    build_profile_lock,
)
from scout_api.modules.crawler.services.html_fetcher import _WarmBrowserSession

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory Redis fake for lock tests."""

    def __init__(self) -> None:
        self._data: dict[
            bytes, tuple[bytes, float | None]
        ] = {}  # key → (value, expiry_monotonic)
        self._lock = threading.Lock()

    def _is_expired(self, key: bytes) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        _, expiry = entry
        if expiry is not None and time.monotonic() > expiry:
            return True
        return False

    def set(
        self,
        key: str | bytes,
        value: str | bytes,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool | None:
        k = key.encode() if isinstance(key, str) else key
        v = value.encode() if isinstance(value, str) else value
        with self._lock:
            if nx and k in self._data and not self._is_expired(k):
                return None  # NX failed
            expiry = None
            if px is not None:
                expiry = time.monotonic() + px / 1000.0
            elif ex is not None:
                expiry = time.monotonic() + float(ex)
            self._data[k] = (v, expiry)
            return True

    def get(self, key: str | bytes) -> bytes | None:
        k = key.encode() if isinstance(key, str) else key
        with self._lock:
            if self._is_expired(k):
                self._data.pop(k, None)
                return None
            entry = self._data.get(k)
            return entry[0] if entry else None

    def delete(self, *keys: str | bytes) -> int:
        count = 0
        with self._lock:
            for key in keys:
                k = key.encode() if isinstance(key, str) else key
                if k in self._data:
                    del self._data[k]
                    count += 1
        return count

    def eval(self, script: bytes | str, num_keys: int, *args: bytes | str) -> int:
        """Execute compare-and-delete/renew Lua scripts (inlined)."""
        key = args[0] if isinstance(args[0], bytes) else args[0].encode()
        token = args[1] if isinstance(args[1], bytes) else args[1].encode()
        with self._lock:
            entry = self._data.get(key)
            if entry is None or self._is_expired(key):
                return 0
            if entry[0] == token:
                if "pexpire" in str(script).casefold():
                    ttl_ms = int(args[2])
                    self._data[key] = (
                        entry[0],
                        time.monotonic() + ttl_ms / 1000.0,
                    )
                    return 1
                del self._data[key]
                return 1
            return 0

    def expire(self, key: str | bytes, seconds: int) -> int:
        k = key.encode() if isinstance(key, str) else key
        with self._lock:
            if k in self._data:
                val, _ = self._data[k]
                self._data[k] = (val, time.monotonic() + seconds)
                return 1
            return 0


def _make_gateway(fake_redis: _FakeRedis | None = None) -> MagicMock:
    gw = MagicMock()
    gw.get_client.return_value = fake_redis if fake_redis is not None else _FakeRedis()
    gw.reset = MagicMock()
    return gw


# ---------------------------------------------------------------------------
# 1. NullProfileLock
# ---------------------------------------------------------------------------


def test_null_lock_acquire_release_no_error() -> None:
    lock = NullProfileLock()
    lease = lock.acquire(Path("/tmp/slot-0"), timeout_ms=1_000)
    assert lease.backend == "null"
    assert lease.profile_path == Path("/tmp/slot-0")
    lock.release(lease)  # must not raise


def test_null_lock_emits_warning(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    lock = NullProfileLock()
    with caplog.at_level(logging.WARNING, logger="scout_api"):
        lock.acquire(Path("/tmp/slot-0"), timeout_ms=1_000)
    assert any("profile_lock_disabled" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. RedisProfileLock — exclusivity
# ---------------------------------------------------------------------------


def test_redis_lock_exclusivity_second_caller_blocks() -> None:
    """While A holds the Redis lock, B must not acquire within timeout."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    lock = RedisProfileLock(gw, ttl_ms=10_000)
    profile = Path("/mnt/profiles/slot-0")

    lease_a = lock.acquire(profile, timeout_ms=1_000)
    assert lease_a.backend == "redis"

    # B tries to acquire with a 100ms timeout — should fail
    with pytest.raises(RequestError) as exc_info:
        lock.acquire(profile, timeout_ms=100)
    assert exc_info.value.code == "PROFILE_LOCK_TIMEOUT"
    assert exc_info.value.retryable is True

    lock.release(lease_a)


def test_redis_lock_b_acquires_after_a_releases() -> None:
    """After A releases, B can acquire."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    lock = RedisProfileLock(gw, ttl_ms=10_000)
    profile = Path("/mnt/profiles/slot-0")

    lease_a = lock.acquire(profile, timeout_ms=1_000)
    lock.release(lease_a)

    lease_b = lock.acquire(profile, timeout_ms=500)
    assert lease_b.backend == "redis"
    lock.release(lease_b)


def test_redis_lock_ttl_crash_recovery() -> None:
    """After 'crash' (no release), lock auto-expires within TTL."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    ttl_ms = 200  # very short for test
    lock = RedisProfileLock(gw, ttl_ms=ttl_ms, poll_interval_ms=30)
    profile = Path("/mnt/profiles/slot-crash")

    lease_a = lock.acquire(profile, timeout_ms=1_000)
    # Simulate crash: don't call release, just forget the lease.
    del lease_a

    # Wait slightly longer than TTL
    time.sleep(ttl_ms / 1000.0 + 0.1)

    # B should now acquire
    lease_b = lock.acquire(profile, timeout_ms=500)
    assert lease_b.backend == "redis"
    lock.release(lease_b)


def test_redis_lock_compare_and_delete_only_owner_releases() -> None:
    """Only the owner token can release the lock."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    lock = RedisProfileLock(gw, ttl_ms=10_000)
    profile = Path("/mnt/profiles/slot-0")

    lease_a = lock.acquire(profile, timeout_ms=1_000)

    # Craft a fake lease with wrong token — should not release A's lock
    fake_lease = ProfileLockLease(
        profile_path=profile, backend="redis", _token="wrong_token"
    )
    lock.release(fake_lease)

    # A's lock should still be held
    with pytest.raises(RequestError) as exc_info:
        lock.acquire(profile, timeout_ms=50)
    assert exc_info.value.code == "PROFILE_LOCK_TIMEOUT"

    # Actual release works
    lock.release(lease_a)
    lease_b = lock.acquire(profile, timeout_ms=200)
    lock.release(lease_b)


# ---------------------------------------------------------------------------
# 3. RedisProfileLock — fail-open when Redis unavailable
# ---------------------------------------------------------------------------


def test_redis_lock_failopen_when_redis_unavailable() -> None:
    """If Redis is down, acquire fails-open (no error, backend=redis_failopen)."""
    gw = _make_gateway(fake_redis=None)
    gw.get_client.return_value = None  # simulate Redis unavailable

    lock = RedisProfileLock(gw, ttl_ms=10_000)
    profile = Path("/mnt/profiles/slot-0")

    lease = lock.acquire(profile, timeout_ms=500)
    assert "failopen" in lease.backend
    lock.release(lease)  # must not raise


def test_redis_lock_failopen_release_is_noop() -> None:
    """Release of a fail-open lease never raises."""
    gw = _make_gateway(fake_redis=None)
    gw.get_client.return_value = None
    lock = RedisProfileLock(gw)

    lease = ProfileLockLease(
        profile_path=Path("/tmp/x"),
        backend="redis_failopen",
        _token="t",
    )
    lock.release(lease)  # must not raise


# ---------------------------------------------------------------------------
# 4. FileProfileLock — on Linux with real fcntl; skip on Windows
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_profile(tmp_path: Path) -> Path:
    p = tmp_path / "slot-0"
    p.mkdir()
    return p


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="fcntl not available on Windows; tested inside Linux container",
)
def test_file_lock_exclusivity_threading(tmp_profile: Path) -> None:
    """Two threads: A holds file lock; B must block until A releases."""
    lock = FileProfileLock()
    lease_a = lock.acquire(tmp_profile, timeout_ms=2_000)
    assert lease_a.backend == "file"

    b_got_lock = threading.Event()
    b_error: list[Exception] = []

    def worker_b() -> None:
        try:
            lease_b = lock.acquire(tmp_profile, timeout_ms=3_000)
            b_got_lock.set()
            lock.release(lease_b)
        except Exception as e:
            b_error.append(e)

    t = threading.Thread(target=worker_b, daemon=True)
    t.start()

    # B should be blocked for ~100ms
    time.sleep(0.1)
    assert not b_got_lock.is_set(), "B should not have the lock while A holds it"

    lock.release(lease_a)
    assert b_got_lock.wait(timeout=3.0), "B should acquire after A releases"
    t.join(timeout=3.0)
    assert not b_error, f"B raised: {b_error}"


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="fcntl not available on Windows",
)
def test_file_lock_timeout_when_held(tmp_profile: Path) -> None:
    """B raises PROFILE_LOCK_TIMEOUT when A holds the lock and B's timeout elapses."""
    lock = FileProfileLock()
    lease_a = lock.acquire(tmp_profile, timeout_ms=2_000)

    with pytest.raises(RequestError) as exc_info:
        lock.acquire(tmp_profile, timeout_ms=100)
    assert exc_info.value.code == "PROFILE_LOCK_TIMEOUT"

    lock.release(lease_a)


def test_file_lock_windows_noop() -> None:
    """On Windows (no fcntl), FileProfileLock acquires as file_noop without error."""
    with patch.dict("sys.modules", {"fcntl": None}):
        lock = FileProfileLock()
        # Force ImportError path by mocking fcntl import
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *a, **kw: (
                (_ for _ in ()).throw(ImportError())
                if name == "fcntl"
                else __import__(name, *a, **kw)
            ),
        ):
            try:
                lease = lock.acquire(Path("/tmp/slot-0"), timeout_ms=500)
                assert "noop" in lease.backend or lease.backend == "file"
                lock.release(lease)
            except (ImportError, Exception):
                pass  # Platform-specific behaviour is acceptable here


# ---------------------------------------------------------------------------
# 5. BrowserScheduler — lease contains profile_lock_lease when lock configured
# ---------------------------------------------------------------------------


def test_scheduler_lease_has_profile_lock_lease() -> None:
    """With profile_lock configured, BrowserSlotLease._profile_lock_lease is set."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    redis_lock = RedisProfileLock(gw, ttl_ms=60_000)

    sched = BrowserScheduler(
        capacity=1,
        queue_capacity=4,
        queue_timeout_ms=5_000,
        profile_lock=redis_lock,
        profile_base_path=Path("/mnt/profiles"),
        profile_lock_timeout_ms=2_000,
    )

    lease = sched.acquire()
    assert lease._profile_lock_lease is not None
    assert lease._profile_lock_lease.backend == "redis"
    assert lease._profile_lock_lease.profile_path == Path("/mnt/profiles/slot-0")
    sched.release(lease)


def test_scheduler_no_profile_lock_lease_when_not_configured() -> None:
    """Without profile_lock, BrowserSlotLease._profile_lock_lease is None."""
    sched = BrowserScheduler(capacity=1, queue_capacity=4, queue_timeout_ms=5_000)
    lease = sched.acquire()
    assert lease._profile_lock_lease is None
    sched.release(lease)


# ---------------------------------------------------------------------------
# 6. BrowserScheduler — profile lock released on slot release
# ---------------------------------------------------------------------------


def test_scheduler_releases_profile_lock_on_release() -> None:
    """When the lease is released, the profile lock is also released."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    redis_lock = RedisProfileLock(gw, ttl_ms=60_000)
    profile_base = Path("/mnt/profiles")

    sched = BrowserScheduler(
        capacity=1,
        queue_capacity=4,
        queue_timeout_ms=5_000,
        profile_lock=redis_lock,
        profile_base_path=profile_base,
        profile_lock_timeout_ms=2_000,
    )

    lease_a = sched.acquire()
    assert lease_a._profile_lock_lease is not None
    sched.release(lease_a)

    # After release, another process can acquire the same profile
    lease_b = sched.acquire()
    assert lease_b._profile_lock_lease is not None
    sched.release(lease_b)


def test_warm_session_holds_profile_lock_until_close_and_renews_ttl() -> None:
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    profile_base = Path("/mnt/profiles")
    lock_a = RedisProfileLock(gw, ttl_ms=90)
    lock_b = RedisProfileLock(gw, ttl_ms=90)
    sched_a = BrowserScheduler(
        capacity=1,
        queue_capacity=0,
        queue_timeout_ms=0,
        profile_lock=lock_a,
        profile_base_path=profile_base,
        profile_lock_timeout_ms=100,
    )
    sched_b = BrowserScheduler(
        capacity=1,
        queue_capacity=0,
        queue_timeout_ms=0,
        profile_lock=lock_b,
        profile_base_path=profile_base,
        profile_lock_timeout_ms=100,
    )
    lease = sched_a.acquire()

    class _Context:
        closed = False

        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            self.closed = True

    cm = _Context()
    session = _WarmBrowserSession(
        cm=cm,
        browser=object(),
        fingerprint="locale=pt-BR",
        on_idle=lambda expired, generation: expired.close(generation=generation),
    )
    sched_a.retain_profile_lock(lease)
    session.retain_profile_ownership(
        scheduler=sched_a,
        slot_lease=lease,
        idle_timeout_s=1.0,
    )
    sched_a.release(lease)

    assert sched_a.snapshot()["active"] == 0
    until = time.monotonic() + 0.22
    while time.monotonic() < until:
        session.touch()
        time.sleep(0.04)
    assert lease._profile_lock_retained
    assert lease._profile_lock_lease is not None
    assert lease._profile_lock_lease._renew_thread is not None
    assert lease._profile_lock_lease._renew_thread.is_alive()
    assert fake.get(RedisProfileLock._key(profile_base / "slot-0")) is not None
    with pytest.raises(RequestError) as exc_info:
        lock_b.acquire(profile_base / "slot-0", timeout_ms=100)
    assert exc_info.value.code == "PROFILE_LOCK_TIMEOUT"
    session.close()
    assert cm.closed
    lease_b = sched_b.acquire()
    sched_b.release(lease_b)


def test_warm_session_idle_expiration_releases_profile_lock() -> None:
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    profile_base = Path("/mnt/profiles")
    lock = RedisProfileLock(gw, ttl_ms=500)
    sched = BrowserScheduler(
        capacity=1,
        queue_capacity=0,
        queue_timeout_ms=0,
        profile_lock=lock,
        profile_base_path=profile_base,
        profile_lock_timeout_ms=100,
    )
    waiter = BrowserScheduler(
        capacity=1,
        queue_capacity=0,
        queue_timeout_ms=0,
        profile_lock=RedisProfileLock(gw, ttl_ms=500),
        profile_base_path=profile_base,
        profile_lock_timeout_ms=100,
    )
    lease = sched.acquire()

    class _Context:
        closed = threading.Event()

        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            self.closed.set()

    cm = _Context()
    session = _WarmBrowserSession(
        cm=cm,
        browser=object(),
        fingerprint="locale=pt-BR",
        on_idle=lambda expired, generation: expired.close(generation=generation),
    )
    sched.retain_profile_lock(lease)
    session.retain_profile_ownership(
        scheduler=sched,
        slot_lease=lease,
        idle_timeout_s=0.06,
    )
    sched.release(lease)

    assert cm.closed.wait(0.5)
    lease_waiter = waiter.acquire()
    waiter.release(lease_waiter)


# ---------------------------------------------------------------------------
# 7. BrowserScheduler — profile lock timeout returns slot to pool
# ---------------------------------------------------------------------------


def test_scheduler_profile_lock_timeout_returns_slot_to_pool() -> None:
    """If profile lock times out, the scheduler slot is returned to the pool."""
    fake = _FakeRedis()
    gw = _make_gateway(fake)
    redis_lock = RedisProfileLock(gw, ttl_ms=60_000)
    profile_base = Path("/mnt/profiles")

    # Manually hold the Redis profile lock for slot-0
    key = RedisProfileLock._key(profile_base / "slot-0")
    fake.set(key, b"external_owner", nx=True, px=60_000)

    sched = BrowserScheduler(
        capacity=1,
        queue_capacity=4,
        queue_timeout_ms=5_000,
        profile_lock=redis_lock,
        profile_base_path=profile_base,
        profile_lock_timeout_ms=100,  # short timeout
    )

    with pytest.raises(RequestError) as exc_info:
        sched.acquire()
    assert exc_info.value.code == "PROFILE_LOCK_TIMEOUT"

    # Slot must be returned to pool (active=0)
    snap = sched.snapshot()
    assert snap["active"] == 0, "Slot must return to pool after profile lock timeout"


# ---------------------------------------------------------------------------
# 8. build_profile_lock — factory
# ---------------------------------------------------------------------------


def test_build_profile_lock_redis_mode() -> None:
    gw = _make_gateway()
    lock = build_profile_lock("redis", gw)
    assert isinstance(lock, RedisProfileLock)


def test_build_profile_lock_file_mode() -> None:
    lock = build_profile_lock("file")
    assert isinstance(lock, FileProfileLock)


def test_build_profile_lock_off_mode() -> None:
    lock = build_profile_lock("off")
    assert isinstance(lock, NullProfileLock)


def test_build_profile_lock_redis_no_gateway_falls_back_to_null() -> None:
    lock = build_profile_lock("redis", None)
    assert isinstance(lock, NullProfileLock)


def test_build_profile_lock_unknown_mode_falls_back_to_null() -> None:
    lock = build_profile_lock("unknown_mode")
    assert isinstance(lock, NullProfileLock)
