from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import pytest

import scout_api.modules.crawler.services.html_fetcher as html_fetcher


class _FailingLaunch(AbstractContextManager[Any]):
    def __enter__(self) -> Any:
        raise TimeoutError("BrowserType.launch_persistent_context: Timeout exceeded")

    def __exit__(self, *args: object) -> None:
        return None


def test_failed_browser_launch_records_duration_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record(*args: object, **kwargs: object) -> None:
        events.append((args, kwargs))

    monkeypatch.setattr(html_fetcher, "observe", record)
    launch = html_fetcher._TimedBrowserLaunch(_FailingLaunch(), store="shoppingchina")

    with pytest.raises(TimeoutError, match="launch_persistent_context"):
        launch.__enter__()

    assert len(events) == 1
    args, kwargs = events[0]
    assert args[0] == "browser_launch"
    assert isinstance(args[1], float) and args[1] >= 0
    assert kwargs["stage"] == "shoppingchina"
    assert kwargs["force_event"] is True
    assert kwargs["context"] == {
        "outcome": "error",
        "error_type": "TimeoutError",
        "failure_kind": "launch_timeout",
    }
