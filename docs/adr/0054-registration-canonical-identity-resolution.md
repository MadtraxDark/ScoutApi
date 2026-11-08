# ADR-0054: Resolução de identidade canônica no cadastro de produtos

- Status: Accepted
- Data: 2026-11-08

## Contexto

`POST /products` reconhecia um produto existente apenas por dois critérios:
um listing da **mesma loja** (`canonical_url`, `store+country+product_id`,
`store+country+sku`) ou um GTIN. Sem GTIN, `get_or_create_canonical` sempre
inseria um novo `CanonicalProduct`. Como muitas lojas não expõem EAN, o mesmo
produto adicionado por outra loja gerava um canônico duplicado, em vez de uma
nova `StoreListing` no canônico existente.

Além disso, ao reaproveitar um registro, a API respondia `200 created=false`
sem explicitar o motivo, e a interface informava "Produto salvo". Reimportar o
mesmo anúncio também registrava imagens e semeava snapshot no produto
existente.

`canonical_products` não tem chave natural confiável sem GTIN. Por isso, uma
constraint única não resolve o problema nem a concorrência.

## Problema / decisão necessária

Como impedir um segundo `CanonicalProduct` para um produto já cadastrado,
sem comparar apenas nome ou URL e sem bloquear novas ofertas de outras lojas?

## Alternativas consideradas

- **Unique constraint em `canonical_products` (marca, modelo, variante):**
  rejeitada. A normalização de identidade (chips de GPU, cores, armazenamento,
  linhas de cooler) não é expressável em SQL de forma estável, e títulos
  diferentes descrevem o mesmo produto.
- **Comparação por título ou URL:** rejeitada. Gera falsos positivos e falsos
  negativos entre lojas.
- **Validação só no frontend:** rejeitada. A API é a autoridade (ADR 0021,
  AGENTS do PriceScout).
- **Resolver no backend reaproveitando o `MatchingEngine` do Product Match:**
  adotada.

## Decisão

`CanonicalIdentityResolver`
(`src/scout_api/modules/matching/canonical_resolver.py`) resolve cada cadastro
nesta ordem, da evidência mais forte para a mais fraca:

1. listing da mesma loja: resulta em `existing_listing`;
2. GTIN: resulta em `existing_product_new_listing`;
3. `target_product_id` confirmado pelo usuário: `existing_product_new_listing`
   (`matched_by=user_confirmed`);
4. identidade: pré-filtro por marca normalizada e `canonical_model_key`,
   depois `MatchingEngine.score`, com as mesmas regras de variante do Match
   (cor, armazenamento, edição, família `rtx5070` diferente de `rtx5070ti`).
   - `auto_match`: `existing_product_new_listing`;
   - `review`: `needs_review`. Nada é criado; `POST /products` responde
     `409 PRODUCT_IDENTITY_REVIEW_REQUIRED` com `existing_product`;
   - sem candidato: `new_product`.

Regras de precisão:

- sem marca e modelo identificáveis, não há correspondência por identidade;
- um candidato que já tem listing ativo da **mesma loja** é rebaixado para
  `review`, porque indica outra variante;
- dois candidatos `auto_match` com a mesma confiança são rebaixados para
  `review`;
- atributos estruturados (`edition`, `color`, `storage`...) entram na
  identidade quando o título não os traz; valores derivados do título
  prevalecem;
- só produtos visíveis ao usuário entram na busca. Produto privado de outro
  dono nunca é sugerido nem revelado (BOLA).

Efeitos:

- `existing_listing` é idempotente: sem novas imagens e sem snapshot; apenas
  aprende o GTIN ausente (`ensure_gtin_identifier`);
- `existing_product_new_listing` anexa a listing e registra imagens só se o
  canônico ainda não tiver galeria;
- `create_new_product=true` ignora apenas a correspondência por identidade.
  Listing da mesma loja e GTIN continuam valendo;
- `ProductRegisterResponse` ganhou `resolution`, `matched_by` e
  `match_confidence`;
- `POST /products/resolve` executa o mesmo resolver **sem gravar**, para a UI
  avisar antes da aprovação.

Concorrência: dentro da transação do cadastro,
`pg_advisory_xact_lock(hashtextextended('canonical-identity:{marca}|{modelo}', 0))`
(fallback: GTIN) é adquirido antes da busca por identidade e liberado no
commit ou rollback. Há um único lock por transação, portanto não há ordem de
aquisição nem deadlock. Uma colisão de hash só serializa cadastros a mais. Em
SQLite (testes) o lock é no-op.

## Justificativa

- Reutiliza o critério de equivalência já validado pelo Product Match, em vez
  de criar um segundo critério divergente.
- Mantém a separação: `CanonicalProduct` é o produto; `StoreListing` é a
  oferta da loja.
- Prefere precisão: dúvida vira decisão explícita, nunca fusão silenciosa.
- Lock transacional segue a prática documentada para get-or-create sem chave
  única. Fontes:
  <https://hakibenita.com/postgresql-get-or-create>,
  <https://www.netguru.com/codestories/advisory-locks>,
  <https://forum.elixirforum.com/t/how-to-do-get-or-insert-by-in-ecto-without-race-condition/22160>,
  <https://chat2db.ai/resources/blog/postgres-advisory-locks-guide>.

## Consequências positivas

- O mesmo produto vindo de outra loja vira nova oferta do canônico existente.
- Reimportação é idempotente e explícita para a interface.
- GTIN aprendido tardiamente passa a unificar cadastros futuros.
- Teste de integração em PostgreSQL comprova o lock: sem ele, o cadastro
  concorrente cria um canônico duplicado.

## Trade-offs / consequências negativas

- Custo do cadastro: varre até 5000 canônicos visíveis (colunas leves) e
  pontua só os que passam no gate de marca e modelo. Catálogos maiores podem
  exigir um índice ou uma coluna de chave de modelo normalizada.
- Falso negativo continua possível quando o scraper não fornece marca ou
  modelo; nesse caso o comportamento é o anterior (produto novo).
- Duplicatas já existentes não são unificadas por esta mudança; candidatos
  empatados viram `review`.
