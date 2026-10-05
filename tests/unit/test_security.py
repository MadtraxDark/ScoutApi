"""Security tests: auth, authorization, minimization, rate limit, redaction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from scout_api.core.config import get_settings
from scout_api.core.log_redaction import redact_mapping, redact_string
from scout_api.core.rate_limit import RateLimiter, reset_rate_limiter
from scout_api.main import app
from scout_api.modules.auth.jwt_service import AuthError, verify_access_token
from scout_api.modules.auth.schemas import PublicUser, principal_to_public
from scout_api.modules.crawler.router import get_product_scrape_service


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch):
    secret = "test-hs256-secret-for-security-suite-only"
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "120")
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "20")
    monkeypatch.setenv("RATE_LIMIT_CRAWLER_PER_MINUTE", "3")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    reset_rate_limiter()
    yield secret
    get_settings.cache_clear()
    reset_rate_limiter()


def _mint(
    secret: str,
    *,
    sub: str | None = None,
    exp_delta: timedelta = timedelta(minutes=15),
    aud: str = "authenticated",
    alg: str = "HS256",
    role: str | None = None,
    extra: dict | None = None,
) -> str:
    now = datetime.now(UTC)
    user_id = sub or str(uuid4())
    payload = {
        "sub": user_id,
        "aud": aud,
        "exp": now + exp_delta,
        "iat": now,
        "email": "user@example.com",
        "user_metadata": {"full_name": "Alice Example", "phone": "+550000"},
        "app_metadata": (
            {"provider": "google", "role": role} if role else {"provider": "google"}
        ),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm=alg)


def test_health_is_public(auth_settings: str) -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_product_search_requires_auth(auth_settings: str) -> None:
    client = TestClient(app)
    response = client.get(
        "/products/search",
        params={"brand": "Asus", "model": "GeForce RTX 5070"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"
    client = TestClient(app)
    response = client.post(
        "/crawl",
        json={"url": "https://www.kabum.com.br/produto/1"},
    )
    assert response.status_code == 401
    body = response.json()["detail"]
    assert body["code"] == "UNAUTHORIZED"
    assert "password" not in response.text.lower()
    assert "secret" not in response.text.lower()


def test_crawl_rejects_invalid_token(auth_settings: str) -> None:
    client = TestClient(app)
    response = client.post(
        "/crawl",
        headers={"Authorization": "Bearer not-a-jwt"},
        json={"url": "https://www.kabum.com.br/produto/1"},
    )
    assert response.status_code == 401


def test_crawl_rejects_expired_token(auth_settings: str) -> None:
    token = _mint(auth_settings, exp_delta=timedelta(minutes=-5))
    client = TestClient(app)
    response = client.post(
        "/crawl",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "https://www.kabum.com.br/produto/1"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "TOKEN_EXPIRED"


def test_crawl_rejects_alg_none(auth_settings: str) -> None:
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        "sub": str(uuid4()),
        "aud": "authenticated",
        "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
    }
    # PyJWT may refuse encoding alg=none; build manually.
    import base64
    import json

    def b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    token = f"{b64(header)}.{b64(payload)}."
    with pytest.raises(AuthError):
        verify_access_token(token, settings=get_settings())


def test_token_without_aud_still_verifies_hs256(auth_settings: str) -> None:
    """Supabase claim shape can omit aud; signature + sub/exp must still pass."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "exp": now + timedelta(minutes=15),
            "iat": now,
            "role": "authenticated",
        },
        auth_settings,
        algorithm="HS256",
    )
    principal = verify_access_token(token, settings=get_settings())
    assert principal.id is not None


