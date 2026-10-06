### Task 3: Phase 3 — Retry / attempt budgets

**Files:**
- Create: `src/scout_api/modules/matching/attempt_budget.py`
- Modify: `src/scout_api/core/config.py`, `.env.example`
- Modify: `src/scout_api/modules/matching/product_match_service.py`
- Modify: `src/scout_api/modules/matching/store_search_service.py` (emit attempt accounting hooks)
- Test: `tests/unit/test_attempt_budget.py`

**Interfaces:**

```python
@dataclass
class StoreAttemptBudget:
    queries_budget: int
    external_attempt_budget: int
    browser_navigation_budget: int
    queries_used: int = 0
    external_attempts: int = 0
    browser_navigations: int = 0
    stopped_reason: str | None = None

    def begin_query(self) -> bool: ...  # False → stop progressive
    def record_external(self) -> bool: ...  # False if exhausted
    def record_browser_nav(self) -> bool: ...
    def skip_cached(self) -> None: ...  # no consume
```

Settings (names exact):

```python
match_search_query_budget: int = 5  # MAX, not mandatory count
match_external_attempt_budget: int = 12
match_browser_navigation_budget: int = 8
```

- [ ] **Step 1: Tests — progressive stops early; cache hit no external; strategy A+B = 1 query; exhaustion sets stopped_reason**

- [ ] **Step 2: Implement + wire Match store loop; log final store summary fields**

- [ ] **Step 3: Document worst-case table in working log (must match spec §6)**

| Case | queries | external | browser nav |
|---|---|---|---|
| Best | 1 | 1 | 0–1 |
| Normal A→B | 1 | ≤2 | 1 |
| Worst allowed | ≤5 | ≤ min(12, 5×2 + proxy) | ≤8 |

- [ ] **Step 4: pytest PASS**

**Rollback:** raise budgets via env; or set query budget high temporarily (not preferred).

---
