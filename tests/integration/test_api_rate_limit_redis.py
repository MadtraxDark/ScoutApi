"""Actual Lua verification; only isolated disposable Redis keys are modified."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from redis import Redis

from scout_api.core.config import Settings
from scout_api.core.rate_limit import RateLimiter


@pytest.mark.integration
def test_redis_bucket_recovers_and_is_atomic():
    url = os.environ.get("TEST_RATE_LIMIT_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_RATE_LIMIT_REDIS_URL for Redis Lua verification")
    client = Redis.from_url(url)

    class Gateway:
        def get_client(self):
            return client

    identity = "test-rate-limit-" + uuid4().hex
    key = "rl:tb:crawler:" + identity
    limiter = RateLimiter(
        settings=Settings(rate_limit_enabled=True), redis_gateway=Gateway()
    )

    def hit(_=None):
        return limiter.check(scope="crawler", identity=identity, limit=10)

    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(hit, range(40)))
        assert sum(result.allowed for result in results) == 10
        assert client.type(key) == b"hash"  # Redis, never silent local fallback.
        assert hit().retry_after == 6
        seconds, micros = client.time()
        client.hset(
            key,
            mapping={"tokens": 0, "updated_at": seconds + micros / 1e6 - 6.1},
        )
        assert hit().allowed
        assert not hit().allowed
        assert 1 <= client.ttl(key) <= 60
    finally:
        client.delete(key)
        client.close()
