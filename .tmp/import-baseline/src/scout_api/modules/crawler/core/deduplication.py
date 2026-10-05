import time
from threading import Lock


class DeduplicationStore:
    """Small local store; replace with Redis SET NX for multi-worker deployments."""

    def __init__(self, ttl: int = 3600) -> None:
        self.ttl = ttl
        self._seen: dict[str, float] = {}
        self._lock = Lock()

    def claim(self, fingerprint: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._seen.get(fingerprint, 0) > now:
                return False
            self._seen[fingerprint] = now + self.ttl
            return True
