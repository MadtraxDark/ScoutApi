# Segurança da API — autenticação, autorização e rate limiting

Documento canônico do fluxo de segurança HTTP do ScoutApiV2.
Invariantes curtos: [`AGENTS.md`](../../AGENTS.md) e
[`.cursor/rules/security.mdc`](../../.cursor/rules/security.mdc).
Decisão: [ADR 0023](../adr/0023-api-auth-supabase-deny-by-default.md).

## Arquitetura

```text
Frontend
  → Google OAuth (Supabase Auth, PKCE iniciado em GET /auth/google)
  → access_token (Bearer)
  → ScoutApiV2 valida JWT (JWKS / HS256 teste; fallback `GET /auth/v1/user`
    se a verificação local falhar com token ainda aceito pelo Auth)
  → AuthenticatedPrincipal (sub, role)
  → require_permission / ownership
  → serviço de domínio
```

Refresh token permanece em cookie HttpOnly (`scout_refresh_token`, path `/auth`).
Access token de mídia: cookie HttpOnly `scout_access_token` (path `/`),
definido em login/refresh e consumido **somente** por
`GET /products/{id}/images/{id}/content` — ver ADR 0035. A `service_role`
**nunca** é usada pelo frontend nem retornada pela API.

## Allowlist pública

| Rota | Motivo |
|---|---|
| `GET /health` | Liveness/readiness sem dados sensíveis |
| `GET /auth/google` | Início OAuth |
| `GET /auth/callback` | Callback OAuth/PKCE |
| `POST /auth/refresh` | Renovação via cookie |
| `POST /auth/logout` | Limpa cookies de sessão |

Tudo o mais exige `Authorization: Bearer` (incluindo `GET /auth/me`) quando
`AUTH_REQUIRED=true` (padrão).

### `AUTH_REQUIRED`

| Valor | Comportamento |
|---|---|
| `true` (default) | Endpoints protegidos exigem Bearer válido; 401 se ausente/inválido |
| `false` | Modo local/dev/teste: sem Bearer usa principal fixo `dev-bypass` (`USER`, UUID estável). Bearer presente ainda é validado. Rate limiting, validação e secrets permanecem ativos |

**Produção:** `ENVIRONMENT=production` + `AUTH_REQUIRED=false` (ou alias
`AUTH_ENABLED=false`) **falha no startup** — a API não sobe pública por
acidente de `.env`.

Ownership continua derivando apenas do principal autenticado (ou do principal
de bypass em dev) — nunca de `user_id` no body.

Em `ENVIRONMENT=production`: `/docs`, `/redoc` e `/openapi.json` ficam
desabilitados.

## Endpoints autenticados

| Rota | Permissão | Rate scope |
|---|---|---|
| `GET /auth/me` | autenticado | default |
| `POST /crawl` | `crawl` | crawler |
| `POST /crawl/offer` | `crawl` | crawler |
| `POST /match` | `match` | crawler |
| `POST /products/{id}/match-runs` | `match` | crawler |
| `GET /products/{id}/match-runs` | `match` | default |
| `GET /products/{id}/match-runs/active` | `match` | poll |
| `GET /match-runs/{id}` | `match` | poll |
| `GET /match-runs/{id}/details` | `match` | default |
| `GET /notifications` | autenticado | poll |
| `GET /notifications/unread-count` | autenticado | poll |
| `POST /notifications/{id}/read` | autenticado | default |
| `POST /notifications/read-all` | autenticado | default |
| `POST /offers/refresh` | `offers:refresh` | crawler |
| `POST /products` | `products:write` | default |
| `GET /products` | `products:read` | default |
| `GET /products/search` | `products:read` | default |
| `GET /products/{id}` | `products:read` | default |
| `PATCH /products/{id}` | `products:write` | default |
| `DELETE /products/{id}` | `products:write` | default |
| `GET /products/{id}/images` | `products:read` | default |
| `POST /products/{id}/images` | `products:write` | default |
| `PATCH /products/{id}/images` | `products:write` | default |
| `DELETE /products/{id}/images/{image_id}` | `products:write` | default |
| `POST /products/{id}/images/{image_id}/retry-optimization` | `products:write` | default |
| `GET /products/{id}/images/{image_id}/content` | `products:read` (Bearer **ou** cookie de mídia) | default |
| `GET /stores` | `products:read` | default |
| `GET /exchange-rates` | `products:read` | default |
| `GET /exchange-rates/diagnostics` | admin | default |
| `POST /exchange-rates/refresh` | admin | default |

Roles: `user` (permissões de negócio) e `admin` (`AUTH_ADMIN_USER_IDS` ou
`app_metadata.role=admin`).

Integração frontend: [`docs/integration/pricescout.md`](../integration/pricescout.md).

## Ownership (BOLA)

- `canonical_products.owner_user_id` = JWT `sub` na criação via `POST /products`.
- Leitura/refresh negada (404) se o produto tem dono diferente e o caller não é admin.
- Linhas legadas/`NULL` (matching system) continuam legíveis a usuários autenticados.
- O body **não** aceita `user_id` / `owner_user_id` (`extra=forbid`).

