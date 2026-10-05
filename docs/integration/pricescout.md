# Integração PriceScout ↔ ScoutApiV2

Documento canônico da matriz de integração. Fonte de verdade da API:
OpenAPI (`/openapi.json`) + schemas Pydantic + routers.

Frontend: repositório PriceScout (Next.js). Backend: este repositório.

## Arquitetura

```text
PriceScout (localhost:3000)
  → utils/api (client + Bearer + refresh single-flight)
  → ScoutApiV2 (localhost:8000)
  → Services → PostgreSQL / Crawler / Camoufox
```

## Matriz de operações

| Frontend operation | Endpoint legado FE | ScoutApiV2 | Status | Action |
|---|---|---|---|---|
| health / config | — | `GET /health` | DIRECT_MAPPING | usar health |
| Google login start | `GET /api/v1/auth/google/login` | `GET /auth/google` | FRONTEND_ADAPTER | `authorization_url` |
| OAuth callback | `GET /api/v1/auth/google/callback` | `GET /auth/callback` | FRONTEND_ADAPTER | fragment `#access_token` |
| auth me | `GET /api/v1/auth/me` | `GET /auth/me` | FRONTEND_ADAPTER | `id`+`display_name`; UI autenticada (sem role) |
| auth refresh | `POST /api/v1/auth/refresh` | `POST /auth/refresh` | FRONTEND_ADAPTER | cookie + Bearer |
| auth logout | `POST /api/v1/auth/logout` | `POST /auth/logout` | DIRECT_MAPPING | limpar access em memória |
| email/password/magic | `/api/v1/auth/login|signup|…` | — | OBSOLETE_FRONTEND_BEHAVIOR | remover |
| catalog overview | `GET /api/v1/catalog/overview` | — | FRONTEND_ADAPTER | derivar de `GET /products` |
| list products | `GET /api/v1/catalog/products` | `GET /products` | BACKEND_ENDPOINT_REQUIRED | listagem paginada |
| product search | filtros FE | `GET /products/search` | DIRECT_MAPPING | integrar filtros reais |
| product detail | `GET /api/v1/catalog/products/{id}` | `GET /products/{id}` | FRONTEND_ADAPTER | mapper `ProductView` |
| product update | `PATCH …/products/{id}` | `PATCH /products/{id}` | BACKEND_ENDPOINT_REQUIRED | schema explícito |
| product delete | `DELETE …/products/{id}` | `DELETE /products/{id}` | BACKEND_ENDPOINT_REQUIRED | ownership + cascade |
| preview | `POST …/preview` | `POST /crawl` | FRONTEND_ADAPTER | mapper preview |
| get/discard preview | preview TTL | — | OBSOLETE_FRONTEND_BEHAVIOR | estado local FE |
| import | `POST …/import` | `POST /products` | FRONTEND_ADAPTER | `ProductRegisterRequest` |
| other-store prices | sync legado | `POST /match` | DIRECT_MAPPING | tooling / sync |
| other-store match run | job + poll | `POST /products/{id}/match-runs` + `GET /match-runs/{id}` | DIRECT_MAPPING | job persistente (ADR 0036); sem SSE |
| other-store active | — | `GET /products/{id}/match-runs/active` | DIRECT_MAPPING | banner / botão |
| other-store history/detail | — | `GET …/match-runs` + `GET /match-runs/{id}/details` | DIRECT_MAPPING | log consultável |
| notifications | — | `GET/POST /notifications*` | DIRECT_MAPPING | central persistente |
| other-store refresh | (opcional) | `POST /offers/refresh` | DIRECT_MAPPING | refresh de listings existentes |
| offers refresh | — | `POST /offers/refresh` | DIRECT_MAPPING | integrar |
| list stores | `GET …/catalog/stores` | `GET /stores` | DIRECT_MAPPING | registry `STORE_CONFIGS` + `display_name` + `match_enabled` |
| create/update store | POST/PATCH stores | — | OBSOLETE_FRONTEND_BEHAVIOR | somente leitura |
| store markets | `GET …/store-markets` | derivado de `/stores` | FRONTEND_ADAPTER | countries do registry |
| images CRUD | Drive/gallery APIs | `GET/POST/PATCH/DELETE /products/{id}/images` + `/content` | DIRECT_MAPPING | galeria pós-aprovação; `display_url` |
| crawl offer | — | `POST /crawl/offer` | DIRECT_MAPPING | opcional FE |

