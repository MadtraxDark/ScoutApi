"""Logical purpose of a live scrape (observability + future cooldown policy).

Cooldownup protection still keys primarily on canonical URL + result cache.
Purpose distinguishes *why* a fetch was requested so callers can reuse the
same HTML outcome across offer refresh, match reference, and candidates
without treating internal reuse as a client duplicate.
"""

from __future__ import annotations

from enum import StrEnum


class ScrapePurpose(StrEnum):
    MANUAL_CRAWL = "manual_crawl"
    OFFER_REFRESH = "offer_refresh"
    MATCH_REFERENCE = "match_reference"
    MATCH_CANDIDATE = "match_candidate"
    STORE_SEARCH = "store_search"
    SCHEDULED_REFRESH = "scheduled_refresh"
    UNSPECIFIED = "unspecified"
