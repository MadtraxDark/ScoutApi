"""Category-aware product identity profiles."""

from scout_api.modules.crawler.utils.category_profiles.base import CategoryProfile
from scout_api.modules.crawler.utils.category_profiles.registry import (
    all_profiles,
    detect_category,
    ensure_profiles_loaded,
    get_profile,
    register_profile,
)

__all__ = [
    "CategoryProfile",
    "all_profiles",
    "detect_category",
    "ensure_profiles_loaded",
    "get_profile",
    "register_profile",
]
