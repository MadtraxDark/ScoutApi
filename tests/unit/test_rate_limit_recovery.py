"""Recovery under continuous traffic, without weakening scoped limits."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from scout_api.core.config import Settings
from scout_api.core.rate_limit import RateLimiter


def test_exhausted_bucket_recovers_without_idle_window(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("scout_api.core.rate_limit.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(settings=Settings(redis_url="", rate_limit_enabled=True))

    def hit():
        return limiter.check(scope="crawler", identity="user", limit=10)

    for _ in range(10):
        assert hit().allowed
    assert not hit().allowed
    assert hit().retry_after == 6
    for second in range(1, 6):
        clock[0] = 100.0 + second
        assert not hit().allowed
    clock[0] = 106.0
    assert hit().allowed
    assert not hit().allowed
    clock[0] = 112.0
    assert hit().allowed


def test_concurrent_requests_cannot_overspend_bucket():
    limiter = RateLimiter(settings=Settings(redis_url="", rate_limit_enabled=True))
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _: limiter.check(scope="poll", identity="user", limit=5),
                range(40),
            )
        )
    assert sum(result.allowed for result in results) == 5


@pytest.mark.parametrize(
    "field",
    [
        "rate_limit_default_per_minute",
        "rate_limit_auth_per_minute",
        "rate_limit_crawler_per_minute",
        "rate_limit_poll_per_minute",
    ],
)
def test_rate_limit_configuration_requires_positive_capacity(field):
    with pytest.raises(ValueError):
        Settings(**{field: 0})
