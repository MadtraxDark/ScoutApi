"""Cross-process Camoufox profile ownership lock.

Prevents two processes (api, monitor, match-runner) from simultaneously opening
the same Firefox profile directory — which causes Playwright launch failures and
profile corruption.

Design (Phase 4):
- Primary: Redis SET NX PX + compare-and-delete (proven cross-process, TTL avoids
  orphans after crash). Same pattern as DistributedSingleFlight.
- Secondary: fcntl.flock on Linux (proven on bind-mount when tested; best-effort
  because Docker Desktop Windows VirtioFS may not propagate advisory locks
  across containers).
- Off: NullProfileLock — emergency bypass only; logs WARN every acquisition.

CAMOUFOX_PROFILE_LOCK setting values:
    "redis"  — Redis primary (default when Redis available).
    "file"   — fcntl.flock (use only after bind-mount proof on real env).
    "off"    — no lock, WARN emitted (dev/emergency only).

Protocol:
    acquire(profile_path, *, timeout_ms) -> ProfileLockLease
    release(lease) -> None

Callers (BrowserScheduler) acquire per-slot profile path before warming session
and release on slot release, regardless of success/failure.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

from scout_api.modules.crawler.core.exceptions import RequestError

if TYPE_CHECKING:
    from scout_api.modules.crawler.core.redis_client import RedisGateway

logger = logging.getLogger(__name__)

# Redis key namespace for profile locks.
_KEY_PREFIX = "scout:v1:profile_lock"

# Default TTL for Redis-backed lease.
# Long enough to cover the full browser session lifecycle (launch + N fetches).
# The holder is expected to release explicitly; TTL is the crash-safety backstop.
_DEFAULT_REDIS_TTL_MS = 10 * 60 * 1000  # 10 minutes

# Compare-and-delete: only the exact owner token may delete the key.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""

# Poll interval when waiting for a locked profile to become available.
_POLL_INTERVAL_S = 0.05  # 50 ms


# ---------------------------------------------------------------------------
# Lease
# ---------------------------------------------------------------------------


@dataclass
class ProfileLockLease:
    """Opaque lease returned by ProfileLock.acquire().

    Callers treat this as an opaque handle and pass it unchanged to release().
    The backend-specific fields (_token, _fd_handle) are internal.
    """

    profile_path: Path
    backend: str  # "redis" | "file" | "null" | "redis_failopen" | "file_noop"
    _token: str = field(default="", repr=False)
    _ttl_ms: int | None = field(default=None, repr=False)
    _renew_stop: threading.Event | None = field(default=None, repr=False)
    _renew_thread: threading.Thread | None = field(default=None, repr=False)
    _lost_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # File handle kept open for the duration of a file lock.
    _file_handle: IO[str] | None = field(default=None, repr=False)

    @property
    def lost(self) -> bool:
        """Whether a renewable backend lease is known to have been lost."""
        return self._lost_event.is_set()


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ProfileLock(Protocol):
    """Cross-process profile ownership lock."""

    def acquire(self, profile_path: Path, *, timeout_ms: int) -> ProfileLockLease:
        """Acquire exclusive ownership of ``profile_path``.

        Blocks until the profile is available, or raises RequestError if
        ``timeout_ms`` elapses.

        Args:
            profile_path: Absolute path to the Firefox profile directory.
            timeout_ms:   Maximum wait time in milliseconds.

        Returns:
            ProfileLockLease — must be passed to release() exactly once.

        Raises:
            RequestError(code="PROFILE_LOCK_TIMEOUT", retryable=True):
                Another owner is holding the lock and ``timeout_ms`` elapsed.
        """
        ...

    def release(self, lease: ProfileLockLease) -> None:
        """Release ownership. Idempotent; never raises."""
        ...

    def retain(self, lease: ProfileLockLease) -> bool:
        """Keep ownership beyond one fetch; return whether the backend supports it."""
        ...


# ---------------------------------------------------------------------------
# Null implementation (off mode / emergency)
# ---------------------------------------------------------------------------


class NullProfileLock:
    """No-op profile lock. Logs WARN on every acquire — dev/emergency only."""

    def acquire(self, profile_path: Path, *, timeout_ms: int) -> ProfileLockLease:
        logger.warning(
            "profile_lock_disabled",
            extra={
                "profile_path": str(profile_path),
                "mode": "off",
            },
        )
        return ProfileLockLease(
            profile_path=profile_path,
            backend="null",
            _token="null",
        )

    def release(self, lease: ProfileLockLease) -> None:
        pass  # no-op

    def retain(self, lease: ProfileLockLease) -> bool:
        del lease
        return False


# ---------------------------------------------------------------------------
# Redis implementation (primary, recommended)
# ---------------------------------------------------------------------------


class RedisProfileLock:
    """Cross-process profile ownership via Redis SET NX PX + compare-and-delete.

    Same pattern as DistributedSingleFlight (already used in this project).
    TTL of ~10 minutes prevents orphaned locks after process crash/kill.

    Fail-open: if Redis is unavailable the lock degrades to null semantics
    with a warning — preventing a Redis outage from breaking all crawls.
    """

    def __init__(
        self,
        gateway: RedisGateway,
        *,
        ttl_ms: int = _DEFAULT_REDIS_TTL_MS,
        poll_interval_ms: float = _POLL_INTERVAL_S * 1000,
    ) -> None:
        self._gateway = gateway
        self._ttl_ms = max(1, int(ttl_ms))
        self._poll_s = max(0.01, float(poll_interval_ms) / 1000.0)

    @staticmethod
    def _key(profile_path: Path) -> str:
        digest = hashlib.sha256(str(profile_path).encode()).hexdigest()[:24]
        return f"{_KEY_PREFIX}:{digest}"

    def acquire(self, profile_path: Path, *, timeout_ms: int) -> ProfileLockLease:
        key = self._key(profile_path)
        token = uuid.uuid4().hex
        deadline = time.monotonic() + timeout_ms / 1000.0

        client = self._gateway.get_client()
        if client is None:
            logger.warning(
                "profile_lock_redis_unavailable",
                extra={"profile_path": str(profile_path)},
            )
            return ProfileLockLease(
                profile_path=profile_path,
                backend="redis_failopen",
                _token=token,
            )

        while True:
            try:
                ok = client.set(key, token.encode("utf-8"), nx=True, px=self._ttl_ms)
            except Exception:
                logger.warning(
                    "profile_lock_redis_error",
                    extra={"profile_path": str(profile_path)},
                    exc_info=True,
                )
                self._gateway.reset()
                return ProfileLockLease(
                    profile_path=profile_path,
                    backend="redis_failopen",
                    _token=token,
                )

            if ok:
                logger.debug(
                    "profile_lock_acquired",
                    extra={
                        "profile_path": str(profile_path),
                        "backend": "redis",
                        "ttl_ms": self._ttl_ms,
                    },
                )
                return ProfileLockLease(
                    profile_path=profile_path,
                    backend="redis",
                    _token=token,
                    _ttl_ms=self._ttl_ms,
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RequestError(
                    f"Timeout aguardando profile lock Redis "
                    f"({timeout_ms} ms) para {profile_path}.",
                    code="PROFILE_LOCK_TIMEOUT",
                    retryable=True,
                )

            logger.debug(
                "profile_lock_waiting",
                extra={
                    "profile_path": str(profile_path),
                    "remaining_ms": round(remaining * 1000),
                    "backend": "redis",
                },
            )
            time.sleep(min(self._poll_s, remaining))

    def release(self, lease: ProfileLockLease) -> None:
        if lease.backend.startswith("redis_failopen") or lease.backend == "null":
            return
        stop = lease._renew_stop
        worker = lease._renew_thread
        if stop is not None:
            stop.set()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        lease._renew_stop = None
        lease._renew_thread = None
        key = self._key(lease.profile_path)
        client = self._gateway.get_client()
        if client is None:
            return
        try:
            client.eval(_RELEASE_LUA, 1, key, lease._token.encode("utf-8"))
            logger.debug(
                "profile_lock_released",
                extra={"profile_path": str(lease.profile_path), "backend": "redis"},
            )
        except Exception:
            logger.warning(
                "profile_lock_redis_release_error",
                extra={"profile_path": str(lease.profile_path)},
                exc_info=True,
            )
            self._gateway.reset()

    def retain(self, lease: ProfileLockLease) -> bool:
        """Renew the Redis lease while a persistent browser owns its profile."""
        if lease.backend != "redis":
            return False
        if lease._renew_thread is not None and lease._renew_thread.is_alive():
            return True
        if lease._ttl_ms is None:
            return False
        stop = threading.Event()
        lease._renew_stop = stop
        worker = threading.Thread(
            target=self._renew_loop,
            args=(lease, stop),
            name="camoufox-profile-lock-renew",
            daemon=True,
        )
        lease._renew_thread = worker
        worker.start()
        return True

    def _renew_loop(self, lease: ProfileLockLease, stop: threading.Event) -> None:
        interval_s = max(0.05, (lease._ttl_ms or self._ttl_ms) / 3000.0)
        key = self._key(lease.profile_path)
        while not stop.wait(interval_s):
            client = self._gateway.get_client()
            if client is None:
                lease._lost_event.set()
                logger.warning(
                    "profile_lock_renew_lost",
                    extra={
                        "profile_path": str(lease.profile_path),
                        "reason": "redis_unavailable",
                    },
                )
                return
            try:
                renewed = client.eval(
                    _RENEW_LUA,
                    1,
                    key,
                    lease._token.encode("utf-8"),
                    str(lease._ttl_ms or self._ttl_ms),
                )
            except Exception:
                self._gateway.reset()
                lease._lost_event.set()
                logger.warning(
                    "profile_lock_renew_lost",
                    extra={
                        "profile_path": str(lease.profile_path),
                        "reason": "redis_error",
                    },
                    exc_info=True,
                )
                return
            if not renewed:
                lease._lost_event.set()
                logger.warning(
                    "profile_lock_renew_lost",
                    extra={
                        "profile_path": str(lease.profile_path),
                        "reason": "token_mismatch",
                    },
                )
                return


# ---------------------------------------------------------------------------
# File implementation (secondary / alternative)
# ---------------------------------------------------------------------------


class FileProfileLock:
    """fcntl.flock-based profile lock (Linux).

    WARNING: Advisory file locks may not be propagated across different container
    filesystems on the same bind mount (Docker Desktop Windows + VirtioFS).
    Use only after proving on a real bind mount via
    scripts/probe_profile_lock_multiprocess.py.
    On Windows (no fcntl) this degrades to no-op with a warning.

    Thread-safe: each acquisition opens a separate file descriptor.
    The open fd keeps the lock alive; closing it (or the process dying) releases it.
    """

    def __init__(self) -> None:
        # Map token → open file handle so we can close on release.
        self._handles: dict[str, IO[str]] = {}
        self._lock = threading.Lock()

    def acquire(self, profile_path: Path, *, timeout_ms: int) -> ProfileLockLease:
        try:
            import fcntl as _fcntl
        except ImportError:
            logger.warning(
                "profile_lock_file_unavailable",
                extra={
                    "profile_path": str(profile_path),
                    "reason": "fcntl_not_available",
                },
            )
            return ProfileLockLease(
                profile_path=profile_path,
                backend="file_noop",
                _token=uuid.uuid4().hex,
            )

        lock_file = profile_path / ".profile.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        deadline = time.monotonic() + timeout_ms / 1000.0

        fd = open(lock_file, "a")  # noqa: WPS515 — kept open intentionally
        try:
            while True:
                try:
                    _fcntl.flock(fd.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    with self._lock:
                        self._handles[token] = fd
                    logger.debug(
                        "profile_lock_acquired",
                        extra={"profile_path": str(profile_path), "backend": "file"},
                    )
                    return ProfileLockLease(
                        profile_path=profile_path,
                        backend="file",
                        _token=token,
                        _file_handle=fd,
                    )
                except (BlockingIOError, OSError):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        fd.close()
                        raise RequestError(
                            f"Timeout aguardando profile lock de arquivo "
                            f"({timeout_ms} ms) para {profile_path}.",
                            code="PROFILE_LOCK_TIMEOUT",
                            retryable=True,
                        ) from None
                    logger.debug(
                        "profile_lock_waiting",
                        extra={
                            "profile_path": str(profile_path),
                            "remaining_ms": round(remaining * 1000),
                            "backend": "file",
                        },
                    )
                    time.sleep(min(_POLL_INTERVAL_S, remaining))
        except RequestError:
            raise
        except Exception:
            fd.close()
            raise

    def release(self, lease: ProfileLockLease) -> None:
        if lease.backend in ("file_noop", "null"):
            return
        with self._lock:
            fd = self._handles.pop(lease._token, None)
        if fd is None:
            return
        try:
            import fcntl as _fcntl

            _fcntl.flock(fd.fileno(), _fcntl.LOCK_UN)
        except Exception:
            pass
        finally:
            try:
                fd.close()
            except Exception:
                pass
        logger.debug(
            "profile_lock_released",
            extra={"profile_path": str(lease.profile_path), "backend": "file"},
        )

    def retain(self, lease: ProfileLockLease) -> bool:
        return lease.backend == "file"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_profile_lock(
    mode: str,
    redis_gateway: RedisGateway | None = None,
    *,
    redis_ttl_ms: int = _DEFAULT_REDIS_TTL_MS,
) -> NullProfileLock | RedisProfileLock | FileProfileLock:
    """Build the profile lock implementation based on CAMOUFOX_PROFILE_LOCK mode.

    Args:
        mode:           One of "redis", "file", "off".
        redis_gateway:  Required when mode="redis".
        redis_ttl_ms:   Redis key TTL (crash-safety backstop).

    Returns the appropriate implementation. Falls back gracefully:
        redis + no gateway → NullProfileLock (warn).
        file on Windows    → FileProfileLock (which itself degrades to noop).
    """
    mode = (mode or "off").strip().lower()

    if mode == "off":
        logger.warning(
            "profile_lock_mode_off",
            extra={"mode": mode},
        )
        return NullProfileLock()

    if mode == "redis":
        if redis_gateway is None:
            logger.warning(
                "profile_lock_redis_no_gateway",
                extra={"fallback": "null"},
            )
            return NullProfileLock()
        return RedisProfileLock(redis_gateway, ttl_ms=redis_ttl_ms)

    if mode == "file":
        return FileProfileLock()

    logger.warning(
        "profile_lock_unknown_mode",
        extra={"mode": mode, "fallback": "null"},
    )
    return NullProfileLock()