def test_auth_server_fallback_when_local_signature_fails(
    auth_settings: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout_api.modules.auth import jwt_service as jwt_mod

    user_id = uuid4()

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "id": str(user_id),
                "email": "user@example.com",
                "user_metadata": {"full_name": "Fallback User"},
                "app_metadata": {"provider": "google"},
            }

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        jwt_mod.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(),
    )
    # Wrong secret → local HS256 fails → /auth/v1/user fallback.
    bad = jwt.encode(
        {
            "sub": str(user_id),
            "aud": "authenticated",
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        "not-the-configured-secret-value-xxxxxx",
        algorithm="HS256",
    )
    principal = verify_access_token(bad, settings=get_settings())
    assert principal.id == user_id
    assert principal.display_name == "Fallback User"
    get_settings.cache_clear()


def test_valid_token_allows_crawl(auth_settings: str) -> None:
    from decimal import Decimal

    from scout_api.modules.crawler.models.product import ProductPriceItem

    class Fake:
        def scrape(self, url: str, *, include_images: bool = False) -> ProductPriceItem:
            return ProductPriceItem(
                store="kabum",
                country="BR",
                product_id="1",
                sku="1",
                title="ok",
                brand=None,
                model=None,
                seller="kabum",
                url=url,
                canonical_url=url,
                currency="BRL",
                price=Decimal("10.00"),
                pix_price=None,
                available=True,
                availability="available",
                images=[],
            )

    token = _mint(auth_settings)
    app.dependency_overrides[get_product_scrape_service] = Fake
    try:
        response = TestClient(app).post(
            "/crawl",
            headers={"Authorization": f"Bearer {token}"},
            json={"url": "https://www.kabum.com.br/produto/1"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200


def test_auth_me_minimizes_user_fields(auth_settings: str) -> None:
    token = _mint(auth_settings)
    response = TestClient(app).get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"id", "display_name"}
    assert "email" not in payload
    assert "phone" not in payload
    assert "role" not in payload
    assert "app_metadata" not in payload
    assert PublicUser.model_validate(payload)


def test_principal_to_public_never_leaks_email(auth_settings: str) -> None:
    principal = verify_access_token(_mint(auth_settings), settings=get_settings())
    public = principal_to_public(principal)
    dumped = public.model_dump()
    assert "email" not in dumped
    assert principal.email is not None


def test_crawler_rate_limit_returns_429(
    auth_settings: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_CRAWLER_PER_MINUTE", "2")
    get_settings.cache_clear()
    reset_rate_limiter()

    from decimal import Decimal

    from scout_api.modules.crawler.models.product import ProductPriceItem

    class Fake:
        def scrape(self, url: str, *, include_images: bool = False) -> ProductPriceItem:
            return ProductPriceItem(
                store="kabum",
                country="BR",
                product_id="1",
                sku="1",
                title="ok",
                brand=None,
                model=None,
                seller="kabum",
                url=url,
                canonical_url=url,
                currency="BRL",
                price=Decimal("10.00"),
                pix_price=None,
                available=True,
                availability="available",
                images=[],
            )

    token = _mint(auth_settings)
    app.dependency_overrides[get_product_scrape_service] = Fake
    client = TestClient(app)
    try:
        for _ in range(2):
            ok = client.post(
                "/crawl",
                headers={"Authorization": f"Bearer {token}"},
                json={"url": "https://www.kabum.com.br/produto/1"},
            )
            assert ok.status_code == 200
        limited = client.post(
            "/crawl",
            headers={"Authorization": f"Bearer {token}"},
            json={"url": "https://www.kabum.com.br/produto/1"},
        )
    finally:
        app.dependency_overrides.clear()
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After")
    assert limited.json()["detail"]["code"] == "RATE_LIMITED"
    assert limited.json()["detail"]["rate_limit_scope"] == "crawler"
    assert limited.json()["detail"]["rate_limit_policy"] == "token_bucket"
    assert limited.headers["X-RateLimit-Scope"] == "crawler"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert "redis" not in limited.text.lower()


def test_rate_limit_buckets_are_per_identity(auth_settings: str) -> None:
    limiter = RateLimiter(settings=get_settings(), redis_gateway=None)
    a = limiter.check(scope="crawler", identity="u:aaa", limit=1, window_seconds=60)
    b = limiter.check(scope="crawler", identity="u:bbb", limit=1, window_seconds=60)
    a2 = limiter.check(scope="crawler", identity="u:aaa", limit=1, window_seconds=60)
    assert a.allowed and b.allowed
    assert a2.allowed is False


def test_redis_token_bucket_decision_is_used(auth_settings: str) -> None:
    """Redis decisions, including wait times, must not fall back to local state."""

    class _FakeRedis:
        def __init__(self) -> None:
            self.calls = 0

        def eval(self, script: str, numkeys: int, key: str, capacity: int, window: int):
            assert numkeys == 1
            assert key == "rl:tb:poll:u:poller"
            assert capacity == 10 and window == 60
            self.calls += 1
            return [0, 0, 4]

    class _FakeGateway:
        def __init__(self, client: _FakeRedis) -> None:
            self._client = client

        def get_client(self):
            return self._client

    client = _FakeRedis()
    limiter = RateLimiter(settings=get_settings(), redis_gateway=_FakeGateway(client))
    result = limiter.check(scope="poll", identity="u:poller", limit=10)
    assert not result.allowed
    assert result.retry_after == 4
    assert client.calls == 1


def test_refreshing_jwt_does_not_reset_user_quota(auth_settings, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "1")
    get_settings.cache_clear()
    user = str(uuid4())
    client = TestClient(app)
    first = _mint(auth_settings, sub=user, extra={"jti": "first"})
    renewed = _mint(auth_settings, sub=user, extra={"jti": "renewed"})
    assert (
        client.get("/auth/me", headers={"Authorization": f"Bearer {first}"}).status_code
        == 200
    )
    blocked = client.get("/auth/me", headers={"Authorization": f"Bearer {renewed}"})
    assert blocked.status_code == 429
    other = _mint(auth_settings)
    assert (
        client.get("/auth/me", headers={"Authorization": f"Bearer {other}"}).status_code
        == 200
    )


def test_poll_and_default_buckets_are_independent(auth_settings: str) -> None:
    limiter = RateLimiter(settings=get_settings(), redis_gateway=None)
    for _ in range(3):
        assert limiter.check(
            scope="poll", identity="u:same", limit=3, window_seconds=60
        ).allowed
    assert (
        limiter.check(
            scope="poll", identity="u:same", limit=3, window_seconds=60
        ).allowed
        is False
    )
    # Exhausting poll must not block default (or crawler start).
    assert limiter.check(
        scope="default", identity="u:same", limit=1, window_seconds=60
    ).allowed
    assert limiter.check(
        scope="crawler", identity="u:same", limit=1, window_seconds=60
    ).allowed


def test_log_redaction_strips_secrets() -> None:
    text = redact_string("Authorization: Bearer super-secret-token")
    assert "super-secret-token" not in text
    mapping = redact_mapping(
        {
            "password": "hunter2",
            "database_url": "postgresql://user:pass@host/db",
            "ok": "visible",
        }
    )
    assert mapping["password"] == "***"
    assert mapping["ok"] == "visible"
    assert "pass@" not in mapping["database_url"]


def test_cors_does_not_echo_unknown_origin(auth_settings: str) -> None:
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Unknown origins must not receive ACAO=*
    assert response.headers.get("access-control-allow-origin") not in {
        "*",
        "https://evil.example",
    }


def test_admin_permission_gate(
    auth_settings: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from scout_api.modules.auth.deps import require_admin

    admin_id = str(uuid4())
    monkeypatch.setenv("AUTH_ADMIN_USER_IDS", admin_id)
    get_settings.cache_clear()

    user = verify_access_token(_mint(auth_settings), settings=get_settings())
    admin = verify_access_token(
        _mint(auth_settings, sub=admin_id), settings=get_settings()
    )
    with pytest.raises(HTTPException) as denied:
        require_admin(user)
    assert denied.value.status_code == 403
    assert require_admin(admin).id == UUID(admin_id)


@pytest.fixture
def auth_optional_settings(monkeypatch: pytest.MonkeyPatch):
    secret = "test-hs256-secret-for-security-suite-only"
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_CRAWLER_PER_MINUTE", "2")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    get_settings.cache_clear()
    reset_rate_limiter()
    yield secret
    get_settings.cache_clear()
    reset_rate_limiter()


def test_auth_optional_allows_crawl_without_token(
    auth_optional_settings: str,
) -> None:
    from decimal import Decimal

    from scout_api.modules.auth.deps import DEV_BYPASS_USER_ID
    from scout_api.modules.crawler.models.product import ProductPriceItem

    class Fake:
        def scrape(self, url: str, *, include_images: bool = False) -> ProductPriceItem:
            return ProductPriceItem(
                store="kabum",
                country="BR",
                product_id="1",
                sku="1",
                title="ok",
                brand=None,
                model=None,
                seller="kabum",
                url=url,
                canonical_url=url,
                currency="BRL",
                price=Decimal("10.00"),
                pix_price=None,
                available=True,
                availability="available",
                images=[],
            )

    app.dependency_overrides[get_product_scrape_service] = Fake
    try:
        response = TestClient(app).post(
            "/crawl",
            json={"url": "https://www.kabum.com.br/produto/1"},
        )
        me = TestClient(app).get("/auth/me")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert me.status_code == 200
    assert me.json()["id"] == str(DEV_BYPASS_USER_ID)
    assert "password" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "service_role" not in response.text.lower()


def test_auth_optional_still_rejects_invalid_token(
    auth_optional_settings: str,
) -> None:
    response = TestClient(app).post(
        "/crawl",
        headers={"Authorization": "Bearer not-a-jwt"},
        json={"url": "https://www.kabum.com.br/produto/1"},
    )
    assert response.status_code == 401


def test_auth_optional_keeps_rate_limiting(
    auth_optional_settings: str,
) -> None:
    from decimal import Decimal

    from scout_api.modules.crawler.models.product import ProductPriceItem

    class Fake:
        def scrape(self, url: str, *, include_images: bool = False) -> ProductPriceItem:
            return ProductPriceItem(
                store="kabum",
                country="BR",
                product_id="1",
                sku="1",
                title="ok",
                brand=None,
                model=None,
                seller="kabum",
                url=url,
                canonical_url=url,
                currency="BRL",
                price=Decimal("10.00"),
                pix_price=None,
                available=True,
                availability="available",
                images=[],
            )

    app.dependency_overrides[get_product_scrape_service] = Fake
    client = TestClient(app)
    try:
        for _ in range(2):
            ok = client.post(
                "/crawl",
                json={"url": "https://www.kabum.com.br/produto/1"},
            )
            assert ok.status_code == 200
        limited = client.post(
            "/crawl",
            json={"url": "https://www.kabum.com.br/produto/1"},
        )
    finally:
        app.dependency_overrides.clear()
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "RATE_LIMITED"


def test_auth_optional_rejected_in_production_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import ValidationError

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    with pytest.raises((ValidationError, ValueError)):
        get_settings()
    get_settings.cache_clear()


def test_auth_enabled_alias_maps_to_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    assert get_settings().auth_required is False
    get_settings.cache_clear()


def test_media_content_accepts_access_cookie_without_bearer(
    auth_settings: str,
) -> None:
    """``<img src>`` cannot send Bearer — cookie HttpOnly must authenticate media."""
    from unittest.mock import MagicMock

    from scout_api.modules.auth.deps import ACCESS_COOKIE
    from scout_api.modules.images.router import get_image_service

    token = _mint(auth_settings)
    product_id = uuid4()
    image_id = uuid4()

    mock_svc = MagicMock()
    mock_svc.get_content.return_value = (b"\xff\xd8\xff", "image/jpeg", '"etag-1"')

    app.dependency_overrides[get_image_service] = lambda: mock_svc
    client = TestClient(app)
    try:
        denied = client.get(f"/products/{product_id}/images/{image_id}/content")
        assert denied.status_code == 401

        ok = client.get(
            f"/products/{product_id}/images/{image_id}/content",
            cookies={ACCESS_COOKIE: token},
        )
        assert ok.status_code == 200
        assert ok.headers["content-type"].startswith("image/jpeg")
        assert ok.headers.get("content-disposition", "").startswith("inline")
        mock_svc.get_content.assert_called()

        # Cookie must NOT authenticate mutating/JSON catalog APIs.
        json_api = client.get("/products", cookies={ACCESS_COOKIE: token})
        assert json_api.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_logout_clears_access_cookie(auth_settings: str) -> None:
    from scout_api.modules.auth.deps import ACCESS_COOKIE

    client = TestClient(app)
    response = client.post("/auth/logout")
    assert response.status_code == 204
    set_cookie = ",".join(response.headers.get_list("set-cookie"))
    assert ACCESS_COOKIE in set_cookie
