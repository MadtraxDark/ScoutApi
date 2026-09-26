# PENDING-027 — Reduzir timeout de loja no Product Match C1

- Status: RESOLVED
- Tipo: PERFORMANCE
- Prioridade original: P1
- Área: matching/crawler
- Origem: 2026-09-25 — validação live de PENDING-026
- Resolvida: 2026-09-25

## Contexto e causa

A wave C1 registrou `STORE_WALL_TIMEOUT` para AliExpress e Visão VIP após
~180 s. O código de `title_hint_from_url` aceitava qualquer segmento URL com
mais de oito caracteres como título. Como a SERP do AliExpress fornece
`/item/{id}.html` sem título, IDs numéricos eram interpretados como nome de
produto. A pré-validação concluía `brand_mismatch` antes de buscar o PDP.

## Evidência

- MatchRun `f295d9e5-05e5-439f-a823-533fd86bb21d`: AliExpress e Visão VIP
  tiveram timeout em ~180,2 s; a segunda expirou antes de adquirir o browser
  para `browser_post`.
- Execução isolada de AliExpress, sem persistência: 23 candidatos sem título
  foram rejeitados por `brand_mismatch` derivado de IDs da URL; zero scrapes.
- Execução isolada de Visão VIP, sem persistência: cinco buscas concluídas em
  14,8 s, sem candidato, scrape, erro ou timeout.

## Correção e validação

- `title_hint_from_url` retorna `None` para segmentos finais compostos apenas
  por IDs numéricos longos. Slugs legíveis, como os de Magalu, continuam sendo
  usados como hints. A ausência de título mantém a identidade desconhecida e
  permite a validação normal do PDP.
- AliExpress isolado com budget padrão de cinco candidatos concluiu em 82,0 s:
  uma busca SERP (32,8 s) e cinco scrapes (49,2 s), sem erro ou timeout. Os
  cinco candidatos foram rejeitados pelo matcher após extração dos PDPs; não
  houve match fabricado.
- Smoke adicional limitado a um candidato terminou em 40,1 s e confirmou a
  leitura PDP.
- `POST /match` live concorrente, sem persistência, para AliExpress e Visão VIP:
  42,6 s totais; AliExpress 32,2 s e Visão VIP 42,5 s. Nenhum
  `STORE_WALL_TIMEOUT` ou `BROWSER_QUEUE_TIMEOUT`; ambas as lojas terminaram
  sem match e sem erro. As consultas foram frescas; o scrape AliExpress foi
  rápido nessa wave. Isso valida C1 concorrente, mas não constitui uma nova
  MatchRun persistida.
- Regressão RED/GREEN e testes AliExpress: 17 aprovados. C1 permaneceu em 1;
  não foram alterados waits, lifecycle, fingerprint, retries ou proxies.

## Relacionado

- [PENDING-026](PENDING-026-match-wave-c1-browser-queue-timeout.md): prazo da
  fila limitado ao wall time da loja.
- [Playbook AliExpress](../crawler/stores/aliexpress.md): títulos ausentes e
  IDs opacos em resultados de busca.
- ADR 0039: capacidade C1 do browser.
