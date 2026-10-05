from functools import lru_cache
from typing import Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ScoutApiV2"
    environment: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    redis_url: str | None = None
    redis_connect_timeout_seconds: float = 0.3
    redis_socket_timeout_seconds: float = 0.5
    # PostgreSQL / Supabase (SQLAlchemy + psycopg). Never use Supabase Data API.
    database_url: str | None = None
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 1800
    database_connect_timeout_seconds: int = 10
    database_statement_timeout_ms: int = 30_000
    database_sslmode: str | None = None
    database_application_name: str = "scout-api-v2"
    # --- API security (Supabase Auth). Never expose service_role to clients. ---
    # AUTH_REQUIRED: whether protected routes demand a client Bearer.
    # Alias AUTH_ENABLED kept for backward compatibility (same polarity).
    auth_required: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "auth_required",
            "AUTH_REQUIRED",
            "auth_enabled",
            "AUTH_ENABLED",
        ),
    )
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    # HS256 legacy/test only. Prefer JWKS (ES256/RS256) via supabase_url.
    supabase_jwt_secret: str | None = None
    supabase_jwt_audience: str = "authenticated"
    auth_google_redirect_url: str | None = None
    auth_frontend_success_url: str | None = None
    auth_admin_user_ids: str = ""
    cors_allowed_origins: str = ""
    trusted_proxy_ips: str = ""
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 120
    rate_limit_auth_per_minute: int = 20
    rate_limit_crawler_per_minute: int = 10
    # Lightweight SPA polling (match status, notifications) — separate bucket
    # so continuous polls do not starve CRUD or crawler start endpoints.
    rate_limit_poll_per_minute: int = 300
    scraper_user_agent: str = "ScoutApiV2/0.1 (+price-monitoring)"
    scraper_log_level: str = "INFO"
    scraper_default_concurrency: int = 2
    scraper_default_delay: float = 2.0
    autothrottle_enabled: bool = True
    autothrottle_start_delay: float = 2.0
    autothrottle_max_delay: float = 60.0
    autothrottle_target_concurrency: float = 0.5
    scraper_default_ttl: int = 3600
    scraper_retry_times: int = 3
    retry_backoff_base: float = 1.0
    retry_backoff_cap: float = 60.0
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_cooldown: int = 300
    scrape_url_cooldown_seconds: int = 300
    scrape_domain_min_interval_seconds: float = 15.0
    scrape_result_cache_ttl_seconds: int = 300
    scrape_distributed_lock_enabled: bool = True
    scrape_single_flight_lock_ttl_seconds: int = 180
    scrape_single_flight_wait_seconds: float = 120.0
    scrape_distributed_cooldown_enabled: bool = True
    mitmproxy_enabled: bool = False
    mitmproxy_url: str | None = None
    rotating_proxies_enabled: bool = False
    rotating_proxy_list_path: str | None = None
    scraper_http_timeout: int = 30
    camoufox_enabled: bool = True
    camoufox_headless: bool = True
    camoufox_humanize: bool = True
    camoufox_timeout_ms: int = 90_000
    # Playwright launch_persistent_context timeout (separate from page navigation).
    # Default Playwright is 180_000; keep much lower so a broken browser fails fast.
    camoufox_launch_timeout_ms: int = 45_000
    camoufox_settle_ms: int = 5_000
    camoufox_max_settle_attempts: int = 12
    # Process-wide circuit after structural Camoufox launch failures.
    browser_circuit_failure_threshold: int = 1
    browser_circuit_cooldown_seconds: int = 60
    camoufox_proxy_url: str | None = None
    camoufox_user_data_dir: str | None = None
    camoufox_disable_coop: bool = True
    camoufox_warmup_origin: bool = True
    # Keep Camoufox persistent context warm across fetches (ADR 0032).
    camoufox_warm_reuse: bool = True
    camoufox_warm_max_fetches: int = 40
    # --- BrowserScheduler (Phase 1 / C1 hardening) ---
    # Bounded FIFO queue replacing implicit unbounded _PlaywrightOwnerLoop queue.
    # Feature flag: set false to fall back to legacy lock-only path (rollback).
    camoufox_browser_scheduler_enabled: bool = True
    # Number of concurrent Camoufox slots. Default=1; do NOT increase without
    # benchmark evidence (C1 vs C2 vs C3). See docs/superpowers/plans/…
    camoufox_browser_capacity: int = 1
    # Max callers waiting for a slot before queue saturation error.
    camoufox_browser_queue_capacity: int = 32
    # Milliseconds a caller waits in queue before BROWSER_QUEUE_TIMEOUT.
    camoufox_queue_timeout_ms: int = 60_000
    # --- ProfileLock (Phase 4 / cross-process profile ownership) ---
    # Mode: "redis" (default, recommended) | "file" (fcntl, bind-mount proof required)
    #       | "off" (emergency bypass only — logs WARN on every acquire).
    # "redis" requires REDIS_URL. Falls back to no-op if Redis unavailable (fail-open).
    camoufox_profile_lock: str = "redis"
    # TTL (milliseconds) for Redis-backed profile lease.
    # Crash-safety backstop: lock auto-expires after this interval if holder dies.
    # Must exceed typical browser session lifetime (launch + N fetches).
    camoufox_profile_lock_ttl_ms: int = 10 * 60 * 1000  # 10 minutes
    # Milliseconds caller waits for a profile lock before PROFILE_LOCK_TIMEOUT.
    camoufox_profile_lock_timeout_ms: int = 30_000
    # Product Match: independent stores may overlap; Camoufox stays lock-serialized.
    match_store_concurrency: int = 3
    # Retry / attempt budgets per store per Match run (Phase 3 / spec §2).
    # match_search_query_budget: MAX queries — progressive stop, not mandatory count.
    # Never hardcode these defaults elsewhere — only here and .env.example.
    match_search_query_budget: int = 5
    match_external_attempt_budget: int = 12
    match_browser_navigation_budget: int = 8
    # Cost-aware Shopee controls (DataImpulse is billed primarily by GB).
    shopee_warmup_policy: str = "once_per_session"
    shopee_resource_blocking_enabled: bool = True
    # Challenge / CAPTCHA resolution (ADR 0017). Default: offline amazoncaptcha.
    captcha_solver_enabled: bool = True
    captcha_solver_provider: str = "amazoncaptcha"
    captcha_solver_max_attempts: int = 2
    # Auth wall bypass (ADR 0018). Credentials are operator-local — never commit.
    auth_bypass_enabled: bool = True
    auth_bypass_max_attempts: int = 2
    amazon_auth_email: str | None = None
    amazon_auth_password: str | None = None
    shopee_auth_email: str | None = None
    shopee_auth_password: str | None = None
    # --- Product images / Google Drive (ADR 0029). Backend-only secrets. ---
    google_drive_client_id: str | None = None
    google_drive_client_secret: str | None = None
    google_drive_refresh_token: str | None = None
    google_drive_root_folder_id: str | None = None
    image_max_bytes: int = 8_000_000
    image_max_dimension: int = 4096
    image_download_timeout_seconds: float = 30.0
    image_max_redirects: int = 5
    image_max_per_product: int = 20
    image_avif_quality: int = 60
    image_avif_max_concurrency: int = 2
    # Alias conceitual: IMAGE_OPTIMIZATION_CONCURRENCY → image_avif_max_concurrency
    image_optimization_concurrency: int | None = None
    image_media_cache_max_age_seconds: int = 86_400
    image_optimization_enabled: bool = True
    image_optimization_sweep_interval_seconds: int = 2
    image_optimization_batch_size: int = 4
    image_optimization_lease_seconds: int = 300
    # --- Persistent offer monitoring (ADR 0030). Clock lives in PostgreSQL. ---
    offer_refresh_interval_hours: int = 12
    offer_monitor_enabled: bool = True
    offer_monitor_sweep_interval_seconds: int = 30
    offer_monitor_batch_size: int = 10
    offer_monitor_lease_seconds: int = 300
    offer_monitor_retry_base_seconds: int = 300
    offer_monitor_retry_max_seconds: int = 3600
    offer_monitor_jitter_seconds: int = 600
    offer_promotion_grace_seconds: int = 60
    offer_monitor_stale_heartbeat_seconds: int = 300
    # --- Exchange-rate subsystem (ADR 0034). HTTP-only; no API keys. ---
    exchange_rate_enabled: bool = True
    exchange_rate_refresh_interval_seconds: int = 1800
    exchange_rate_sweep_interval_seconds: int = 60
    exchange_rate_http_timeout_seconds: float = 20.0
    exchange_rate_stale_after_seconds: int = 21600
    exchange_rate_outlier_max_change_pct: float = 15.0
    exchange_rate_tourism_max_premium_pct: float = 12.0
    exchange_rate_ptax_agreement_pct: float = 0.5
    # Emergency / test override — never use in production for live rates.
    exchange_rate_manual_usd_brl_tourism_sell: str | None = None
    # --- Durable Product Match runs (ADR 0036). ---
    match_run_worker_enabled: bool = True
    match_run_sweep_interval_seconds: float = 2.0
    match_run_batch_size: int = 1
    match_run_lease_seconds: int = 600
    # Heartbeat must be well below lease (default lease/5). Worker-only renewals.
    match_run_heartbeat_interval_seconds: int = 120
    # Periodic reconcile of exhausted stale leases (beyond claim sweep).
    match_run_recovery_interval_seconds: float = 30.0
    match_run_max_attempts: int = 3
    # --- Match hang / wall deadlines (defense-in-depth; 0 = disabled) ---
    # Absolute wall-time per store inside a MatchRun (monotonic). Progress does
    # NOT reset this deadline. Exceeded → store ERROR STORE_WALL_TIMEOUT.
    match_store_wall_timeout_seconds: float = 180.0
    # Absolute wall-time for the whole run since claim/processing start
    # (PENDING queue time does not count). Exceeded → FAILED RUN_WALL_TIMEOUT.
    match_run_wall_timeout_seconds: float = 2700.0
    # Watchdog: no *real* progress for this long → os._exit(78). Independent
    # of lease heartbeat (heartbeat ≠ progress).
    match_run_watchdog_stale_seconds: float = 600.0
    match_run_watchdog_enabled: bool = True
    match_run_watchdog_check_interval_seconds: float = 5.0
    # Test-only: block after claim to exercise hard hang (rejected in production).
    match_run_test_inject_hang: bool = False
    # --- Product Match embeddings (MatchRun worker only; off by default). ---
    match_embeddings_mode: Literal["off", "shadow", "active"] = "off"
    match_embeddings_provider: Literal["openai", "openai_compatible"] = "openai"
    match_embeddings_api: str = "https://api.openai.com/v1/embeddings"
    match_embeddings_api_key: str | None = None
    match_embeddings_model: str = "text-embedding-3-small"
    # Bump when weights change without changing model ID or endpoint.
    match_embeddings_cache_revision: str = "v1"
    match_embeddings_input_prefix: str = ""
    match_embeddings_cache_backend: Literal["local", "redis"] = "redis"
    match_embeddings_dimensions: int = Field(default=1536, ge=1, le=3072)
    match_embeddings_batch_size: int = Field(default=8, ge=1, le=100)
    match_embeddings_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    match_embeddings_max_candidates_per_run: int = Field(default=12, ge=1, le=100)
    match_embeddings_cache_entries: int = Field(default=128, ge=1, le=512)
    match_embeddings_cache_ttl_seconds: int = Field(default=21_600, ge=1, le=604_800)
    match_embeddings_representation: Literal[
        "raw", "normalized", "structured", "hybrid"
    ] = "hybrid"
    match_embeddings_active_similarity_threshold: float | None = Field(
        default=None, ge=0, le=1
    )
    # --- Store + capability circuit breaker (Phase 11) ---
    # Process-local circuit per (store_key, capability).
    # Set false to disable entirely (rollback path).
    store_capability_circuit_enabled: bool = True
    # Consecutive upstream-blocking failures before circuit opens.
    store_capability_circuit_failure_threshold: int = 3
    # Seconds the circuit stays OPEN before transitioning to HALF_OPEN (probe).
    store_capability_circuit_cooldown_seconds: int = 120

    # --- Visão VIP Strategy A (searchProducts Server Action) ---
    # When true, Match tries HTTP searchProducts before browser SERP.
    # Action ID is deploy-coupled: leave visaovip_search_action_id empty to
    # auto-discover from Camoufox-hydrated SERP chunks (process cache).
    visaovip_search_action_enabled: bool = True
    # Optional bootstrap Next-Action id (hex40+). Empty = auto-discover.
    # On 404/UNAVAILABLE the process cache is invalidated and rediscovered.
    visaovip_search_action_id: str = ""

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug(cls, value: object) -> object:
        """Accept the legacy ``DEBUG=release`` value as production mode."""
        if isinstance(value, str) and value.strip().lower() == "release":
            return False
        return value

    @field_validator("shopee_warmup_policy", mode="before")
    @classmethod
    def normalize_shopee_warmup_policy(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"always", "once_per_session", "never"}:
                return normalized
        return value

    @field_validator("match_embeddings_active_similarity_threshold", mode="before")
    @classmethod
    def empty_embedding_threshold_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def reject_insecure_auth_in_production(self) -> Self:
        """Fail closed: never boot production with client auth optional."""
        if self.environment.lower() == "production" and not self.auth_required:
            raise ValueError(
                "AUTH_REQUIRED=false (ou AUTH_ENABLED=false) não é permitido "
                "quando ENVIRONMENT=production. "
                "Defina AUTH_REQUIRED=true ou use um ambiente não-produção."
            )
        if self.image_optimization_concurrency is not None:
            self.image_avif_max_concurrency = max(
                1, int(self.image_optimization_concurrency)
            )
        lease = max(60, int(self.match_run_lease_seconds))
        heartbeat = max(15, int(self.match_run_heartbeat_interval_seconds))
        if heartbeat >= lease:
            raise ValueError(
                "MATCH_RUN_HEARTBEAT_INTERVAL_SECONDS deve ser menor que "
                "MATCH_RUN_LEASE_SECONDS (margem para falhas transitórias)."
            )
        self.match_run_lease_seconds = lease
        self.match_run_heartbeat_interval_seconds = heartbeat
        self.match_run_recovery_interval_seconds = max(
            5.0, float(self.match_run_recovery_interval_seconds)
        )
        # Wall/watchdog: negative → 0 (disabled). Keep floats finite.
        self.match_store_wall_timeout_seconds = max(
            0.0, float(self.match_store_wall_timeout_seconds)
        )
        self.match_run_wall_timeout_seconds = max(
            0.0, float(self.match_run_wall_timeout_seconds)
        )
        self.match_run_watchdog_stale_seconds = max(
            0.0, float(self.match_run_watchdog_stale_seconds)
        )
        self.match_run_watchdog_check_interval_seconds = max(
            0.5, float(self.match_run_watchdog_check_interval_seconds)
        )
        if self.match_run_test_inject_hang and self.environment.lower() == "production":
            raise ValueError(
                "MATCH_RUN_TEST_INJECT_HANG não é permitido quando "
                "ENVIRONMENT=production."
            )
        if self.match_embeddings_mode == "active":
            if self.match_embeddings_provider == "openai" and not (
                self.match_embeddings_api_key and self.match_embeddings_api_key.strip()
            ):
                raise ValueError(
                    "MATCH_EMBEDDINGS_API_KEY é obrigatória para OpenAI no modo active."
                )
            if self.match_embeddings_active_similarity_threshold is None:
                raise ValueError(
                    "MATCH_EMBEDDINGS_ACTIVE_SIMILARITY_THRESHOLD deve ser "
                    "calibrado antes do modo active."
                )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
