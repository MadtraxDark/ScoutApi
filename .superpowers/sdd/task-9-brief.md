### Task 9: Phase 9 — Implement Strategy A (only if Go)

**Files:**
- Modify: `src/scout_api/modules/matching/search_adapters/paraguay/visaovip.py`
- Create helpers as needed under `search_adapters/paraguay/visaovip_*.py`
- Test: `tests/unit/test_visaovip_strategies.py`
- Flag: `VISAOVIP_SEARCH_ACTION_ENABLED` default false until green, then true

**Interfaces:**

```python
class StrategyResult(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"  # contract invalid / action missing
    INVALID_RESPONSE = "invalid_response"
    ERROR = "error"
```

Contract detection: unexpected shape → `UNAVAILABLE`/`INVALID_RESPONSE` → **fallback B**, never empty-as-NO_MATCH.

- [ ] **Step 1: Failing tests for parse success, zero results, invalid contract**

- [ ] **Step 2: Implement + enable behind flag**

- [ ] **Step 3: Live smoke B650M + S25 search-only**

**Rollback:** `VISAOVIP_SEARCH_ACTION_ENABLED=false`.

---
