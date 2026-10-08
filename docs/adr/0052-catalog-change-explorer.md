# ADR-0052: Consulta administrativa do histórico do catálogo

- Status: Accepted
- Data: 2026-10-07

## Contexto

O feed do ADR 0051 resume atividade comercial e omite registros técnicos e
eventos redundantes da mesma observação. A investigação de alterações precisa
consultar os registros originais, incluindo seller e observações sem mudança.

## Problema / decisão necessária

Oferecer pesquisa administrativa paginada, filtrada e com correspondências
visíveis, mantendo a semântica do resumo de atividade recente.

## Alternativas consideradas

- Filtrar páginas do resumo no frontend: perderia eventos omitidos e resultados
  em páginas ainda não carregadas.
- Nova tabela de auditoria/índice externo: duplicaria persistência e exigiria
  sincronização, sem necessidade demonstrada neste escopo.
- Consulta própria sobre as fontes persistidas, reutilizando a projeção histórica.

## Decisão

`GET /products/changes` pesquisa `OfferEvent` e `CanonicalProduct.created_at`.
Reutiliza repository/service/projeção do histórico, com modo explícito de
investigação que preserva os registros originais antes da paginação. Snapshot
continua sendo evidência auxiliar histórica, nunca uma alteração inventada.
`GET /products/activity` mantém suas regras de resumo e dedupe.

A busca usa até oito termos literais, combinados por AND, com correspondência
por substring em título, marca, modelo, nome de loja e seller anterior/novo.
Normalização de acentos latinos e caixa usa `translate`/`lower` do PostgreSQL,
sem extensão, serviço ou biblioteca nova. Filtros e ownership precedem os
limites por fonte e o merge com cursor do ADR 0051.

PriceScout expõe `/admin/catalogo/alteracoes`, preserva filtros na URL e usa
o client HTTP centralizado. O destaque usa `<mark>` e texto escapado pelo React;
índices normalizados são mapeados ao texto original, preservando caracteres.
Busca literal não interpreta regex, SQL wildcards nem HTML.

O endpoint requer `products:read`, auth e rate limiting existentes. Tipos
desconhecidos recebem `other`, com label neutro no frontend. A resposta possui
somente a projeção comercial permitida; erros/payloads brutos não são expostos.

## Justificativa

Reutiliza a fonte de verdade e separa as necessidades de resumo e investigação
sem nova persistência. Labels continuam pertencendo ao frontend.
Referências: [SQLAlchemy contains/autoescape](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.ColumnOperators.contains),
[Next.js useSearchParams](https://nextjs.org/docs/app/api-reference/functions/use-search-params)
e [React: conteúdo e HTML](https://react.dev/reference/react-dom/components/common).

## Consequências positivas

- Pesquisa considera todo o histórico visível e preserva eventos omitidos pelo feed.
- Reload, voltar/avançar e filtros compartilhados mantêm os critérios.
- Correspondências destacadas preservam a escrita original e não inserem HTML bruto.

## Trade-offs / consequências negativas

- Substring normalizada pode exigir varredura para termos amplos. Não há promessa
  de busca indexada; o volume retornado é limitado e os tempos são observáveis.
- Não há busca fuzzy, regex ou pesquisa em erros/JSON arbitrário.
- Título, marca, modelo e imagem são atuais; seller, preço e horário vêm da evidência
  histórica. Snapshots sem eventos não viram ocorrências artificiais.
- Cascades existentes permanecem: produtos/listings excluídos não constituem um
  audit log independente. Valores legados ausentes permanecem ausentes.

Contrato: [explorador de alterações](../matching/catalog-changes.md).
