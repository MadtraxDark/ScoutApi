"""Persistent offer monitoring (DB-driven schedule)."""

from __future__ import annotations

__all__ = ["OfferMonitorService"]


def __getattr__(name: str) -> object:
    if name == "OfferMonitorService":
        from scout_api.modules.monitoring.service import OfferMonitorService

        return OfferMonitorService
    raise AttributeError(name)
