# ADR-0043: Evidência semântica experimental no Product Match

- Status: Accepted
- Data: 2026-09-24

## Contexto

O Product Match descobre candidatos em SERPs ao vivo e usa uma cascata
determinística de identidade, conflitos e atributos antes do score textual.
Não há catálogo local vetorial. O worker de `MatchRun` é persistente no
PostgreSQL; Redis básico atende cache/coordenação de scraping e não declara
RediSearch. Embeddings só podem ajudar casos que a cascata deixa em `review`.

## Problema / decisão necessária

Como coletar evidência semântica mensurável sem substituir os gates existentes,
alterar o endpoint síncrono ou tornar uma falha externa impeditiva.

## Alternativas consideradas

- Vetor como candidate generation em Redis, PostgreSQL/pgvector ou serviço
  vetorial: rejeitada nesta etapa porque não existe corpus local indexável e a
  descoberta atual é feita em SERPs ao vivo.
- Similaridade como fallback após falha do matcher: rejeitada por poder
  contornar identificadores, conflitos e atributos estruturais.
- Similaridade adicional em toda decisão: rejeitada por custo, latência e
  interferência desnecessários em decisões já claras.
- Evidência adicional apenas em `MatchRun`, com default `off`, shadow mode e
  cache LRU local ao processo: escolhida.

## Decisão

- Avaliar embeddings apenas quando o matcher retorna `review` com
  `variant_semantic_uncertain`; rejeições e decisões claras não chamam provider.
- Manter `off` como padrão. `shadow` guarda estado/modelo/representação,
  similaridade, latência e hits de cache, sem alterar decisão. `active` exige
  chave e limiar explícito e só pode promover review se a mesma decisão contiver
  `brand_model_exact` e `variant_semantic_uncertain`, não houver `price_deviation`
  e o cálculo atual de preço não identificar razão extrema (ratio >= 4 na mesma
  moeda). Conflitos determinísticos continuam fora do caminho do embedding.
- O primeiro provider implementado é OpenAI Embeddings via `httpx`, selecionado
  por configuração no worker. Falhas, timeout e limites são fail-open. Os dados
  de título e identidade elegíveis são enviados ao provider quando o operador
  habilita modo e credencial; vetores e credenciais não são persistidos nem
  registrados.
- Cache LRU é apenas de processo, limitado por entries e TTL. Não usar Redis,
  persistência de vetores ou vector search até demonstrar ganho e capacidade.
- Persistir apenas metadados resumidos em `match_candidate_logs` (inclui tokens
  informados pelo provider, quando disponíveis); nunca vetor, texto enviado ou
  credencial. Aplicar migração Alembic 0031.
- Não habilitar `active` em produção até dataset revisado, holdout independente,
  baseline e limiar validados. O fixture atual é smoke pequeno, não decisão de
  modelo.

## Justificativa

O fluxo experimental mantém intacta a busca das lojas e o score síncrono. O
shadow permite comparar evidência com decisões determinísticas nas mesmas Runs.
O cache local evita competir com o cache de scraping Redis sob a configuração
existente. Um índice vetorial só se justifica se surgir catálogo indexável com
Recall@K/MRR superior ao fluxo atual.

## Consequências positivas

- Rollback imediato para `MATCH_EMBEDDINGS_MODE=off`.
- Falha de rede/provider preserva o matcher baseline.
- Saídas são comparáveis e observáveis sem persistir vetores.
- Limites de candidatos por Run, timeout e deadline de loja reduzem impacto.

## Trade-offs / consequências negativas

**Atualização de provider:** a exigência inicial de provider OpenAI/key foi
substituída pela [ADR 0045](0045-self-hosted-product-match-embeddings-provider.md),
que generaliza a URL de API e adiciona TEI self-hosted sem chave. Os limites de
shadow/active e fail-open desta ADR continuam válidos; cache foi atualizado
pela ADR 0046.

**Atualização de cache:** a decisão inicial de LRU apenas local foi substituída
pela [ADR 0046](0046-product-match-embedding-cache-redis-l2.md): LRU local L1 e
cache L2 efêmero com TTL no Redis já existente. Não há vetor durável ou ANN.

- Modo ativo pode alterar classificação e exige avaliação estatística e revisão
  operacional antes do rollout.
- O provider recebe texto de títulos/identidade dos casos elegíveis quando
  habilitado; a retenção e política do fornecedor precisam fazer parte da
  decisão operacional de habilitação.
- Sem chave/provider local neste ambiente não foi possível medir latência,
  custo ou qualidade semântica real. O dataset versionado contém apenas dez
  casos e não sustenta seleção de modelo nem limiar.
- A migration foi aplicada no PostgreSQL do Compose e Redis foi auditado em
  runtime; os containers de aplicação ainda usam imagem anterior ao código da
  feature e não houve chamada real ao provider.
