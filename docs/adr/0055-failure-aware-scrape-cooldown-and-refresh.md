# ADR 0055: Cooldown ciente de falha e refresh que preserva dados válidos

- Status: Accepted
- Data: 2026-11-08
- Complementa ADR 0030, 0047 e 0053.

## Contexto

`ScrapeGuard` reservava URL (300s) e domínio (15s) antes do fetch e nunca
liberava a reserva em falha. Uma falha de infraestrutura (Camoufox sem permissão
no diretório de perfil do container) ou um bloqueio da loja virava, no retry,
`DUPLICATE_REQUEST`/`RATE_LIMITED` com até 300s, e o frontend travava
`POST /crawl` pelo mesmo tempo ("A API pediu uma pausa"). O monitor processava
lotes sequenciais por loja, gerando rajadas de `RATE_LIMITED` gravadas como
`scrape_failed`; todo `ParseError` removia a listing permanentemente; e o
evento `scrape_failed` (`after` sem seller) era lido como novo estado vazio
("Vendedor: X → Não informado").

## Problema / decisão necessária

Diferenciar causas de falha, não punir o usuário por falha nossa, isolar a
loja problemática e nunca apresentar falha como dado novo.

## Alternativas consideradas

- Reduzir `SCRAPE_URL_COOLDOWN_SECONDS`: não resolve o bloqueio real da loja.
- Circuit breaker do Scrapy (`core/circuit_breaker.py`): não participa do `/crawl`.
- Escrever o estado anterior em `after` do evento: mascara falha como observação.

## Decisão

- `classify_failure` (`core/exceptions.py`) define `kind`: `local_throttle`,
  `blocked`, `timeout`, `infra`, `extraction`, `gone`, `upstream_error`. Respostas
  de erro de `/crawl`, `/crawl/offer` e rotas de match incluem `kind`.
- `ScrapeGuard.live_fetch` libera a reserva de URL após falha; falha de infra
  também libera o domínio; falha da loja (bloqueio/timeout/erro) abre pausa por
  loja com backoff exponencial (`SCRAPE_STORE_FAILURE_COOLDOWN_*`, Redis
  compartilhado). Outras lojas não são afetadas; sucesso zera a pausa.
- Refresh: `local_throttle`/`infra` adiam a listing (`deferred`) sem falha nem
  evento; bloqueio/timeout gravam `scrape_failed` com `observed=false` e backoff
  que respeita `retry_after`; `ParseError` só remove a listing se for
  `ListingGoneError` (soft-404) ou após
  `OFFER_MONITOR_EXTRACTION_FAILURES_BEFORE_REMOVAL` falhas consecutivas.
- O monitor intercala o lote por loja e adia sem custo listings de loja em pausa.
- Atividade do catálogo expõe `observed`/`error_kind`; falha mantém os últimos
  valores (seller, disponibilidade) em vez de campos vazios.
- Docker: o entrypoint garante que o usuário `app` escreva em
  `camoufox-profiles/{default,direct}` (chown não recursivo, fallback chmod).

## Consequências positivas

- Retry imediato após falha de infra funciona; mensagens indicam a causa.
- Uma loja bloqueada não consome o lote nem atrasa as demais.
- Snapshots e histórico nunca são sobrescritos por falhas.

## Consequências negativas / riscos

- Pausa por loja pode adiar tentativas legítimas por até 300s após bloqueio.
- Remoção de listing por erro de extração exige N falhas, atrasando remoções reais.
