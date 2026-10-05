# PENDING-016 — Shopee domina wall-time do Product Match full-store

- Status: RESOLVED
- Tipo: PERFORMANCE
- Prioridade: P1
- Área: crawler/shopee / matching
- Origem: 2026-09-22 — pós PENDING-015 (bench full 13-store `…023048Z`)
- Atualizado: 2026-09-25

## Encerramento de escopo (2026-09-25)

O usuário decidiu manter Shopee desativada no Product Match e remover suas
pendências da lista ativa. Este arquivamento **não** corrige o wall-time nem
representa validação de auth; `match_enabled=false` permanece deliberado.
Reabrir somente com novo pedido para reativação/investigação dessa loja.

## Contexto

Com waves + concurrency + scrape budget + challenge fail-fast, o match
full 13-store **completa sem hang**, mas a Shopee sozinha consome ~25–34 min
do wall-time (cold ≈1472s / warm ≈2044s) e ainda retorna `AUTH_REQUIRED`.
Sem Shopee, o restante das lojas caberia em poucos minutos.

## Performance

- Operação: Product Match store=`shopee` (search; scrape=0)
- Duração observada: cold **1472s**, warm **2044s** (`match_store_timing`)
- Duração esperada (budget `product_match_store`): 15s
- Frequência: 1/1 no bench full 13 (`BENCH_SKIP_CROSS=1`, cooldown forçado off)
- Impacto: wall full-store cold **1726s** / warm **2225s**; warm pior que cold
  por causa da Shopee
- Causa conhecida/provável: ciclos longos de challenge (`camoufox_waiting_challenge`
  + fail-fast + proxy fallback) antes de emitir `AUTH_REQUIRED`; SERP fria
  sem sessão autenticada (ver `docs/crawler/stores/shopee.md`)
- Evidências: `data/live-match-reports/match_perf_bench_20260922T023048Z.json`;
  logs `fetch_cost_metrics store=shopee … result=blocked duration_ms≈200–235s`
  repetidos; ERROR final `AUTH_REQUIRED`
- Comandos: `docker compose run … BENCH_SKIP_CROSS=1 MATCH_STORE_CONCURRENCY=3
  api python scripts/_bench_match_perf.py`
- Arquivos: `html_fetcher.py`, `challenge_resolution`, `store_aware_fetcher`,
  playbook Shopee
- Investigação realizada: fail-fast de challenge já ativo; auth bypass tentado
  (`shopee_auth_fields_missing` / `challenge_resolved` intermitente); proxy
  fallback também bloqueia (~210s)
- Possíveis soluções: encurtar settle/retry após fail-fast sem enfraquecer
  resolução obrigatória; garantir sessão seeded (`make seed-shopee-login`) no
  bench; HTTP/`search_items` intercept quando sessão válida; orçamento de
  wall por loja após esgotar resolução
- Done condition: store Shopee no match full ≤ ~60–90s wall **ou** match
  bem-sucedido com candidatos; full 13 wall tipicamente &lt; ~10 min sem
  sacrificar cobertura/contrato ERROR≠NO_MATCH

## Feito

- Full 13-store completa (PENDING-015)
- Challenge fail-fast evita hang infinito
- Contrato: Shopee → `ERROR AUTH_REQUIRED` (não NO_MATCH falso)
- **2026-09-22:** Match automático exclui Shopee via `match_enabled=false`
  (PENDING-017). Esta pendência vale para quando a loja for reativada.

## Falta

- Reduzir wall-time da Shopee no caminho challenge → AUTH_REQUIRED
- Validar com sessão seeded / auth env no bench live

## Por que não terminou

- Escopo PENDING-015 era hang + concurrency; otimização específica Shopee
  ficou como follow-up após evidência do bench full

## Impacto

- Product Match com Shopee na lista fica ~30 min; UX operacional ruim
- Warm reuse não ajuda Shopee neste cenário (warm pior que cold)

## Done when

- [ ] Wall Shopee no match full ≪ 1472s (meta ≤90s) **ou** MATCH/candidatos
      com sessão válida
- [ ] Bench full 13 cold tipicamente &lt; ~10 min (ou justificado)
- [ ] Docs `shopee.md` / baselines atualizados com o after
