import pytest

from scout_api.modules.crawler.core.fetch_metrics import FetchCostMetrics


def test_stage_records_duration_and_preserves_return_value() -> None:
    metrics = FetchCostMetrics()
    result = object()
    assert metrics.measure("navigation", lambda: result) is result
    assert metrics.as_log_dict()["stage_timings_ms"]["navigation"] >= 0


def test_stage_records_failure_without_swallowing_exception() -> None:
    metrics = FetchCostMetrics()
    failure = ValueError("failure")

    def operation() -> None:
        raise failure

    with pytest.raises(ValueError) as caught:
        metrics.measure("browser_acquire", operation)
    assert caught.value is failure
    assert metrics.stage_timings_ms["browser_acquire"] >= 0
