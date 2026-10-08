# Atividade recente do catálogo

Decisão: [ADR 0051](../adr/0051-persistent-catalog-activity-read-model.md).
Integração: [PriceScout](../integration/pricescout.md).

## Endpoint

`GET /products/activity?limit=15&cursor=...`

- Requer `products:read`, auth padrão e rate scope `default`.
- Limite: 1 a 50; default 15. Cursor inválido: 422 `INVALID_ACTIVITY_CURSOR`.
- Resposta: `items` e `next_cursor` (null ao terminar). Cliente trata cursor como opaco.
- Itens: `id`, `type`, `occurred_at`, `product`, `store` e `offer`.
- `product`: ID, título, marca e `primary_image_url` canônica.
- `store`: chave e `display_name`, null para criação de produto.
- `offer`: listing, preços decimais como strings, moeda antiga/nova, bases
  (`pix`, `regular`, `promotion`), direção, disponibilidade e expiração quando observada.
- Sem before/after brutos, credenciais Drive, payloads de crawler ou mensagens localizadas.

## Semântica

| Fonte | Tipo do feed | Valor usado |
|---|---|---|
| `CanonicalProduct.created_at` | `product_added` | sem oferta |
| `offer_created` / `new_offer` | `new_offer` | after comercial |
| `price_changed` | `price_changed` | before e after; direção na mesma moeda |
| `offer_removed` | `offer_removed` | último valor de before |
| `out_of_stock` | `out_of_stock` | último valor de before |
| `availability_changed` | `availability_changed` | estado observado |
| `promotion_activated` | `promotion_activated` | preço da promoção em after |
| `promotion_expired` | `promotion_expired` | preço da promoção em before |
| `promotion_updated` | `promotion_updated` | before/after da promoção |

`unchanged`, `scrape_failed`, `seller_changed`, `gtin_learned` e tipos desconhecidos
não integram o feed principal. Falha não cria mensagem de remoção.

O preço comercial prioriza Pix e depois normal/original. Se Pix permanece igual
mas o preço normal muda, o evento descreve esse preço normal. Promoções priorizam
seu preço explícito. Moeda ausente impede texto com valor; não assume BRL.

Para eventos legados esparsos de criação/promoção, o snapshot é selecionado com
`scraped_at <= detected_at`. Não se consulta preço atual do listing. O frontend
omite valores ausentes. A atividade não infere causas de encerramento/remoção.

## Ordenação, dedupe e performance

Cada fonte retorna no máximo limit+1, com keyset aplicado antes do `UNION ALL`.
O merge usa `occurred_at DESC, id DESC`; ID tem prefixo para separar as fontes.
A consulta retorna somente uma página, sem carregar todo o histórico na aplicação.

A mesma observação é reconhecida pelo timestamp compartilhado ou `after.scraped_at`.
Promoções legadas de refresh podem usar o mesmo `before.scraped_at` com estado
estruturado em after; eventos wall-clock ficam fora dessa equivalência.
Prioridades são aplicadas no SQL antes de cortar a página (ver ADR).
Eventos de preço numericamente igual, mesma moeda e preço normal inalterado são
omitidos, incluindo diferenças antigas de representação decimal. Dados malformados
não são convertidos por CAST antes da validação; sem evidência completa, o evento
permanece com texto neutro. A mudança do preço normal com Pix igual é preservada.
Aliases de spiders (ex.: `amazon`) são resolvidos pelo par chave/país do registry,
inclusive para escolher os metadados administrados da loja regional.

O feed usa duas consultas por página não vazia: join de eventos/listing/produto/loja
com fallback histórico correlacionado e uma consulta de imagens em lote. URLs de
imagens são calculadas uma vez por produto. Índices: migration `0033_catalog_activity`.

## Interface

O PriceScout usa componente independente dos indicadores do dashboard, com
miniatura de 46 px (42 px no mobile), `<time>`, divisão leve, textos em `utils/display/`,
URL do produto, loading, vazio, erro discreto com retry e botão Ver mais.
`Intl.DateTimeFormat("pt-BR")` usa o timestamp da API em horário local.
`formatMoney` mantém BRL/USD/outras moedas. O fallback é `ImageWithState` existente.

## Validação

- Unitários: `python -m pytest tests/unit/test_catalog_activity.py -q`.
- PostgreSQL: configurar `TEST_ACTIVITY_DATABASE_URL` para banco local separado
  cujo nome comece por `scout_activity_test`, aplicar Alembic e executar
  `python -m pytest tests/integration/test_catalog_activity_postgres.py -q`.
- Frontend: `npm run test:activity`, `npm run lint`, `npm run typecheck`.
- Cenário PostgreSQL usa cadastro e refresh reais; respostas de loja são mockadas
  apenas em teste. Verifica expiração sem remoção, último preço, imagens, cursor
  e persistência em nova conexão. Não faz upload no Drive.
- Navegador validado em 2026-10-07: API real em PostgreSQL isolado, Ver mais
  15→24, clique no produto, F5, loading, vazio, erro/retry e viewport 390px.
  Apenas bytes de mídia e estados de falha/vazio foram interceptados como fixtures;
  o feed normal veio da API persistente. Sem escritas de teste no catálogo principal.

Documentação revisada: nova decisão, contrato, matriz de integração e índice.
Nenhuma alteração em variáveis de configuração, Dockerfile/Compose ou secrets.

### Resultado final dos checks locais

Em 2026-10-07, suíte rápida oficial: **1183 passed, 9 skipped**, em 18,84s.
Os skips dependem de serviços opcionais; o cenário novo PostgreSQL foi habilitado
explicitamente e passou. Frontend: **94 testes**, lint global e typecheck passaram.
Backend: `ruff check .`, `ruff format --check .` e `mypy src` passaram.

A validação inicial reproduziu falhas preexistentes numa cópia de HEAD (três erros
de tipos e violações Ruff). Para atender aos gates globais, foram corrigidos
imports/formato em diagnósticos/testes e três estreitamentos de tipos: detalhe de
condição (`str`), tipo do seletor Amazon e metadados locais antes de unpack.
Nenhuma alteração de navegação, espera, fingerprint, proxies ou extração comercial.
A suíte completa foi repetida após esses ajustes e permaneceu verde.

Tempos: leitura integrada PostgreSQL com cadastro/refresh/expiry/removal/reload
levou 0,37s no primeiro cenário; teste de página com 21 itens confirmou duas queries.
Os testes HTTP de activity levam cerca de 2,7s cada, por startup/shutdown da app
com lifespan dos workers; consultas unitárias ficaram abaixo de 0,1s.
Não houve benchmark de browser/crawler e sua capacidade permaneceu inalterada.

Validação no ambiente Docker local com catálogo existente: migration head 0033, HTTP 200, 15 linhas com imagens reais, horário, sem erro JavaScript ou overflow. Última revisão corrigiu aliases regionais e eventos sem mudança monetária observados nesse catálogo.

Conferência final após Docker: aliases Amazon Brasil/Amazon US corretos, preço igual omitido, 15 imagens e atividades preservadas após F5.
