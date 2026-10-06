# ADR 0050: Search locale por integração + condição comercial ≠ identidade

- Status: Accepted
- Data: 2026-10-05
- Supersede: parcial de [ADR 0024](0024-product-match-evidence-cascade.md)
  (item 4 da cascata — condição) e parcial de
  [ADR 0040](0040-matching-multilingual-attributes.md) (item 7 — condição)

## Contexto

O Product Match localizava atributos de query (cor, categoria) via
`StoreConfig.query_locale`, que caía no país da loja (`PY → es-PY`). Em
Shopping China, a query `… negro …` recuperava o SKU errado, enquanto
`… black …` recuperava `CELULAR APPLE IPHONE 17 256GB BLACK`.

Em paralelo, `condition_conflict` rejeitava Renewed/Refurbished contra
referência nova. Isso impedia ofertas Amazon Renewed do mesmo produto
canônico de chegar ao matcher/persistência.

## Problema / decisão necessária

1. País da loja ≠ idioma do índice de busca.
2. Condição comercial (New/Renewed/Refurbished) ≠ identidade do produto.

## Alternativas consideradas

- Tradução automática (LLM/API): rejeitada (não determinística, risco de
  alterar brand/model/MPN).
- Multiplicar sinônimos de cor (black+preto+negro) em toda loja: rejeitada
  (explode o query budget).
- Remover checagem de condição: rejeitada (USED/OPEN_BOX e rótulo comercial
  continuam necessários).
- `search_locale` explícito em `StoreConfig` + domain model de condição:
  adotado.

## Decisão

1. **Search locale:** `StoreConfig.search_locale` é a fonte de verdade do
   idioma de atributos localizáveis na SERP. Country fallback (`BR→pt-BR`,
   `US→en-US`, `PY→es-PY`) permanece só como último recurso. Integrações
   ativas devem declarar `search_locale` com evidência do índice.
2. **Query pipeline:** `ProductIdentity` canônico → locale da loja → query.
   Brand/model/MPN/GTIN/códigos nunca são traduzidos. Cor usa a representação
   preferida do locale (sem explosão de sinônimos). Tokens de marketing
   redundantes (`5g`, `4g`, `lte`) saem do título de busca.
3. **Condição:** `parse_offer_condition` modela
   `new | renewed | refurbished | used | open_box | unknown` (+ `grade` para
   Renewed Premium). Renewed/Refurbished **não** rejeitam identidade contra
   referência nova; geram razão `offer_condition`. USED/OPEN_BOX preservam
   `condition_reject`.
4. **Persistência/UI:** condição e carrier vão no snapshot/payload e em
   `ProductListingView`; PriceScout exibe rótulos pt-BR (Renovado, etc.) e
   ranking prefere New sobre Renewed ao marcar “menor preço”.
5. **Carrier:** `network_lock` / carrier permanecem estruturados; unlocked vs
   Verizon locked rejeita; lock ausente na referência + locked no candidato →
   `review`.

## Justificativa

Evidência live Shopping China (`quick_search`): `black` recupera o SKU;
`negro` devolve irmão errado. Renewed é a mesma identidade comercialmente
recondicionada — rejeitá-la como “outro produto” eliminava ofertas reais.

## Consequências positivas

- Queries alinhadas ao índice real de cada integração.
- Renewed/Refurbished descobertos, persistidos e rotulados.
- Identidade crítica (storage, Pro/Pro Max) permanece rigorosa.

## Trade-offs / consequências negativas

- `search_locale` exige manutenção por integração quando o índice muda.
- Ofertas Renewed aumentam o volume comercial apresentado; ranking/UI precisam
  deixar a condição visível.

## Relacionado

- `StoreConfig.search_locale` / `query_locale`
- `matching/identity.py` (`parse_offer_condition`, `build_search_queries`)
- `matching/engine.py`, prefilter SERP, PriceScout offer cards
- ADR 0024, ADR 0040, `docs/matching/README.md`
