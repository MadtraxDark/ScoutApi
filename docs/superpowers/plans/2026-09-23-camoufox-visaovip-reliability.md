# Camoufox Reliability + Visão VIP Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Endurecer Camoufox com scheduling limitado, ownership cross-process, half-open single-flight e budgets explícitos; medir C1/C2/C3; só então fechar causa raiz Visão VIP e (se validado) Strategy A Server Action + fallback B — sem reacoplar Search/PDP.

**Architecture:** Extrair `BrowserScheduler` + `BrowserSlot` em volta do Camoufox existente (owner-thread preservado); circuits por failure domain com `claim_trial` atômico; budgets separados no Match; Visão VIP multi-strategy só A+B com contract detection. Capacity default=1 até benchmark.

**Tech Stack:** Python 3, Camoufox/Playwright sync, FastAPI modular monolith, pytest, Docker Compose (`api` + `monitor` + `match-runner` sharing `./data/camoufox-profiles`).

**Spec aprovado:** `docs/superpowers/specs/2026-09-23-camoufox-visaovip-reliability-design.md`

## Global Constraints

- PDP ≠ Store Search (ADR 0038) — Search só em `matching/search_adapters/`
- Não matcher por store; não hardcode S25/ASUS/smartphone
- Não pool N browsers sem evidência; default `CAMOUFOX_BROWSER_CAPACITY=1`
- Nunca compartilhar `user_data_dir` entre owners simultâneos
- Half-open: non-probe → **fail-fast** (nunca wait-on-probe)
- ERROR técnico ≠ NO_MATCH; MatchRun nunca eternamente RUNNING
- Proxy Cost Mode / CAPTCHA / auth-wall rules permanecem
- Scraper/Camoufox navigation imutável salvo exceções (challenge/auth/proxy-cost) — scheduler/ownership/circuit são infraestrutura permitida
- Sem SerpAPI / Google / Bing como discovery principal
- Sem autocomplete/sitemap/local index sem evidência
- Output humano pt-BR; códigos técnicos em inglês
- Não hardcode `search_query_budget=5` fora de Settings
- Feature flags / Settings para rollback em cada fase crítica

## File map (planned)

| Path | Responsibility |
|---|---|
| `src/scout_api/modules/crawler/core/browser_scheduler.py` | Bounded queue, slots, acquire/release, metrics |
| `src/scout_api/modules/crawler/core/browser_slot.py` | Slot identity + warm session handle |
| `src/scout_api/modules/crawler/core/profile_lock.py` | Cross-process profile ownership |
| `src/scout_api/modules/crawler/core/browser_health.py` | Infra circuit + atomic `claim_trial` |
| `src/scout_api/modules/crawler/core/store_capability_health.py` | `(store, capability)` circuit |
| `src/scout_api/modules/crawler/core/failure_domains.py` | Enum + logging helpers |
| `src/scout_api/modules/matching/attempt_budget.py` | Query / external / browser-nav budgets |
| `src/scout_api/modules/matching/search_adapters/strategy.py` | Thin strategy orchestration (optional) |
| `src/scout_api/modules/matching/search_adapters/paraguay/visaovip.py` | Strategies A/B + contract detection |
| `src/scout_api/modules/crawler/services/html_fetcher.py` | Wire scheduler; shrink God-class over time |
| `src/scout_api/core/config.py` + `.env.example` | New settings |
| `scripts/bench_camoufox_capacity.py` | C1/C2/C3 harness |
| `scripts/probe_visaovip_serp_ab.py` | S25 vs B650M capture |
| `scripts/probe_visaovip_search_action.py` | Capture Server Action contract |
| `tests/unit/test_browser_scheduler.py` | Queue/capacity/cancel/fairness |
| `tests/unit/test_browser_circuit_claim_trial.py` | Half-open single-flight |
| `tests/unit/test_profile_lock.py` | Cross-process semantics (as far as unit can) |
| `tests/unit/test_attempt_budget.py` | Budgets |
| `tests/unit/test_visaovip_strategies.py` | A/B chain + contract invalid |
| `tests/integration/...` | Multi-process lock on real volume when CI allows |
| `docs/adr/0039-...` | Só se capacity>1 ou scheduler for decisão durável |
| `memory/working/2026-09-23-camoufox-visaovip-reliability.md` | Logs de trilhas |