## Decisões

### Access token

Mantido **somente em memória** no módulo `utils/api/auth.ts`.
Refresh permanece HttpOnly (`scout_refresh_token`, path `/auth`).
Em reload da página: `POST /auth/refresh` (credentials) → novo access em memória.
Não usar `localStorage` para access token.

### `/auth/me` e admin UI

`PublicUser` continua mínimo (`id`, `display_name`) — ADR 0023.
Gate `/admin` exige **usuário autenticado**, não `role=admin`.
Autorização real = permissões JWT no backend.

### Preview

Sem cache persistido no backend nesta integração. Preview = resultado de
`POST /crawl` mantido no estado do cliente até import ou descarte.

O frontend expõe o checkbox **Buscar imagens do produto** depois que a URL
casa com uma loja do `GET /stores`. O default vem de
`default_include_images` / `image_fetch_cost` do registry (não de `if store ==`
no FE). Envia `include_images` real no body.

### Imagens

Crawl/preview (`POST /crawl` com `include_images=true`) devolve
`image_candidates` (URLs externas) — **não** persiste no Drive.
Falha de galeria não derruba o produto: metadata `image_status` /
`image_error` / `image_pipeline` permite UX de sucesso parcial.

Após revisão no PriceScout, o import (`POST /products`) envia
`images: [{ source_url, position, is_main }]`. Só então o ScoutApiV2 baixa,
valida (SSRF), grava o **original** no Drive e responde sucesso. AVIF roda
em background (`optimized_status=pending` é estado válido).

Galeria persistida: usar `display_url` ou, na listagem, `primary_image_url`
(ambos já aplicam AVIF-if-ready, senão original). Resolver paths relativos
com o helper central do FE (`apiUrl` / `resolveMediaUrl`). O browser carrega
bytes via cookie HttpOnly de mídia (ADR 0035) — **não** espere Bearer em
`<img>`. Não usar URL da loja depois da aprovação. Credenciais Drive nunca
no frontend.

Canônico: [`docs/persistence/product-images.md`](../persistence/product-images.md)
+ [ADR 0029](../adr/0029-google-drive-product-images.md).

### Checklist frontend (PriceScout)

1. Após URL reconhecida: mostrar checkbox; default = `default_include_images`
   da loja.
2. Preview: `POST /crawl` com `include_images` conforme checkbox; UI usa
   `image_candidates` (ou `images[]` URLs) como candidatas externas.
3. Se `image_status` for `error`/`empty`/`omitted` com produto OK: avisar
   “Produto encontrado, mas não foi possível carregar a galeria.”
4. Permitir selecionar / remover / reordenar / marcar principal **antes** do
   import; deixar claro que ainda não estão no Drive.
5. Import: `POST /products` com `images: [{ source_url, position, is_main }]`
   das aprovadas (idempotente no clique).
6. Após cadastro: listar via `GET /products/{id}/images` ou campo `images` /
   `primary_image_url` do `ProductView`; renderizar `display_url` /
   `primary_image_url` (path relativo → `resolveMediaUrl`). **Não espere
   AVIF** — original já é válida enquanto `optimized_status` for
   `pending`/`processing`/`failed`. Auth de bytes: cookie de mídia (ADR 0035).
7. CRUD: `POST/PATCH/DELETE /products/{id}/images`; retry AVIF opcional.
8. Não enviar `drive_file_id` / paths / credentials no body.
9. Reutilizar `ImageViewer` / `ImageWithState` / `utils/api/product-images.ts`.
10. Listagem: **não** ignorar `primary_image_url` do `ProductView`; **não**
    usar `source_url` da loja como capa.

