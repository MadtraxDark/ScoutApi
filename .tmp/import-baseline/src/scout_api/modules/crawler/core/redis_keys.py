"""Versioned Redis key builders for scrape cache and coordination."""

from __future__ import annotations

from hashlib import sha256

from .fingerprints import canonicalize_url

KEY_PREFIX = "scout:v1"


def url_digest(url: str) -> str:
    """Stable SHA-256 of the canonical URL (never store raw URLs as keys)."""
    canonical = canonicalize_url(url)
    return sha256(canonical.encode("utf-8")).hexdigest()


def scrape_cache_key(url: str) -> str:
    return f"{KEY_PREFIX}:scrape:{url_digest(url)}"


def flight_lock_key(url: str) -> str:
    return f"{KEY_PREFIX}:flight:{url_digest(url)}"


def url_cooldown_key(url: str) -> str:
    return f"{KEY_PREFIX}:cooldown:url:{url_digest(url)}"


def domain_cooldown_key(hostname: str) -> str:
    host = hostname.strip().lower()
    return f"{KEY_PREFIX}:cooldown:domain:{host}"
