# Product Match — Store Search vs PDP

Product Match **compõe** duas capabilities independentes (ADR 0038):

1. **Store Search** (`matching/search_adapters`) — descobre `SearchCandidate`
2. **Product Scraping** (`crawler/spiders` + `ProductScrapeService`) — interpreta PDP

```text
ProductMatchService
  → StoreSearchService → StoreSearchAdapter → SearchCandidate[]
  → ProductScrapeService → BaseStoreSpider (PDP)
  → ProductIdentity → ProductMatcher
```

## Adicionar loja

| Objetivo | O que implementar |
|---|---|
| Só crawl/oferta | Spider PDP em `crawler/spiders/` + `StoreConfig` |
| Participar do Match | **Também** `StoreSearchAdapter` em `matching/search_adapters/` |

Não implemente SERP dentro do spider PDP. Não implemente `extract_offer` no Search adapter.

## Eligibility

```text
eligible = match_enabled ∩ registered_search_store_keys()
```

Fonte de Search: `matching/search_adapters/registry.py` — **não** `supports_search` no spider.

## Contratos

- `SearchRequest` — URL (+ `prefer_browser`)
- `StoreSearchAdapter` — `build_search_request` / `parse_candidates` / `classify_empty_result`
- `SearchCandidate` — `matching/search_candidate.py`

## Geração de queries

`ProductIdentity` gera queries progressivas a partir de identificadores e frases
comerciais reconhecidas. Quando não há frase específica para a categoria, um
modelo estruturado alfanumérico (letras e números) também é preservado como
chave de descoberta; a validação posterior continua sob responsabilidade do
matcher e dos gates de variante.

Para CPU, a escada começa também com consultas de modelo em forma legível (por
exemplo, `amd ryzen 7 5800x3d` e `ryzen 7 5800x3d`) porque SERPs de lojas podem
não indexar o modelo concatenado `ryzen75800x3d`. O SKU e seus sufixos são
mantidos integralmente. As queries genéricas de OPN/título continuam como
fallback dentro do mesmo budget.

Consultas com atributos localizáveis usam o `query_locale` configurado na
integração da loja (`StoreConfig`). Por padrão, a metadata deriva `pt-BR`,
`en-US` ou `es-PY` do país; lojas podem sobrescrever esse locale explicitamente.
Cada loja recebe sua própria ladder: por exemplo, cor `Rosa` mantém uma query
`rosa` para pt-BR e prioriza `pink` para en-US. Identificadores, códigos de
modelo e frases comerciais não são traduzidos. Os aliases de cor restantes
continuam disponíveis como fallback sem multiplicar a quantidade de queries;
a ladder preserva seu dedup e o budget existente por loja.

## Budgets por loja (StoreAttemptBudget)

Cada loja dentro de um `MatchRun` opera sob três budgets independentes
(módulo `matching/attempt_budget.py`):

| Budget | Padrão env var | Propósito |
|---|---|---|
| `queries_budget` | `MATCH_SEARCH_QUERY_BUDGET=5` | Queries progressivas máximas por loja |
| `external_attempt_budget` | `MATCH_EXTERNAL_ATTEMPT_BUDGET=12` | Requests upstream (SERP + PDPs) |
| `browser_navigation_budget` | `MATCH_BROWSER_NAVIGATION_BUDGET=8` | Navegações Camoufox reais |

Regras:
- Strategy A + B na mesma query = 1 `begin_query()`; cada request upstream = 1 `record_external()`.
- Cache/dedup hits: `skip_cached()` — não consomem budget.
- Budget esgotado: store encerra progressão; Run continua nas outras stores.
- `stopped_reason` preserva o primeiro motivo de parada por loja (observabilidade de log).

## Diagnóstico do Product Match

Os logs da API e do `match-runner` imprimem os campos necessários no texto da
mensagem, além dos extras estruturados:

- `store_search_fetch`: loja, query, método, preferência por browser e URL SERP.
- `store_search_candidates`: query, quantidade e candidatos limitados com ID,
  título e URL sem query string.
- `match_serp_title_reject`: query, título e motivo da rejeição antes do PDP.
- `match_candidate_decision`: query, título/URL PDP, modelo, socket, MPN,
  decisão, confiança e razões do matcher.
- `match_store_summary`: queries usadas, tempos de busca/scrape, contagens,
  resultado e motivo de parada por loja.

Esse conjunto distingue ausência de descoberta, rejeição de título, falha de
scraping e decisão do matcher sem registrar query strings das URLs de PDP.

