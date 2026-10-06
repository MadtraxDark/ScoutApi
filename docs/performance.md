# Performance & observabilidade de tempo — ScoutApiV2

**PROCESSO LONGO NÃO PODE SER INVISÍVEL.**

Documento canônico de budgets, eventos e fluxo de investigação.

## Preview Magalu e recuperação de pendências — 2026-10-05

Budget aprovado: 15 s frio / 1 s cache. Três acessos finais: 13,77 s, 7,17 s,
7,47 s; cache abaixo de 1 ms; oferta e 25 imagens preservadas, sem proxy.
`FetchCostMetrics.stage_timings_ms` decompõe aquisição, página, warmup,
navegação e settle/challenge, inclusive em erro. C1 permanece 1.
O budget genérico de browser não foi alterado.
[Causas, amostras, checks e limites](performance/pending-recovery-2026-10-05.md).

## Medição local de ofertas progressivas — 2026-10-05

ADR 0049. API FastAPI em Windows, Python 3.12, PostgreSQL 16 em Docker local,
um processo API, pool padrão, nove lojas e quatro matches (incluindo USD).
Fixtures e cleanup exclusivamente em `scout_progressive_test`; sem busca em
lojas durante a carga. Grupos de observadores executados simultaneamente por
603,95s, intervalo 4s, identidades JWT distintas e sem desativar rate limiting.

| Observadores no grupo | Requests | P50 | P95 | Máximo | HTTP |
|---|---:|---:|---:|---:|---|
| 1 | 151 | 23,97 ms | 34,63 ms | 65,96 ms | 200 |
| 10 | 1.510 | 19,11 ms | 31,70 ms | 115,25 ms | 200 |
| 50 | 7.550 | 13,59 ms | 21,65 ms | 123,81 ms | 200 |

Total: 9.211 respostas; payload máximo 5.765 bytes. Snapshot BRL usa uma
query de dados; com moeda estrangeira usa duas, sem candidates ou staging.
Os grupos têm fases de polling diferentes e compartilham carga; P95 menor
do grupo 50 não significa ganho de capacidade. Não é benchmark de produção.

Amostragem do processo API por 200,76s durante a carga: working set
147,46–147,53 MiB, início/fim 147,46 MiB; 22,09s de CPU, equivalentes a 11%
de um core. Não houve crescimento material na janela amostrada.

Primeiro harness com 61 clients separados/catch-up de intervalos excedeu
timeout. A repetição usou um pool HTTP compartilhado e períodos espaçados,
sem acúmulo artificial de polls atrasados. O resultado aprovado acima é dessa
repetição; não se atribuiu o timeout inicial à API sem evidência.

Chrome + API/PostgreSQL reais: commit de outcome até primeiro card 4,42s
(um ciclo de 4s mais rede/renderização). Validou segunda oferta progressiva,
review separado, F5, navegação, falha preservando resultado, handoff e draft.
Smoke real delimitado, C1, acesso direto sem proxy pago: 8,17s; Pichau
`auto_match` com listing vinculado (6.357ms), KaBuM `no_match` (1.503ms).
Não há comparação antes/depois suficiente para afirmar aceleração do crawler;
nenhuma regressão significativa de duração foi observada nesta validação.

Harnesses e reprodução: [integração PriceScout](integration/pricescout.md).
Evidências locais: `.tmp/progressive-browser/{report,load-report,resource-report,smoke-report}.json`.
Regra operacional do agente: [`.cursor/rules/performance.mdc`](../.cursor/rules/performance.mdc).
Decisão: [ADR 0028](adr/0028-performance-observability.md).

## Princípio

Sempre que uma operação relevante ultrapassar o tempo anormal para seu tipo,
registre duração e contexto. Isso vale para:

- runtime da aplicação (API, crawler, Product Search/Match, DB, browser, rede);
- testes e CI;
- scripts, shell, Docker, migrations;
- trabalho observável de agentes (Cursor/Codex/automações) — não o raciocínio interno.

Perguntas que devemos conseguir responder:

1. o que demorou?
2. quanto demorou?
3. em qual etapa?
4. por que demorou?
5. quantas operações foram executadas?
6. houve retries/timeouts?
7. houve trabalho repetido?
8. há oportunidade clara de otimização?

## Severidade

| Nível | Significado |
|---|---|
| `NORMAL` | Dentro do esperado |
| `WARN` | Acima do habitual |
| `SLOW` | Claramente acima do esperado |
| `CRITICAL` | Extremamente lento ou potencialmente travado |

Classificação em `scout_api.core.performance` (`BUDGETS` + `classify_severity`).
**Não** espalhe números mágicos no código — altere só o mapa central.

## Budgets por categoria (ms)

Valores em `BUDGETS` (`src/scout_api/core/performance.py`):

| Categoria | expected | WARN | SLOW | CRITICAL |
|---|---:|---:|---:|---:|
| `unit_test` | 200 | 500 | 2_000 | 10_000 |
| `integration_test` | 1_000 | 2_000 | 10_000 | 60_000 |
| `live_test` | 15_000 | 30_000 | 120_000 | 600_000 |
| `e2e` | 30_000 | 60_000 | 300_000 | 900_000 |
| `http_request` | 1_500 | 3_000 | 15_000 | 60_000 |
| `browser_navigation` | 5_000 | 10_000 | 45_000 | 120_000 |
| `browser_launch` | 3_000 | 5_000 | 20_000 | 60_000 |
| `crawler` | 8_000 | 15_000 | 60_000 | 180_000 |
| `product_search` | 2_000 | 5_000 | 30_000 | 120_000 |
| `product_match` | 15_000 | 30_000 | 180_000 | 600_000 |
| `database_query` | 50 | 200 | 1_000 | 5_000 |
| `migration` | 2_000 | 5_000 | 30_000 | 120_000 |
| `docker_build` | 45_000 | 60_000 | 300_000 | 900_000 |
| `ci_job` | 180_000 | 300_000 | 900_000 | 1_800_000 |
| `agent_shell` | 15_000 | 30_000 | 120_000 | 600_000 |
| `agent_research` | 60_000 | 120_000 | 600_000 | 1_200_000 |
| `external_tool` | 5_000 | 15_000 | 60_000 | 300_000 |