## Minimização

`PublicUser`: apenas `id`, `display_name`.
E-mail, telefone, metadata do provider e roles **não** saem em `/auth/me`.

## Rate limiting

Config (`core/config.py`):

- `RATE_LIMIT_ENABLED`
- `RATE_LIMIT_DEFAULT_PER_MINUTE` (CRUD / leituras on-demand)
- `RATE_LIMIT_AUTH_PER_MINUTE`
- `RATE_LIMIT_CRAWLER_PER_MINUTE` (POST caros: crawl/match/refresh)
- `RATE_LIMIT_POLL_PER_MINUTE` (polling leve SPA: status de Match Run,
  notificações)

Buckets são **mutuamente exclusivos por rota**: um POST `crawler` não consome
o bucket `default`; polling contínuo não esgota o `default` nem bloqueia o
início de novos crawls.

Backend: Redis quando `REDIS_URL` está definido; senão memória do processo.
Token bucket atômico (Lua), conforme [ADR 0047](../adr/0047-api-token-bucket-scoped-cooldown.md).
Cada `*_PER_MINUTE` define capacidade inicial e taxa de reposição por minuto;
não é uma contagem estrita em qualquer janela móvel de 60 segundos. Não há
aumento das capacidades configuradas. A reposição é contínua: com capacidade
10, um saldo esgotado recupera uma requisição em seis segundos. Rejeições não
consomem saldo nem adiam sua reposição. `Retry-After` arredonda para cima o
tempo até o próximo token; requisições concorrentes podem consumi-lo antes.

Redis usa seu próprio relógio (`TIME`) e chaves `rl:tb:*`, sem reutilizar as
strings da política anterior. TTL remove estado após um minuto ocioso, quando
o saldo já estaria cheio. O fallback usa relógio monotônico e lock por processo,
com limpeza periódica de entradas expiradas. Falha do Redis emite
`rate_limit_local_fallback` no máximo uma vez por minuto por limiter; o fallback
não garante uma cota compartilhada entre réplicas. Autorização permanece intacta.

Identidade: hash do `sub` validado (usuário Bearer), estável após renovação do
JWT; IP quando não há Bearer e nas rotas públicas de auth. A validação Bearer
é reutilizada somente dentro do mesmo request. `X-Forwarded-For`
só se o peer estiver em `TRUSTED_PROXY_IPS`.

Resposta ao exceder: **429** + `Retry-After`, `X-RateLimit-Scope`,
`X-RateLimit-Limit` e `X-RateLimit-Remaining: 0`. O corpo preserva
`code=RATE_LIMITED`, `retryable` e `retry_after`, acrescentando
`rate_limit_scope` (`default`, `auth`, `crawler`, `poll`) e
`rate_limit_policy=token_bucket`. Logs `api_rate_limited` registram método,
template da rota, escopo e espera; nunca identidade ou token.

O cliente PriceScout coordena a pausa entre rotas do escopo atingido, inclusive
refresh de autenticação, sem repetir mutações. Rotas conhecidas são classificadas
pelo contrato; outras aprendem pelo header. Sem política explícita, a pausa
permanece local por rota. A coordenação é por sessão e aba; não há coordenação
entre abas. Dados confirmados não devem desaparecer durante a pausa.

Throttling por domínio de marketplace (ScrapeGuard) é **ortogonal** a este
controle. Seus erros 429 preservam códigos existentes (`RATE_LIMITED` ou
`DUPLICATE_REQUEST`) e recebem `rate_limit_policy=upstream_cooldown`, sem
`rate_limit_scope` global. Não alteramos cooldowns, fila ou capacidade de browser.

## CORS

`CORS_ALLOWED_ORIGINS` = lista CSV explícita. Wildcard `*` é ignorado/rejeitado
com credentials.

Métodos: `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS`.
Headers: `Authorization`, `Content-Type`, `Accept`.
Expostos: `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
`X-RateLimit-Scope`.

## Logs e erros

- `core/log_redaction.py` — Authorization, tokens, URLs com senha, etc.
- Handlers em `core/http_errors.py` — sem stack/SQL/secrets na resposta.

## Variáveis

Ver `.env.example` (valores vazios; nunca secrets reais).

Relevantes para auth:

- `AUTH_REQUIRED` (default `true`; alias legado `AUTH_ENABLED`)
- `ENVIRONMENT` (produção rejeita `AUTH_REQUIRED=false` no startup)
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_*`
- `AUTH_ADMIN_USER_IDS`, `CORS_ALLOWED_ORIGINS`, `TRUSTED_PROXY_IPS`
- `RATE_LIMIT_*`

## Testes

`tests/unit/test_security.py` — 401/403/429, `alg=none`, minimização, redaction,
CORS, admin gate, `AUTH_REQUIRED=true/false`, rejeição em production.
