# ADR-0051: Atividade do catálogo como read-model do histórico persistido

- Status: Accepted
- Data: 2026-10-07

## Contexto

O PriceScout renderizava uma lista vazia de atividade produzida no frontend.
`OfferEvent` já registra acontecimentos comerciais e `OfferSnapshot` guarda
observações; produtos têm `created_at` persistido. Labels pertencem à interface.

## Problema / decisão necessária

Fornecer atividade paginada e semanticamente correta, com imagens, lojas,
preços históricos e ownership, sem duplicar o sistema de histórico.

## Alternativas consideradas

- Nova tabela de atividades: duplicaria eventos e exigiria escrita sincronizada.
- Merge no frontend: exporia payloads técnicos e dificultaria paginação/ownership.
- Duas fontes limitadas com `UNION ALL` e projeção no backend: reutiliza o banco.

## Decisão

`GET /products/activity` integra `CanonicalProduct.created_at` e os eventos
comerciais permitidos em `OfferEvent`, através de repository e service de leitura.
As duas fontes têm limites antes do merge; o cursor opaco usa timestamp UTC e ID
com prefixo de origem. A ordenação decrescente é determinística, inclusive em empates.
Auth, rate limiting e visibilidade seguem `products:read`: owner, legado compartilhado
ou admin. Não é adicionada uma rota pública ou um papel novo para a interface admin.

Imagens são carregadas em lote pelo repository existente. A URL principal segue
o contrato canônico AVIF/original. Lojas usam metadados administrados/registry.
Preços vêm de before/after; eventos antigos esparsos de criação/promoção podem usar
o último snapshot anterior ao evento, nunca o preço atual do listing. Ausência de
evidência não é preenchida com valores inventados. Moeda anterior e nova são
independentes; direção de preço só existe para valores na mesma moeda.

Normalização ocorre na consulta antes da paginação e preserva eventos originais.
`out_of_stock`/`offer_removed` prevalecem sobre `availability_changed` da mesma
observação; eventos de promoção prevalecem sobre `price_changed` associado.
`offer_created` vira `new_offer`; ocorrências equivalentes de criação na mesma
observação são reduzidas a uma. Observações distintas continuam visíveis. Eventos de preço numericamente igual
na mesma moeda são omitidos se o preço normal também não mudou; o registro técnico
é preservado. Chaves históricas de lojas são resolvidas por chave/país do registry.

Eventos novos de cadastro/expiração passam a preservar evidência comercial.
Refresh compartilha `detected_at` e o `scraped_at` entre eventos da observação.
Isso não muda navegação, scraping, retries, scheduling nem detecção de diferenças.

## Justificativa

É uma leitura derivada de dados já persistidos. Evita tabela, backfill e segunda
fonte de verdade; uma consulta do feed e uma de galeria evitam N+1. Dois índices
compostos sustentam ordenação de produtos/eventos. A forma de query usa
[SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/selectable.html).
O frontend segue cleanup/cancelamento recomendado pelo
[React](https://react.dev/reference/react/useEffect), sem nova biblioteca.

## Consequências positivas

- Reload consulta acontecimentos reais, com valores e datas persistidos.
- UI tem contrato semântico pequeno, sem erros de scraping ou secrets.
- Cursor evita deslocamentos de offset quando surgem novos registros.

## Trade-offs / consequências negativas

- Exclusão do produto/listing continua seguindo os cascades existentes; isto
  é atividade do catálogo atual, não um audit log independente de exclusões.
- Eventos antigos com evidência incompleta podem omitir preços; não há backfill.
- Dedupe legado é conservador: usa observação atual/previous snapshot documentado,
  não proximidade arbitrária de horários. Expiração wall-clock é independente.
- Imagens e título usam o catálogo atual; preço e horário são históricos.

Contrato e validações: [atividade do catálogo](../matching/catalog-activity.md).
