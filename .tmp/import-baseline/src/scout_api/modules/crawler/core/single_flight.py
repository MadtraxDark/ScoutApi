"""In-process single-flight coalescing for identical canonical URLs."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock
from typing import TypeVar

T = TypeVar("T")


class SingleFlight:
    """Share one in-flight callable result across concurrent waiters."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._inflight: dict[str, Future[object]] = {}

    def do(self, key: str, fn: Callable[[], T]) -> T:
        leader = False
        with self._lock:
            existing = self._inflight.get(key)
            if existing is None:
                future: Future[object] = Future()
                self._inflight[key] = future
                leader = True
            else:
                future = existing

        if not leader:
            return future.result()  # type: ignore[return-value]

        try:
            result = fn()
        except Exception as exc:
            future.set_exception(exc)
            with self._lock:
                self._inflight.pop(key, None)
            raise
        else:
            future.set_result(result)
            with self._lock:
                self._inflight.pop(key, None)
            return result
