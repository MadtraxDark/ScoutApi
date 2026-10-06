### Task 1: Phase 1 — BrowserScheduler C1 hardening

**Files:**
- Create: `src/scout_api/modules/crawler/core/failure_domains.py`
- Create: `src/scout_api/modules/crawler/core/browser_slot.py`
- Create: `src/scout_api/modules/crawler/core/browser_scheduler.py`
- Modify: `src/scout_api/core/config.py`, `.env.example`
- Modify: `src/scout_api/modules/crawler/services/html_fetcher.py` (wire acquire/release; keep owner-thread)
- Test: `tests/unit/test_browser_scheduler.py`

**Interfaces:**
- Consumes: existing `_WarmBrowserSession` / `_PlaywrightOwnerLoop` patterns
- Produces:

```python
class FailureDomain(StrEnum):
    BROWSER_INFRASTRUCTURE = "browser_infrastructure"
    STORE_CAPABILITY = "store_capability"
    OPERATION = "operation"


@dataclass
class BrowserSlot:
    slot_id: int
    profile_path: Path
    owner: str | None
    state: str  # idle|busy|recycling|poisoned|closed
    started_at: datetime | None
    last_used_at: datetime | None
    fetch_count: int
    health: str


class BrowserScheduler:
    def __init__(
        self, *, capacity: int, queue_capacity: int, queue_timeout_ms: int
    ): ...
    def acquire(
        self, *, cancel_event: threading.Event | None = None
    ) -> BrowserSlotLease: ...
    def release(self, lease: BrowserSlotLease, *, poison: bool = False) -> None: ...
    def snapshot(self) -> dict[str, object]: ...  # depth, capacity, active, waits
```

Settings:

```python
camoufox_browser_capacity: int = 1
camoufox_browser_queue_capacity: int = 32
camoufox_queue_timeout_ms: int = 60_000
```

- [ ] **Step 1: Write failing tests for bounded queue**

```python
def test_capacity_one_second_job_waits_or_queues():
    # capacity=1; two acquires → second waits until release or hits queue

def test_queue_saturated_fails_fast():
    # capacity=1, queue_capacity=1; third acquire → controlled error (not hang)

def test_cancel_while_queued_does_not_get_slot():
    # cancel_event set while waiting → leaves queue; never runs work

def test_fifo_order():
    # three waiters; releases grant in enqueue order
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/unit/test_browser_scheduler.py -v
```

- [ ] **Step 3: Implement scheduler + slot (capacity default 1)**

FIFO queue; on saturate raise `RequestError(code="BROWSER_QUEUE_SATURATED", retryable=False)` with `failure_domain=browser_infrastructure`. Log `slot_id` on every acquire/release. Metrics: `browser_queue_depth`, `browser_queue_capacity`, `browser_queue_wait_ms`, `browser_queue_timeout_count`, `browser_active_jobs`, `browser_capacity`.

- [ ] **Step 4: Wire `CamoufoxHtmlFetcher.fetch` to acquire→work→release**

Do not change navigation/selectors. Pass `slot_id` into observe/log context.

- [ ] **Step 5: Run unit tests — expect PASS**

- [ ] **Step 6: Feature flag / rollback**

`CAMOUFOX_BROWSER_SCHEDULER_ENABLED=true` (default true after green). If false, legacy lock-only path (temporary). Remove legacy after soak.

**Rollback:** set `CAMOUFOX_BROWSER_SCHEDULER_ENABLED=false` or capacity/queue settings to safe defaults.

---