---

### Task 0: Phase 0 — Baseline / reproduction

**Files:**
- Create/Update: `memory/working/2026-09-23-camoufox-visaovip-reliability.md`
- Create: `scripts/bench_camoufox_capacity.py` (skeleton metrics only)
- Test: N/A (measurement)

**Interfaces:**
- Produces: baseline numbers for C1 (current code) before changes: launch/reuse/fail counts, P50/P95 of a small nav set, RSS sample

- [ ] **Step 1: Record versions**

Run in container:

```bash
docker compose exec match-runner python -c "import camoufox, playwright; print(camoufox.__version__ if hasattr(camoufox,'__version__') else 'n/a'); import importlib.metadata as m; print('playwright', m.version('playwright'))"
```

Log binary/path if available. Append to working log under `CAMOUFOX_RELIABILITY / baseline`.

- [ ] **Step 2: Minimal infra baseline (current capacity=1 behavior)**

Run 10 sequential warm fetches against a cheap stable URL used today (or store origin warmup). Record `browser_launch` / `browser_reuse` observes if already emitted.

- [ ] **Step 3: Confirm process topology**

Document in working log: Search Match = `match-runner`; PDP refresh = `monitor`; shared volume = `./data/camoufox-profiles`. Do **not** start distributed store circuit yet.

- [ ] **Step 4: Commit** (only if user asked) — otherwise leave uncommitted until user requests.

**Rollback:** N/A (read-only).

---

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

### Task 4: Phase 4 — Cross-process profile ownership

**Files:**
- Create: `src/scout_api/modules/crawler/core/profile_lock.py`
- Modify: `browser_slot.py` / scheduler acquire path
- Test: `tests/unit/test_profile_lock.py`
- Create: `scripts/probe_profile_lock_multiprocess.py`

**Decision gate (must run before locking implementation choice):**

1. On **real** `./data/camoufox-profiles` bind (Docker Desktop Windows): Process A holds lock; B cannot acquire same profile.
2. Kill -9 Process A; measure time until B can acquire (crash release).
3. If file lock unreliable on bind → use Redis SET NX PX pattern from `DistributedSingleFlight` (TTL + compare-and-delete) as primary for profile lease; keep lock file as best-effort secondary.

- [ ] **Step 1: Write probe script + record results in working log `PROFILE_LOCK_PROOF`**

- [ ] **Step 2: Implement chosen locker behind `ProfileLock` protocol**

```python
class ProfileLock(Protocol):
    def acquire(self, profile_path: Path, *, timeout_ms: int) -> ProfileLockLease: ...
    def release(self, lease: ProfileLockLease) -> None: ...
```

- [ ] **Step 3: Scheduler acquire: slot path → ProfileLock → then warm session**

- [ ] **Step 4: Unit tests + script PASS on project volume**

**Rollback:** `CAMOUFOX_PROFILE_LOCK=off` (dev only; warn in logs).

---

### Task 5: Phase 5 — Capacity C1 / C2 / C3 benchmark

**Files:**
- Complete: `scripts/bench_camoufox_capacity.py`
- Update working log with tables

**Must measure per config:** success/fail, launch fail, nav fail, circuit opens, queue_wait, launch/nav latency, p50/p95/max, throughput, CPU, memory, firefox processes, launch/reuse/recycle counts, timeouts, lock errors.

Layer1 infra + Layer2 Visão VIP SERP + one other browser store. Same image/env.

- [ ] **Step 1: Implement harness with `--capacity 1|2|3` creating isolated `slot-*` dirs**

