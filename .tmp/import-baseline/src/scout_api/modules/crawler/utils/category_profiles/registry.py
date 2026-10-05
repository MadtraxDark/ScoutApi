"""Category profile registry — single source for detection and parsers."""

from __future__ import annotations

from scout_api.modules.crawler.utils.category_profiles.base import CategoryProfile

_PROFILES: dict[str, CategoryProfile] = {}
_ORDERED: list[CategoryProfile] = []


def register_profile(profile: CategoryProfile) -> CategoryProfile:
    """Register or replace a category profile."""
    existing = _PROFILES.get(profile.id)
    if existing is not None and existing in _ORDERED:
        _ORDERED.remove(existing)
    _PROFILES[profile.id] = profile
    _ORDERED.append(profile)
    _ORDERED.sort(key=lambda item: (item.detection_priority, item.id))
    return profile


def get_profile(category_id: str | None) -> CategoryProfile | None:
    if not category_id:
        return None
    return _PROFILES.get(category_id.casefold().strip())


def all_profiles() -> tuple[CategoryProfile, ...]:
    return tuple(_ORDERED)


def detect_category(title: str | None) -> str | None:
    """Best-effort category from registered profile needles (priority order)."""
    if not title:
        return None
    text = title.casefold()
    # Discrete GPU in a laptop title must not win over notebook chassis.
    notebook_hint = any(
        needle in text
        for needle in (
            "notebook",
            "laptop",
            "ultrabook",
            "macbook",
            "rog strix g",
            "strix g16",
            "strix g15",
            "zephyrus",
            "ideapad",
            "thinkpad",
            "vivobook",
            "legion ",
            "nitro v",
        )
    ) or (
        ("placa" not in text)
        and ("rtx" in text or "gtx" in text)
        and (
            "i9-" in text
            or "i7-" in text
            or "i5-" in text
            or "hx" in text
            or "strix" in text
        )
    )
    for profile in _ORDERED:
        if profile.id == "gpu" and notebook_hint:
            continue
        if any(needle in text for needle in profile.detection_needles):
            return profile.id
    return None


def ensure_profiles_loaded() -> None:
    """Import definitions so registration side-effects run once."""
    if _PROFILES:
        return
    from scout_api.modules.crawler.utils.category_profiles import (
        definitions,  # noqa: F401
    )

    assert definitions  # keep import used
