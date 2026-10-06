# Architecture Decision Records

ADRs registram decisões arquiteturais relevantes, seu contexto e seus trade-offs.
Use numeração sequencial (`0001-...`) e o template em [`template.md`](template.md).

Uma mudança que contradiga um ADR **Accepted** deve ser registrada em um **novo**
ADR (supersede). Não reescreva silenciosamente a decisão anterior.

Índice geral do projeto: [`docs/README.md`](../README.md).
Política de quando criar/atualizar docs:
[`.cursor/rules/documentation-governance.mdc`](../../.cursor/rules/documentation-governance.mdc).

## Índice — plataforma

| ADR | Título | Status |
|---|---|---|
| [0001](0001-use-modular-monolith.md) | Modular monolith | Accepted |
| [0002](0002-use-src-layout.md) | `src/` layout | Accepted |
| [0003](0003-organize-code-by-feature.md) | Package by feature | Accepted |
| [0004](0004-separate-tests-from-source.md) | Tests fora de `src` | Accepted |
| [0005](0005-dependency-direction.md) | Dependency direction | Accepted |
| [0006](0006-containerize-api-with-docker.md) | Docker oficial | Accepted |
| [0007](0007-environment-configuration.md) | Env / pydantic-settings | Accepted |
| [0021](0021-supabase-postgres-sqlalchemy.md) | PostgreSQL no Supabase via SQLAlchemy (sem Data API) | Accepted |
| [0023](0023-api-auth-supabase-deny-by-default.md) | Auth API Supabase JWT + deny-by-default + rate limit | Accepted |
| [0028](0028-performance-observability.md) | Observabilidade de tempo + budgets por categoria | Accepted |
| [0029](0029-google-drive-product-images.md) | Google Drive (conta dedicada OAuth) + imagens de produto | Accepted |
| [0030](0030-persistent-offer-monitoring.md) | Monitoramento persistente de ofertas (PostgreSQL-driven) | Accepted |
| [0031](0031-durable-image-optimization-queue.md) | Fila durável PostgreSQL para otimização AVIF | Accepted |
| [0034](0034-secondary-exchange-rates.md) | Câmbio secundário sem API comercial (providers web + PostgreSQL) | Accepted |
| [0035](0035-media-access-cookie.md) | Cookie HttpOnly para auth de mídia (`<img>`) | Accepted |
| [0036](0036-persistent-product-match-runs.md) | Product Match persistente + polling (sem SSE) | Accepted |
| [0037](0037-camoufox-launch-health-match-fail-fast.md) | Camoufox launch health + Match fail-fast de infra | Accepted |
| [0038](0038-pdp-search-capability-split.md) | Separar Store Search de Product Page Scraping | Accepted |
| [0041](0041-store-admin-metadata.md) | Metadados administrativos de lojas registradas | Accepted |

## Índice — crawler

| ADR | Título | Status |
|---|---|---|
| [0008](0008-price-crawler-architecture.md) | Arquitetura modular do crawler | Accepted |
| [0009](0009-camoufox-html-fetch.md) | Camoufox como fetcher HTML | Accepted |
| [0010](0010-camoufox-cloudflare-strategy.md) | Cloudflare + ScrapeGuard | Accepted |
| [0011](0011-offer-vs-product-details.md) | Offer vs Product Details | Accepted |
| [0012](0012-optional-image-extraction.md) | Extração opcional de imagens | Accepted |
| [0013](0013-bestbuy-availability-semantics.md) | Best Buy: preço US vs shipping | Accepted |
| [0014](0014-cost-aware-proxy-routing.md) | Proxy Cost Mode / Shopee minimal fetch | Accepted |
| [0015](0015-amazon-multi-marketplace.md) | Amazon BR/US shared core + adapters | Accepted |
| [0016](0016-amazon-http-first-fetch.md) | Amazon HTTP-first progressive fetch | Accepted |
| [0017](0017-captcha-challenge-resolution.md) | Resolução obrigatória de challenge/CAPTCHA | Accepted |
| [0018](0018-auth-wall-bypass.md) | Auth bypass obrigatório (login/session wall) | Accepted |
| [0019](0019-product-matching.md) | Product Matching cross-store + histórico de ofertas | Accepted |
| [0020](0020-distributed-scrape-cache-redis.md) | Cache/single-flight/cooldown distribuídos via Redis | Accepted |
| [0024](0024-product-match-evidence-cascade.md) | Cascata de evidências do Product Match (MPN + bloqueadores) | Accepted |
| [0025](0025-mercadolivre-curl-cffi-http-first.md) | Mercado Livre HTTP-first com curl_cffi + Camoufox | Accepted |
| [0026](0026-product-identity-brand-model-variant.md) | Identidade canônica brand / model / variant + busca opcional | Accepted |
| [0027](0027-category-profile-product-identity.md) | CategoryProfile registry para identidade e filtros | Accepted |
| [0030](0030-persistent-offer-monitoring.md) | Monitoramento persistente + promo expiry | Accepted |
| [0032](0032-product-match-http-first-warm-camoufox.md) | Product Match HTTP-first + Camoufox warm reuse | Accepted |
| [0033](0033-product-match-progressive-search-identity.md) | Progressive search a partir de ProductIdentity (não título bruto) | Accepted |
| [0037](0037-camoufox-launch-health-match-fail-fast.md) | Launch timeout + circuit de browser + ERROR≠NO_MATCH | Accepted |
| [0038](0038-pdp-search-capability-split.md) | Separar Store Search de Product Page Scraping | Accepted |
| [0039](0039-bounded-browser-scheduler-capacity-c1.md) | BrowserScheduler + bounded queue + ProfileLock + claim_trial — capacity C1 | Accepted |
| [0040](0040-matching-multilingual-attributes.md) | Canonicalização multilíngue de atributos no Product Match | Accepted |
| [0041](0041-store-admin-metadata.md) | Metadados administrativos de lojas registradas | Accepted |
| [0042](0042-store-logo-media-processing.md) | Upload e otimização assíncrona de logos | Accepted |
| [0043](0043-product-match-semantic-embedding-evidence.md) | Evidência semântica experimental no Product Match | Accepted |
| [0044](0044-camoufox-profile-lock-warm-session-lifecycle.md) | ProfileLock acompanha a sessão Camoufox persistente | Accepted |
| [0045](0045-self-hosted-product-match-embeddings-provider.md) | Provider self-hosted para embeddings do Product Match | Accepted |
| [0046](0046-product-match-embedding-cache-redis-l2.md) | Cache L2 de embeddings do Product Match em Redis | Accepted |
| [0047](0047-api-token-bucket-scoped-cooldown.md) | Reposição contínua e cooldown por escopo na API | Accepted |
| [0048](0048-import-image-references-before-original-preservation.md) | Referências antes da preservação original na importação | Accepted |
| [0049](0049-progressive-match-live-observations.md) | Ofertas progressivas com observações duráveis de Match Runs | Accepted |
| [0050](0050-search-locale-and-offer-condition.md) | Search locale por integração + condição comercial ≠ identidade | Accepted |


## Quando criar ADR

Crie ADR se a decisão afeta arquitetura, boundaries, invariantes duradouros,
trade-offs relevantes, múltiplos módulos, ou alternativas importantes rejeitadas.

Não crie ADR para selector quebrado, typo ou refactor local sem mudança semântica.
Comportamento operacional por loja → `docs/crawler/stores/` (não ADR, salvo trade-off arquitetural).