- [ ] **Step 2: Run C1, C2, C3; store JSON results under `memory/working/`**

- [ ] **Step 3: Apply acceptance rule; write DECISION paragraph**

**Rollback:** N/A measurement.

---

### Task 6: Phase 6 — Capacity decision + ADR

**Files:**
- Update: `docs/adr/0032-...` only via **new** ADR if needed (never silent flip)
- Possibly create: `docs/adr/0039-bounded-browser-scheduler.md`
- Update: `docs/adr/README.md`, `AGENTS.md` (short invariant), `.cursor/rules/` short rule if durable

- [ ] **Step 1: If C1 wins — ADR emend/new documenting scheduler+queue+lock; keep capacity=1; ADR 0032 pool rejection stands**

- [ ] **Step 2: If C2/C3 wins — new ADR superseding 0032’s “reject N browsers” clause with evidence + isolated profiles; set default capacity**

- [ ] **Step 3: Set production default Settings to decided capacity**

**Rollback:** force `CAMOUFOX_BROWSER_CAPACITY=1`.

---

### Task 7: Phase 7 — Visão VIP S25 vs B650M root-cause A/B

**Files:**
- Create: `scripts/probe_visaovip_serp_ab.py`
- Update: working log `VISAO_VIP_DISCOVERY`

**Prerequisite:** Camoufox infra healthy (Phases 1–4); capacity decided or temporarily C1.

Capture per op: query, URL, slot_id, profile, HTTP status, final URL, nav/settle, DOM size, `/prod/` count, challenge markers, network failures, Server Action requests (if any), duration.

- [ ] **Step 1: Run A/B same container**

- [ ] **Step 2: Write root-cause verdict (infra vs WAF vs incomplete vs genuine empty vs query)**

- [ ] **Step 3: Gate — do not start Task 9 until verdict written**

**Rollback:** N/A.

---

### Task 8: Phase 8 — Validate Server Action contract

**Files:**
- Create: `scripts/probe_visaovip_search_action.py`
- Possibly fixture: `tests/fixtures/visaovip/search_action_*.bin` (sanitized)

Capture: POST URL, headers, Next-Action / action id, payload, response, cookies/session, locale, build coupling.

- [ ] **Step 1: Capture real browser POST for B650M and S25**

- [ ] **Step 2: Determine if action ID is build-stable; if not, design discovery (parse chunk / bootstrap) — no hardcode brittle ID without refresh path**

- [ ] **Step 3: Go / No-Go for Strategy A in working log**

If No-Go → skip Task 9; keep Strategy B only; still do taxonomy + store circuit.

**Rollback:** N/A.

---

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

### Task 10: Phase 10 — Strategy B fallback + error taxonomy

**Files:**
- Modify: `store_search_service.py`, `visaovip.py`, `browser_health.py` consumers, Match error mapping
- Modify: frontend-facing message maps if any in API schemas
- Test: unit taxonomy + strategy chain

- [ ] **Step 1: Prefer new codes; map legacy `UPSTREAM_BLOCKED` on read only**

- [ ] **Step 2: Chain A→B; all blocked → ERROR `SEARCH_INCOMPLETE_RESPONSE` or `UPSTREAM_WAF_BLOCKED`; genuine zero → empty candidates**

- [ ] **Step 3: Ensure UI path never returns raw snake_case as user message**

- [ ] **Step 4: pytest PASS**

**Rollback:** feature flag off Strategy A; alias map keeps old clients working.

---

### Task 11: Phase 11 — Store + capability circuit

**Files:**
- Create: `src/scout_api/modules/crawler/core/store_capability_health.py`
- Wire: `store_search_service.py` (search), **not** PDP spider path for search failures
- Test: unit isolation search vs product_scrape; NO_RESULTS does not trip; WAF repeated does

Process-local initially (Match Search ≈ match-runner). Document if api/monitor later run Search.

- [ ] **Step 1: Implement claim_trial same semantics as infra; separate state**

