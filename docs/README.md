# Documentation index — ScoutApiV2

Mapa rápido para reconstruir contexto sem depender de histórico de chat.

## Precedência

1. Requisito explícito da tarefa
2. [`AGENTS.md`](../AGENTS.md)
3. ADRs **Accepted** em [`adr/`](adr/)
4. [`.cursor/rules/`](../.cursor/rules/)
5. Código atual
6. Skills ([`skills/README.md`](skills/README.md))
7. Genéricos

Governança de documentação: [`.cursor/rules/documentation-governance.mdc`](../.cursor/rules/documentation-governance.mdc).

## Onde ler o quê

| Tópico | Documento canônico |
|---|---|
| Invariantes do agente | [`AGENTS.md`](../AGENTS.md) |
| Decisões arquiteturais | [`adr/README.md`](adr/README.md) |
| Crawler — contratos e erros | [`crawler/contracts.md`](crawler/contracts.md) |
| Identidade brand / model / variant | [`crawler/product-identity.md`](crawler/product-identity.md) + [ADR 0026](adr/0026-product-identity-brand-model-variant.md) + [ADR 0027](adr/0027-category-profile-product-identity.md) |
| Crawler — lojas | [`crawler/stores/`](crawler/stores/) |
| Product Match / Store Search | [`matching/README.md`](matching/README.md) + [investigação de embeddings](matching/embeddings-architecture-investigation.md) + [ADR 0038](adr/0038-pdp-search-capability-split.md) + [ADR 0043](adr/0043-product-match-semantic-embedding-evidence.md) |
| Proxy Cost Mode | [`.cursor/rules/proxy-cost-mode.mdc`](../.cursor/rules/proxy-cost-mode.mdc) + [ADR 0014](adr/0014-cost-aware-proxy-routing.md) |
| Challenge/CAPTCHA (obrigatório resolver) | [`.cursor/rules/captcha-challenge-resolution.mdc`](../.cursor/rules/captcha-challenge-resolution.mdc) + [ADR 0017](adr/0017-captcha-challenge-resolution.md) |
| Auth bypass / login wall (obrigatório) | [`.cursor/rules/auth-wall-resolution.mdc`](../.cursor/rules/auth-wall-resolution.mdc) + [ADR 0018](adr/0018-auth-wall-bypass.md) |
| Pesquisa antes de bloquear / paid | [`.cursor/rules/research-and-problem-solving.mdc`](../.cursor/rules/research-and-problem-solving.mdc) |
| Falha do sandbox de execução no Windows | [`.cursor/rules/windows-sandbox-exec.mdc`](../.cursor/rules/windows-sandbox-exec.mdc) |
| Fetch / Camoufox imutáveis | [`.cursor/rules/scraper-camoufox-immutable.mdc`](../.cursor/rules/scraper-camoufox-immutable.mdc) |
| Segurança da API (auth/CORS/rate limit) | [`security/api-auth.md`](security/api-auth.md) + [ADR 0023](adr/0023-api-auth-supabase-deny-by-default.md) + [`.cursor/rules/security.mdc`](../.cursor/rules/security.mdc) |
| Atividade do catálogo | [`matching/catalog-activity.md`](matching/catalog-activity.md) + [ADR 0051](adr/0051-persistent-catalog-activity-read-model.md) |
| Integração PriceScout | [`integration/pricescout.md`](integration/pricescout.md) |
| Persistência PostgreSQL / Supabase | [`persistence/supabase-postgres.md`](persistence/supabase-postgres.md) + [ADR 0021](adr/0021-supabase-postgres-sqlalchemy.md) |
| Imagens de produto (Drive + AVIF) | [`persistence/product-images.md`](persistence/product-images.md) + [ADR 0029](adr/0029-google-drive-product-images.md) + [ADR 0031](adr/0031-durable-image-optimization-queue.md) + [ADR 0035](adr/0035-media-access-cookie.md) |
| Monitoramento periódico de ofertas | [`monitoring.md`](monitoring.md) + [ADR 0030](adr/0030-persistent-offer-monitoring.md) + [`crawler/promotions.md`](crawler/promotions.md) |
| Câmbio (conversão FX → BRL) | [`exchange/README.md`](exchange/README.md) + [`exchange/research.md`](exchange/research.md) + [ADR 0034](adr/0034-secondary-exchange-rates.md) |
| Testes (comandos, markers, budgets) | [`testing.md`](testing.md) |
| Performance / tempos / slow ops | [`performance.md`](performance.md) + [ADR 0028](adr/0028-performance-observability.md) + [`.cursor/rules/performance.mdc`](../.cursor/rules/performance.mdc) |
| Baselines de performance | [`performance/baselines.md`](performance/baselines.md) |

## Crawler — playbooks por loja

| Loja | Doc |
|---|---|
| KaBuM | [`crawler/stores/kabum.md`](crawler/stores/kabum.md) |
| Magazine Luiza | [`crawler/stores/magazineluiza.md`](crawler/stores/magazineluiza.md) |
| Mercado Livre | [`crawler/stores/mercadolivre.md`](crawler/stores/mercadolivre.md) |
| Pichau | [`crawler/stores/pichau.md`](crawler/stores/pichau.md) |
| TerabyteShop | [`crawler/stores/terabyteshop.md`](crawler/stores/terabyteshop.md) |
| Shopee | [`crawler/stores/shopee.md`](crawler/stores/shopee.md) |
| AliExpress | [`crawler/stores/aliexpress.md`](crawler/stores/aliexpress.md) |
| Best Buy | [`crawler/stores/bestbuy.md`](crawler/stores/bestbuy.md) |
| Nissei | [`crawler/stores/nissei.md`](crawler/stores/nissei.md) |
| Shopping China | [`crawler/stores/shoppingchina.md`](crawler/stores/shoppingchina.md) |
| Visão VIP | [`crawler/stores/visaovip.md`](crawler/stores/visaovip.md) |
| Amazon (BR/US) | [`crawler/stores/amazon.md`](crawler/stores/amazon.md) |

- [Investigação e medições da importação de imagens (2026-10-05)](performance/import-images-2026-10-05.md).
- [Plano de ofertas progressivas em Product Match (2026-10-05)](../tasks/plan.md) — investigação inicial e fases; contrato implementado no ADR 0049.
