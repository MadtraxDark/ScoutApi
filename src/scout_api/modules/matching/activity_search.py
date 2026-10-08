"""Bounded search contract shared by catalog history service and repository."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from scout_api.modules.matching.activity_schemas import ActivityType

# Keep the UI's normalization in utils/display/change-search.ts in sync.
ACCENTED = "áàâãäåéèêëíìîïóòôõöúùûüçñýÿ"
PLAIN = "aaaaaaeeeeiiiiooooouuuucnyy"


def search_terms(query: str) -> tuple[str, ...]:
    normalized = query.translate(str.maketrans(ACCENTED.upper(), PLAIN))
    normalized = normalized.lower().translate(str.maketrans(ACCENTED, PLAIN))
    terms = tuple(dict.fromkeys(normalized.split()))
    if len(query) > 200 or len(terms) > 8:
        raise ValueError("INVALID_CHANGE_FILTERS")
    return terms


@dataclass(frozen=True)
class ActivityFilters:
    q: str = ""
    event_type: ActivityType | None = None
    store: str | None = None
    product_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
