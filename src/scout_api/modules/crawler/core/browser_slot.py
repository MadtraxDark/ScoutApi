"""Browser slot identity for diagnostics and ownership tracking.

Each active Camoufox session is bound to exactly one BrowserSlot. Slot
state is in-memory only; persistence is optional and out of scope for C1.

Capacity=1 always uses slot-0. When capacity>1 is introduced (after
benchmark), each slot gets its own profile directory (slot-{id}) so Firefox
profile locks never clash across concurrent owners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scout_api.modules.crawler.core.profile_lock import ProfileLockLease


@dataclass
class BrowserSlot:
    """Runtime identity + metadata for a single Camoufox browser slot.

    States:
        idle        — session exists but no active fetch
        busy        — fetch in progress
        recycling   — session being replaced (max_fetches reached or poison)
        poisoned    — session unusable; must be closed and recreated
        closed      — session terminated; slot may be reassigned
    """

    slot_id: int
    profile_path: Path
    owner: str | None = None
    state: str = "idle"  # idle|busy|recycling|poisoned|closed
    started_at: datetime | None = None
    last_used_at: datetime | None = None
    fetch_count: int = 0
    health: str = "healthy"

    def as_dict(self) -> dict[str, object]:
        """Return a log-safe snapshot of this slot's identity."""
        return {
            "slot_id": self.slot_id,
            "profile_path": str(self.profile_path),
            "owner": self.owner,
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
            "fetch_count": self.fetch_count,
            "health": self.health,
        }


@dataclass
class BrowserSlotLease:
    """Handle returned by BrowserScheduler.acquire().

    Callers must pass this to BrowserScheduler.release() when the browser
    operation completes (whether success or error). Never share a lease
    across threads.

    ``_profile_lock_lease`` is an internal field managed by BrowserScheduler.
    Callers should not read or modify it directly.
    """

    slot_id: int
    _profile_lock_lease: ProfileLockLease | None = field(default=None, repr=False)
    _profile_lock_retained: bool = field(default=False, repr=False)
