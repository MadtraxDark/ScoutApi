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
| other-store match run | job + poll | `POST /products/{id}/match-runs` + `GET /match-runs/{id}/live` | DIRECT_MAPPING | job persistente (ADR 0036) e ofertas progressivas (ADR 0049) |
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
3. Frontend: descobrir Run via `GET /products/{id}/match-runs/active` e
   consultar `GET /match-runs/{id}/live` para ofertas/progresso/banner/botão
   (rate scope `poll`, bucket separado do CRUD — ver
   [`docs/security/api-auth.md`](../security/api-auth.md))
4. Ao terminal (`completed`/`failed`): atualizar ofertas via `GET /products/{id}`
   sem sobrescrever campos em edição,
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

## Importação com referências pendentes (ADR 0048)

O frontend envia imagens aprovadas em `POST /products.images` (`source_url`,
`position`, `is_main`) e conclui após a resposta de persistência. Não encadeia
uploads individuais, não espera AVIF e não aumenta timeouts.
Detalhes e estados: [product-images.md](../persistence/product-images.md).
No detalhe, polling visível da galeria a cada 3 segundos só enquanto há estados
pendentes; atualiza mídia sem sobrescrever os campos de edição do usuário.
Falha posterior de imagem não transforma produto salvo em importação falha.

A importação respeita o limite atual de 20 imagens por produto: preview com mais
URLs envia as primeiras 20 URLs únicas e informa explicitamente a quantidade
excedente na mensagem de conclusão. Não aumenta o limite de persistência.

## Resultados progressivos de Match (ADR 0049)

Após descobrir a Run, consultar `GET /match-runs/{run_id}/live` a cada quatro
segundos, somente com página visível. O endpoint conhecido substitui active
+ status em cada ciclo. Em idle, descobrir novas Runs a cada 60 segundos;
ao abrir o produto sem Run ativa, recuperar a última pelo histórico.
Lease expirada preserva observações e mostra recuperação pendente.
`is_effectively_active` decide bloqueio do botão e timer.

Resposta `MatchRunLiveView`:

- `run`: contrato leve `MatchRunStatusView` existente.
- `is_effectively_active`: considera lease, não apenas status.
- `auto_matches_found`: outcomes `match` com decisão `auto_match`.
- `stores`: roster com `pending|running|match|no_match|error`, início/fim,
  decisão explícita, identidade comercial, listing final, título, URL,
  preço, moeda, confiança, erro classificado e conversão BRL opcional.

Não expõe candidates, reasons, queries, staging ou referência.
`matched_store`, `matched_country`, `matched_product_id` e
`matched_canonical_url` identificam o produto comercial; `store` continua
sendo a capability operacional (`amazon_br` versus `amazon`). FX usa cotação
existente, sem atualização externa no polling. BRL faz uma query de dados;
moeda estrangeira adiciona no máximo uma leitura das cotações.

Autenticação, permissão `match` e visibility/ownership obrigatórias.
Run inexistente/inacessível retorna `404 RUN_NOT_FOUND`.
Resposta `Cache-Control: private, no-store`; rate scope `poll`, com `429`
e backoff canônico do client. `401/403` e falhas do banco seguem o envelope
de erro existente da API.

Somente `auto_match` vira oferta parcial. Reviews não participam da oferta
confirmada ou do menor preço. Merge por listing, `(store,country,product_id)`
ou URL canônica evita duplicação; preço canônico existente é mantido durante
a Run. Sem cotação, moeda estrangeira fica ao fim da ordenação comparável.

Ao terminal, reter o snapshot e atualizar apenas `variants` do produto e do
rascunho, com até três tentativas. Não recarregar galeria ou campos editáveis.
Cards não reconciliados mantêm identificação explícita; falha/cancelamento
mostra observação não confirmada no catálogo. Navegação cancela leitura,
nunca a Run. Respostas antigas não podem substituir o snapshot atual.

### Migration e rollout

Aplicar Alembic `0032_match_live` antes do backend/worker e do frontend.
As três colunas nullable preservam histórico. Drenar Runs antigas antes de
trocar worker: hit legado sem staging não é recuperável integralmente
(`MATCH_RECOVERY_INCOMPLETE`). Não inferir `auto_match` para backfill.
Manter `CAMOUFOX_BROWSER_CAPACITY=1`. Docker continua a execução oficial.

Rollback: voltar frontend ao polling anterior, drenar/voltar worker e API,
manter colunas aditivas. Downgrade apaga staging e exige backup e ausência
de workers novos. Staging não substitui catálogo/snapshots/identificadores.

### Validação reproduzível

Testes: `tests/unit/test_match_run_live.py`,
`tests/unit/test_match_run_progressive.py` e
`tests/integration/test_match_run_progressive_postgres.py`. PostgreSQL exige
`TEST_DATABASE_URL` exclusivamente em `scout_progressive_test`, com schema
via Alembic; nunca apontar para banco do aplicativo.

Harnesses: `tests/e2e/progressive_match_browser.py` (Chrome, API local 8011,
PriceScout local 3011) e `tests/e2e/progressive_match_load.py` (1/10/50
observadores autenticados em grupos simultâneos, quatro segundos, 600s por
padrão). Fixtures controladas, sem coleta em lojas; evidências em
`.tmp/progressive-browser/`. JWT de teste somente no servidor isolado.

Validação Docker: `docker compose config --quiet` e build de `api`/
`match-runner` aprovados; a imagem importou o OpenAPI live e conferiu a
migration no PostgreSQL isolado. Rollout local concluído em 2026-10-05: nove Runs
concluídas, workers parados durante a troca, migration 0032_match_live antes
da API/worker novos, API saudável e os três workers reiniciados. C1 permanece 1.
[Evidências atuais](../performance/pending-recovery-2026-10-05.md).
