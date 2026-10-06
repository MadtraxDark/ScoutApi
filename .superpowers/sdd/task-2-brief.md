### Task 2: Phase 2 — Half-open single-flight

**Files:**
- Modify: `src/scout_api/modules/crawler/core/browser_health.py`
- Test: `tests/unit/test_browser_circuit_claim_trial.py`

**Interfaces:**
- Produces:

```python
class BrowserCircuitBreaker:
    def allow(self) -> bool: ...  # False if OPEN (not half-open grant)
    def claim_trial(self, *, now: datetime | None = None) -> TrialToken | None: ...
    def complete_trial(self, token: TrialToken, *, success: bool) -> None: ...

    # Crash safety: token has expires_at; snapshot() reaps expired trials → reopen OPEN
```

Semantics:

```text
CLOSED → allow True
OPEN → allow False (fail-fast)
cooldown elapsed → HALF_OPEN; claim_trial returns token for exactly one caller
others in HALF_OPEN without token → allow False / claim_trial None → fail-fast
success → CLOSED; failure → OPEN + new cooldown
```

- [ ] **Step 1: Failing tests**

```python
def test_ten_callers_one_probe(fake_clock):
    # open circuit; advance past cooldown; 10 threads claim_trial → exactly 1 token

def test_non_probe_fail_fast_does_not_wait():
    ...

def test_probe_crash_does_not_stick_half_open(fake_clock):
    # claim token; never complete; advance past trial TTL → state OPEN again (or re-claimable per design: reopen OPEN)

def test_success_closes_circuit():
    ...

def test_failure_reopens_and_resets_cooldown(fake_clock):
    ...
```

- [ ] **Step 2: Implement atomic claim under `threading.Lock` + trial TTL**

- [ ] **Step 3: Wire fetcher launch path: if OPEN → unavailable; if HALF_OPEN → claim or fail-fast; complete_trial in finally**

- [ ] **Step 4: pytest PASS**

**Rollback:** `BROWSER_CIRCUIT_FAILURE_THRESHOLD` / cooldown settings; temporary env `BROWSER_CIRCUIT_CLAIM_TRIAL=false` only if needed during hotfix (prefer not).

---