Live tests **podem** ser mais lentos; ainda assim devem registrar store, duração,
requests, retries, browser e resultado — para distinguir “é live” de “está mal
implementado”.

Unit test de vários segundos **não** é normal: investigar rede, browser, sleep,
retry, DB externo ou fixture pesada.

## Evento `slow_operation`

Emitido via `observe()` / `timed()` / `RetryLedger.observe()` quando severidade
≥ `WARN` (ou `force_event` para retries/duplicatas).

Campos típicos (adaptáveis):

- `event`, `operation`, `category`, `stage`
- `duration_ms`, `expected_ms`, `severity`
- `context` (store, retries, attempt_timings, nodeid, …)

Secrets/tokens/cookies/credentials **nunca** entram no contexto (redaction em
`log_redaction` + `as_log_dict`).

## Instrumentação runtime (já ligada)

| Área | Evidência |
|---|---|
| Product Match | `match_store_timing`, `match_total_timing`, `match_timing_summary`, `match_store_waves` / `match_store_wave_parallel`, `observe(product_match*)`, caches request-scoped (`search_cache_entries` / `scrape_cache_entries`), `MATCH_STORE_CONCURRENCY` |
| Product Search / scrape no match | `observe(product_search\|product_scrape)` |
| Browser fetch | `fetch_cost_metrics` + `observe(browser_fetch)` + `browser_reused` + contadores de launch/circuit |
| Browser launch / reuse | `observe(browser_launch)` / `browser_reuse`; falhas também registram duração, tipo e categoria sanitizada (`process_exit`, `launch_timeout`, `profile_or_lock`, `missing_binary`, `launch_error`); `CAMOUFOX_LAUNCH_TIMEOUT_MS` (default 45 s) ≠ `CAMOUFOX_TIMEOUT_MS` (nav); circuit de processo em `browser_health` (ADR 0037) |
| HTTP curl_cffi | `RetryLedger` + `curl_cffi_retry*` com `attempt_timings` |
| Scrapy retry | `retry_scheduled` + `observe(scrapy_retry)` |
| DB | listener SQLAlchemy de query lenta (`attach_slow_query_listener`) |
| Duplicatas no match | `duplicate_work` se mesma URL scrapeada mais de uma vez |

## Testes

```bash
make test-performance   # suite rápida + --durations=25 + resumo budget-aware
python -m pytest --durations=50
```

O hook em `tests/conftest.py`:

- observa cada teste call contra o budget da categoria (markers `live` /
  `integration` / `slow` / `e2e`);
- imprime seção **ScoutApiV2 slow tests** no terminal summary.

Preferir feedback rápido: teste direcionado → módulo → integration → suite →
live/e2e só quando necessário (`docs/testing.md`).

## Retries

Não colapsar N tentativas em um único “request = 16s”. Use `RetryLedger`:

- attempt N → duration + outcome + code
- backoff_ms por tentativa
- `backoff_ms_total` e `attempt_timings` no log

## Trabalho duplicado

`DuplicateWorkTracker` no Product Match (URLs scrapeadas). Agentes também devem
evitar: mesma pesquisa externa, mesmo teste longo, mesmo browser restart sem
necessidade.

## Regressão

Se before/after piorar significativamente (ex. 25s → 1m45s), isso é regressão
mesmo com testes verdes. Reportar no relatório final; otimizar ou abrir
pendência `PERFORMANCE`.

Baselines resumidos: [`performance/baselines.md`](performance/baselines.md).

Falhas estruturais de Camoufox (launch/circuit) **não** devem somar N×180 s
no Product Match: fail-fast com `BROWSER_*` + circuit (ADR 0037). Budget de
`browser_launch` CRITICAL=60 s; `CAMOUFOX_LAUNCH_TIMEOUT_MS` default 45 s.
O `ProfileLock` acompanha a sessão persistente entre fetches, renova sua lease
e fecha a sessão após inatividade limitada a 10 s, liberando o perfil antes do
timeout padrão de espera; ver ADR 0044.

Product Match full-store (13 lojas, `…023048Z`) é uma baseline histórica; a
Shopee consumiu ~25–34 min antes de `AUTH_REQUIRED`. Atualmente está desativada
no Product Match por decisão de escopo (registro em
[`PENDING-016 arquivada`](pending/resolved/PENDING-016-shopee-match-wall-time.md)).

## Pendência de performance

Tipo `PERFORMANCE` em `docs/pending/` (ver README/template). Só para lentidão
**recorrente** ou crítica não resolvida na tarefa — não para ruído isolado.

Campos mínimos: operação, duração observada/esperada, frequência, impacto,
causa, evidências, comandos/arquivos, investigação, soluções, done condition.

## O que não fazer

- gerar warning para toda operação NORMAL;
- observabilidade tão pesada que degrade o sistema;
- esconder lentidão com skip / timeout maior “para passar”;
- remover testes importantes;
- registrar secrets;
- aceitar 10 minutos como “normal” sem investigação.

- [Investigação e medições da importação de imagens (2026-10-05)](performance/import-images-2026-10-05.md).