### Progresso Match (job persistente — ADR 0036)

SSE (`POST /match/stream`) foi **removido**. O fluxo do botão
**Buscar preços em outras lojas** é:

1. `POST /products/{id}/match-runs` → **202** + `id` / `status` / `started_at`
   (se já houver Run **efetivamente** ativa, devolve a existente com
   `already_active=true`)
2. Product Match roda em background (worker com lease PostgreSQL + heartbeat)
3. Frontend: polling leve em `GET /match-runs/{id}` +
   `GET /products/{id}/match-runs/active` para banner/botão
   (rate scope `poll`, bucket separado do CRUD — ver
   [`docs/security/api-auth.md`](../security/api-auth.md))
4. Ao terminal (`completed`/`failed`): refetch `GET /products/{id}`,
   toast efêmero, notificação persistente, log em
   `GET /match-runs/{id}/details`

Regras:

- A busca **não** pertence à página React; sair/reload não cancela a Run.
- No máximo uma Run `pending|running` por produto (índice único parcial).
- **Active ≠ status sozinho:** `GET …/active` só retorna Run se `pending` ou
  (`running` **e** lease válida). Após power-loss / Docker kill, lease expira →
  **204** (sem banner de horas); worker reclaim (skip stores já terminais) ou
  `POST start` / sweeper marca `failed` + `failure_code=worker_lost`.
- Timer UX usa `active_since` (`claimed_at` da attempt, senão `started_at`) —
  downtime offline não conta como processamento.
- Toast `worker_lost`: “Busca anterior foi interrompida.” (uma vez por Run).
- Labels de timeout (nunca snake_case cru na UI):
  - `STORE_WALL_TIMEOUT` → “A busca nesta loja excedeu o tempo limite.”
  - `RUN_WALL_TIMEOUT` → “A execução excedeu o tempo máximo.”
- Hang watchdog do `match-runner` (exit 78 + restart): ver
  [`docs/matching/README.md`](../matching/README.md) e ADR 0036.
- `reference_url` é resolvida no backend a partir das listings do produto,
  priorizando lojas com scrape de PDP mais confiável (ex.: Kabum/Amazon/Magalu
  antes de Shopping China). Se o scrape da referência falhar, o worker faz
  fallback para identidade canônica (`title`/`brand`/`model`) e segue o Match.
- Com `compose.yaml`, o serviço `match-runner` é o worker canônico; o scheduler
  in-process da API fica desligado por padrão (`API_MATCH_RUN_WORKER_ENABLED=false`).
- A loja de referência **não** entra na descoberta (“outras lojas”).
- `SEARCH_UNSUPPORTED` → store status `error` (nunca `no_match`).
- Labels: `store_display_name` / `GET /stores.display_name` — não renderizar slug.
  Ownership dos display labels de enums (`auto_match`, status de run, tipos de
  notificação, etc.) é do **frontend** (`PriceScout/utils/display/`); a API
  permanece language-neutral. Nunca renderizar snake_case/IDs técnicos na UI.
- Toast ≠ Notification Center ≠ banner ≠ log detalhado.
- Proibido `alert()` / `confirm()` / `prompt()` nesse fluxo (PriceScout).

## CORS

Origens explícitas (`CORS_ALLOWED_ORIGINS`), `credentials=true`, métodos
`GET, POST, PATCH, DELETE, OPTIONS`, headers `Authorization, Content-Type, Accept`.

### Estado visual de imagens (contrato de disponibilidade)

Consumir `primary_image_status` / `image_status`, `*_error_code` e
`*_retryable`; não inferir a causa só pelo `null` da URL. Distinguir produto sem
imagem (`missing`), processamento, referência inválida e falha no storage.
Após `onError`, substituir `<img>` quebrada por um estado visual acessível.
Para uma causa conhecida, usar `detail.image_status` e `detail.retryable` da
resposta JSON do proxy. Exibir retry manual apenas quando recuperável e
remover o estado de erro após sucesso. `image_warning_code=conversion_failed`
indica que a original continua disponível; não exibir exceções internas.