- [ ] **Step 2: Wire search failures with `failure_domain=store_capability`**

- [ ] **Step 3: Prove PDP scrape still works when search circuit OPEN (unit/integration)**

**Rollback:** `STORE_CAPABILITY_CIRCUIT_ENABLED=false`.

---

### Task 12: Phase 12 — Integration / load / soak / chaos

**Files:**
- Tests under `tests/unit` + scripts chaos
- Working log before/after metrics

Chaos checklist (each must leave MatchRun terminal; no false NO_MATCH):

- [ ] browser process killed
- [ ] launch timeout
- [ ] owner-thread failure simulated
- [ ] profile lock held by peer
- [ ] queue full
- [ ] store WAF block
- [ ] operation nav timeout
- [ ] Server Action invalid contract (if A enabled)

Soak: reuse/recycle/memory/profile/queue/circuit for decided capacity until evidence enough (minutes, not hours blindly).

**Coverage gate:** compare eligible/attempted stores, candidates, PDPs, MATCH/NO_MATCH/ERROR — reject “faster” if coverage silently dropped.

---

### Task 13: Phase 13 — Product Match multi-category regressions

Live (or container) Match:

- [ ] Samsung Galaxy S25 Ultra (real identity; no hardcode in code)
- [ ] ASUS TUF Gaming B650M-E WIFI
- [ ] ≥1 CPU, GPU, RAM/SSD
- [ ] One store blocked → Run completes
- [ ] Visão VIP Search ERROR ≠ PDP crawl broken

Record before/after: duration, stores, MATCH/NO_MATCH/ERROR, candidates, browser usage.

---

### Task 14: Phase 14 — Docs / ADR / final report

- [ ] Update `docs/crawler/stores/visaovip.md` (strategies, taxonomy, limitations)
- [ ] Update `docs/matching/README.md` if budgets/circuits affect Match contract
- [ ] ADR 0037/0032/0039 as decided in Phase 6
- [ ] Short invariant in `AGENTS.md` + `.cursor/rules/` (browser bounded + failure domains + multi-strategy when native fallback exists)
- [ ] Pending index: only if something remains incomplete
- [ ] Final report pt-BR sections 1–5 as requested in original goal
- [ ] Update working log trilhas with before/after

---

## Execution order (logical)

```text
0 baseline
→ 1 scheduler C1
→ 2 claim_trial
→ 3 budgets
→ 4 profile lock (prove on real volume)
→ 5 bench C1/C2/C3
→ 6 decide capacity + ADR
→ 7 Visão VIP A/B root cause
→ 8 Server Action contract Go/No-Go
→ 9 Strategy A (if Go)
→ 10 B fallback + taxonomy
→ 11 store+capability circuit
→ 12 load/soak/chaos
→ 13 Match regressions
→ 14 docs/report
```

## Self-review vs approved spec

| Spec requirement | Plan task |
|---|---|
| Bounded queue + queue_capacity + cancel | Task 1 |
| Half-open fail-fast claim_trial | Task 2 |
| Separate budgets + observability | Task 3 |
| Cross-process profile lock proven | Task 4 |
| C1/C2/C3 + acceptance rule | Task 5–6 |
| Visão VIP A/B before Strategy A | Task 7 |
| Server Action contract + detection | Task 8–9 |
| No fake strategies | Task 9–10 |
| Store capability circuit; NO_RESULTS no trip | Task 11 |
| Chaos + coverage gate | Task 12–13 |
| ADR / docs | Task 6, 14 |
| No Search in PDP spiders | Global Constraints |

**Placeholder scan:** none intentional TBD left without gate (Task 8 Go/No-Go).

---

## Handoff

Plan saved to `docs/superpowers/plans/2026-09-23-camoufox-visaovip-reliability.md`.

**Two execution options (after you approve this plan):**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — this session with executing-plans checkpoints  

**Which approach?** Do not start implementation until you explicitly approve this plan.
