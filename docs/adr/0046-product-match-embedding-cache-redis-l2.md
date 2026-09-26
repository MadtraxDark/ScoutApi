# ADR-0046: Cache L2 de embeddings do Product Match em Redis

- Status: Accepted
- Data: 2026-09-26

## Contexto

O provider local elimina cobrança, mas repetir textos entre MatchRuns geraria
inferência e CPU sem necessidade. O Product Match já executa Redis para cache e
coordenação. O modelo de texto e os vetores não devem virar fonte de verdade,
nem ocupar uma tabela persistente.

## Decisão

- Manter LRU bounded local como L1 e usar Redis como L2 somente quando
  `MATCH_EMBEDDINGS_CACHE_BACKEND=redis`; Compose e exemplo local configuram
  `redis` por padrão, com opção explícita `local`.
- Usar namespace `scout:match:embedding:v1:` e a chave SHA-256 já escopada por
  versão do texto, provider/model/prefixo, fingerprint SHA-256 do endpoint,
  revisão configurada dos pesos, dimensão, representação e conteúdo.
  Incrementar `MATCH_EMBEDDINGS_CACHE_REVISION` ao substituir pesos mantendo o
  mesmo ID de modelo e endpoint. A URL bruta não entra na chave nem no log.
  O valor Redis é somente o vetor float, com TTL; o texto bruto não é enviado
  para a chave nem persistido/logado.
- No hit Redis, preencher a L1 local para o restante do processo. No erro de
  leitura/escrita Redis, manter a L1 e permitir a chamada normal ao provider.
  Eviction/expiração apenas provoca novo cálculo; Redis nunca é SoT.
- Não adicionar índice ANN, RediSearch ou armazenamento persistente de vetor.
  TTL e o limite `MATCH_EMBEDDINGS_CACHE_ENTRIES` mantêm escopo pequeno; o
  `maxmemory`/`allkeys-lru` existente continua a autoridade de pressão global.

## Consequências

- Mesmo título pode ser reutilizado por workers/processos diferentes dentro do
  TTL e economizar CPU; o primeiro miss continua uma chamada local ao provider.
- A API pode continuar com cache L1 se Redis falhar, e o match continua com
  fallback determinístico se provider falhar.
- Redis compartilha orçamento de memória com cache/coordenação já existentes;
  expiração/eviction podem reduzir hit ratio, sem afetar correção.
- Não há armazenamento durável nem recuperação de vetores depois do TTL ou
  eviction. A evidência local permanece exploratória; não habilitar `active`
  sem benchmark rotulado e calibração apropriada caso o escopo seja ampliado.

## Alternativas

- Só LRU local: substituída para permitir reutilização entre processos conforme
  solicitação do produto.
- Tabela no PostgreSQL/vector store: rejeitada; aumentaria retenção e schema
  para uma evidência efêmera sem corpus de busca vetorial.
- Vetores como Redis SoT/index: rejeitados; não há necessidade de ANN nem de
  reconstruir o cache de embeddings em produção.
