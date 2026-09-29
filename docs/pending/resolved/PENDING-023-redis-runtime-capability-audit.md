# PENDING-023 — Auditoria runtime de capacidade Redis (RESOLVED)

- Status: RESOLVED
- Tipo: RESEARCH
- Prioridade: P2
- Área: infrastructure/redis
- Resolvido: 2026-09-24

## Resultado verificado

Inspecionado o Redis do Compose local por Docker, sem alterar configuração nem
dados:

- `redis:7-alpine`, versão efetiva **7.4.11**, digest
  `redis@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`.
- Modo standalone; `MODULE LIST` vazio, sem Query Engine/RediSearch declarado
  ou carregado.
- `maxmemory=268435456` (256 MiB), `maxmemory-policy=allkeys-lru`,
  `appendonly=no`, RDB `save=3600 1 300 100 60 10000`; 73 RDB saves e o
  último BGSAVE reportou `ok`.
- Compose não monta volume Redis, então os snapshots ficam no container e não
  são persistidos após recriação.
- No instante da consulta: `used_memory=1.39M`, pico `1.45M`,
  `evicted_keys=0`, `keyspace_hits=9123`, `keyspace_misses=640`,
  `total_commands_processed=36898`.
- Uma amostra curta `redis-cli --latency -i 0.1` observou 11 PINGs: mínimo
  0 ms, média 0.18 ms, máximo 1 ms. Não representa p95 nem carga de produção.

## Decisão preservada

Não usar a instância Redis compartilhada para cache de embeddings ou vector
search nesta implementação. A ausência de módulo foi confirmada no container e
`allkeys-lru` com 256 MiB mantém risco de competir com o cache de scraping.
Embeddings usam cache LRU limitado em memória de processo. Vector search segue
desnecessário sem catálogo indexável.

## Evidência

- Configuração estática: `compose.yaml` e ADR 0020.
- Runtime: `docker compose exec redis redis-cli INFO server`, `MODULE LIST`,
  `CONFIG GET maxmemory maxmemory-policy appendonly`, `INFO memory`,
  `INFO stats` e `redis-cli --latency -i 0.1`, executados em 2026-09-24.
- Relatório atualizado:
  [`embeddings-architecture-investigation.md`](../../matching/embeddings-architecture-investigation.md).
