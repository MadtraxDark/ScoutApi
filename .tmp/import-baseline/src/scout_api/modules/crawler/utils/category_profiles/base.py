"""CategoryProfile: declarative identity / match / search contract per category.

Adding a category should mean registering a profile — not editing the resolver
core with new ``if/elif`` trees.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scout_api.modules.crawler.utils.product_identity import ParsedIdentity

TitleParser = Callable[[str, str], "ParsedIdentity"]
AttributeExtractor = Callable[[str], dict[str, str]]


@dataclass(frozen=True)
class CategoryProfile:
    """Declarative profile for one product category.

    ``parse_title`` is optional: categories without a title grammar still
    participate in detection, search filter catalogs, and match conflict lists.
    """

    id: str
    label: str
    detection_needles: tuple[str, ...] = ()
    detection_priority: int = 100
    """Lower number = checked earlier (more specific classes first)."""

    supported_attributes: tuple[str, ...] = ()
    """Attributes this category may expose when present in sources."""

    critical_conflict_keys: tuple[str, ...] = ()
    """Attribute keys where both sides present and unequal → match reject."""

    strong_match_keys: tuple[str, ...] = ()
    """Keys that weigh heavily when agreeing (MPN, model_number, chip, …)."""

    search_filters: tuple[str, ...] = ()
    """Optional query filters the API may accept for this category."""

    variant_from: tuple[str, ...] = ("edition",)
    """Which resolved keys feed the public ``variant`` string."""

    brand_aliases: Mapping[str, str] = field(default_factory=dict)
    """Folded alias → canonical brand display (evidence-based only)."""

    parse_title: TitleParser | None = None
    """Conservative title grammar. Abstains (ambiguous) when unsure."""

    extract_attributes: AttributeExtractor | None = None
    """Title → attribute map for critical/supported keys (conservative)."""

    specs_only_critical: tuple[str, ...] = ()
    """Critical keys that must never be invented from title (e.g. MPN)."""

    notes: str = ""

    def normalize_brand(self, brand: str | None) -> str | None:
        if not brand or not str(brand).strip():
            return None
        from scout_api.modules.crawler.utils.category_profiles.common import (
            apply_brand_alias,
        )

        return apply_brand_alias(str(brand), self.brand_aliases)


@dataclass(frozen=True)
class ProfileRegistryView:
    """Read-only snapshot used by detectors and docs."""

    profiles: Sequence[CategoryProfile]