## Concorrência de browser (BrowserScheduler)

O `BrowserScheduler` limita o número de slots Camoufox simultâneos:

- `CAMOUFOX_BROWSER_CAPACITY=1` em produção (decisão C1 — ADR 0039).
- Fila saturada → `RequestError(code="BROWSER_QUEUE_SATURATED")` em vez de hang.
- `ProfileLock` (Redis primary, fcntl fallback) previne dois processos abrindo
  o mesmo diretório de perfil simultaneamente. A lease Redis permanece retida
  enquanto a sessão Camoufox warm usa o perfil, com renovação periódica; a
  sessão fecha após até 10 s ociosa (limitado pelo timeout de aquisição e TTL).
  Ao fechar ou falhar a retenção, o perfil é liberado/fechado com segurança.
- `BrowserCircuitBreaker.claim_trial()`: token atômico no estado HALF_OPEN
  garante singleflight (sem thundering herd de launch).
- Failure domains: circuit por store key; falha de infra (launch) ≠ `NO_MATCH`.

O slot local de execução volta à fila ao fim de cada fetch, mas a lease global
do perfil acompanha a sessão persistente até seu fechamento. A configuração
C1 continua limitando a um proprietário Camoufox por perfil. Ver ADR 0037
(launch health + fail-fast), ADR 0039 (bounded scheduler C1) e ADR 0044
(lifecycle da lease da sessão warm).

## Evidência semântica experimental (ADR 0043)

Embeddings são opt-in no `match-runner` (`MATCH_EMBEDDINGS_MODE=off` por padrão).
Shadow mode consulta apenas quando `MatchingEngine` retorna `review` por
`variant_semantic_uncertain`, depois dos gates determinísticos. O resultado
resume modelo, similaridade, latência, cache hit e tokens reportados pelo
provider em `MatchRun`; nenhum vetor é
persistido. A falha de provider mantém a decisão tradicional. O cache é limitado
e local ao processo, sem Redis nem vector search.

O modo `active` exige API key e limiar explícito; além disso, só pode promover
`review` com `brand_model_exact` e `variant_semantic_uncertain`, sem preço
extremo ou outra razão de conflito. Não habilitar até o benchmark rotulado
validar precisão, falso positivo e limiar. A habilitação também envia os títulos
e atributos dos casos elegíveis ao provider configurado; consulte a política de
retenção aplicável antes de ativar.

Implementação: [`embedding_evidence.py`](../../src/scout_api/modules/matching/embedding_evidence.py),
provider em [`embedding_provider.py`](../../src/scout_api/modules/matching/embedding_provider.py),
fixture/runner e [baseline smoke](embedding-acceptance-baseline.md).

## Hang defense-in-depth (match-runner)

Uma MatchRun travada **não** pode bloquear o único worker indefinidamente.

| Camada | Setting | Default | Efeito |
|---|---|---|---|
| Store wall | `MATCH_STORE_WALL_TIMEOUT_SECONDS` | 180 | Deadline absoluto por loja (monotonic); no Product Match também limita a espera na fila C1. Estouro → store `error` `STORE_WALL_TIMEOUT` (nunca `NO_MATCH`). Run continua. |
| Run wall | `MATCH_RUN_WALL_TIMEOUT_SECONDS` | 2700 | Desde claim/processamento (PENDING não conta). Estouro → run `failed` `RUN_WALL_TIMEOUT`. |
| Watchdog | `MATCH_RUN_WATCHDOG_STALE_SECONDS` | 600 | Sem **progresso real** → `os._exit(78)` + Docker restart + reclaim ADR 0036. |
| Flag | `MATCH_RUN_WATCHDOG_ENABLED` | true | Rollback operacional. |
| `0` nos timeouts numéricos | — | desliga aquela camada. |

**Heartbeat ≠ progresso.** Lease heartbeat renova ownership; `ProgressTracker.mark_progress`
só em eventos observáveis (store start, search, candidates, scrape, match/no_match/error).

Exit code documentado: `MATCH_WORKER_HANG_EXIT_CODE = 78`.

Labels UI (PriceScout): mapear códigos para pt-BR — nunca exibir snake_case cru
(`STORE_WALL_TIMEOUT` → “A busca nesta loja excedeu o tempo limite.”;
`RUN_WALL_TIMEOUT` → “A execução excedeu o tempo máximo.”;
`worker_lost` → “Busca anterior foi interrompida.”).

Ver emenda em ADR 0036.
