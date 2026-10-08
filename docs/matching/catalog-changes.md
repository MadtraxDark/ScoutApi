# Pesquisa de alterações do catálogo

Decisão: [ADR 0052](../adr/0052-catalog-change-explorer.md).
Base histórica e cursor: [atividade do catálogo](catalog-activity.md).

## API

`GET /products/changes` retorna `items` e `next_cursor`.

| Parâmetro | Contrato |
|---|---|
| `q` | Até 200 caracteres e oito termos; AND entre termos; substring literal |
| `event_type` | Tipo normalizado, incluindo `seller_changed`, `gtin_learned`, `scrape_failed`, `unchanged` e `other` |
| `store` | Chave regional do registry, respeitando aliases históricos |
| `product_id` | UUID do produto canônico |
| `started_at` | Timestamp com timezone, inclusivo |
| `ended_at` | Timestamp com timezone, exclusivo; posterior ao início |
| `limit` | 1 a 50, default 25 |
| `cursor` | Cursor opaco; reiniciar ao mudar os critérios |

Permissão: `products:read`; owner, legado compartilhado ou admin; rate scope
`default`. Filtros inválidos retornam 422; falhas de banco são sanitizadas.
Resposta de sucesso: `Cache-Control: private, no-store`.
Não há endpoint público, dependência nova, migration ou alteração em configuração.

Pesquisa por título/marca/modelo atuais, display name da loja e seller em
before/after do evento. Acentos latinos comuns e caixa são normalizados; `%`,
`_`, barras e sinais têm significado literal. Não pesquisa payloads arbitrários.

Criação de produto vem de `created_at`; todos os registros de `OfferEvent`
visíveis são preservados. `offer_created` tem apresentação `new_offer`, mas
mantém o ID original. Desconhecidos recebem `other`. Não aplica dedupe do
feed resumido nem omite registros antigos de preço numericamente igual.

Resposta comercial: contrato aditivo `ActivityItem`, com `product.model`,
`offer.old_seller` e `offer.seller`. Antes/depois e snapshots brutos permanecem
internos. O fallback para snapshot continua limitado à evidência anterior à
ocorrência, conforme ADR 0051. Nenhum preço vem do listing atual.

## Interface

PriceScout: `/admin/catalogo/alteracoes`, acessível pelo menu e pela atividade
recente. Filtros ficam em `q`, `event_type`, `store`, `from`, `to` e `product_id`
na URL. Datas representam dias locais completos; `to` é convertido para o
início do dia seguinte. O backend recebe UTC explícito.

Cada resultado mostra produto, marca/modelo, loja, evento e horário. Seller
anterior/novo fica visível e a seção de evidências mostra valores históricos.
Busca destaca todos os trechos encontrados com `<mark>`, preservando acentos,
capitalização e caracteres originais. Termos sobrepostos são unidos.
Valores ausentes não são fabricados. Labels técnicos têm apresentação explícita.

Paginação carrega 25 por vez sem duplicatas. Mudança de filtros aborta leituras
anteriores e reinicia a consulta; sair da página cancela somente a leitura.
Loading, vazio, falha/retry e falta de permissão têm estados próprios.

A busca incremental aplica critérios após 300 ms sem nova alteração. Enter
aplica imediatamente; composição IME aguarda confirmação. O formulário mantém
foco e cursor; resultados permanecem montados e preservam a última resposta
aceita durante refetch, com substituição atômica ao concluir. Erro de atualização
mantém lista e contador; estado vazio depende de resposta concluída. A URL usa replace
nas atualizações automáticas e push no envio explícito, com restauração ao
voltar/avançar. Texto inválido não consulta a API; limpar cancela texto pendente.
Detalhes do frontend: `PriceScout/docs/catalog-changes.md`.

## Validação reproduzível

- Backend: `python -m pytest tests/unit/test_catalog_activity.py tests/unit/test_security.py -q`.
- Frontend: `npm exec -- tsx --test utils/display/change-search.test.ts utils/display/activity.test.tsx`, `npm run typecheck` e `npm run lint`.
- PostgreSQL: migration Alembic em banco local separado `scout_activity_test*`,
  configurar `TEST_ACTIVITY_DATABASE_URL` e executar o teste integrado de activity.
- Chrome: `python tests/e2e/catalog_changes_browser.py`, com essa mesma variável,
  API isolada em 8012 (`AUTH_REQUIRED=false` somente em teste, workers desativados)
  e frontend local em 3000. O contexto dedicado encaminha o transporte da API para
  o ambiente isolado; dados comerciais vêm do PostgreSQL real. Ajustar
  `TEST_FRONTEND_API_ORIGIN` se o frontend usar origem diferente de localhost:8000.
  Fixtures de catálogo só existem nesse banco; apenas erro de transporte é simulado.
  Evidências em `.tmp/catalog-changes-browser/`.
- Estabilidade visual: `python tests/e2e/catalog_changes_stability_browser.py`,
  com catálogo local populado (leitura apenas), frontend em 3000 e API em 8000.
  Respostas comerciais reais com timing e falhas de transporte controlados.
  Confere loading inicial, retenção durante refetch/erro, empty após sucesso,
  containers persistentes, altura, scroll, contador e sequência 580 até 5800x3d.

### Conferência em 2026-10-07

Suíte rápida: 1191 passed, 11 skipped (serviços opcionais), em 28,52s.
O cenário PostgreSQL separado foi habilitado e aprovado. Frontend: 21 testes
de apresentação/regressão, lint global, typecheck e build de produção aprovados.
Backend: Ruff, formato e mypy src aprovados.

Chrome dedicado validou paginação 25 para 34, filtros combinados de tipo/loja/
período, busca com acentos e caixa distintos, destaque exato, evidências, reload,
vazio, voltar, link do produto e erro/retry. Viewports 320/768/1024/1440 sem overflow
e sem erro JavaScript. A linha de horário foi corrigida após a primeira conferência
mobile. Banco/API de teste foram encerrados e removidos após a execução.

Consulta fria de cinco termos no cenário pequeno: 95,52ms. Esse valor inclui
construção/projeção e não representa benchmark de catálogo grande. A consulta
mantém limite no banco, cursor e carregamento de imagens em lote.

API Docker local atualizada e saudável: `/health` 200, endpoint presente no
OpenAPI, página de 25 eventos reais e pesquisa verificadas. O ambiente local
existente usa `development` com `AUTH_REQUIRED=false`; os testes de auth
obrigatória validaram 401 sem credenciais. Build: instalação de dependências
40,6s, Camoufox pinned 88,6s, export/unpack 80,8s. Sem mudança de configuração.

Conferência final com API Docker e catálogo reais: busca com 25 correspondências
destacadas, navegação pelo menu, foco por teclado e zero erro JavaScript.
Screenshot local: `.tmp/catalog-changes-browser/real-local.png`.

Busca incremental validada em 2026-10-07: 23 testes frontend, lint, typecheck e
build aprovados. Chrome com catálogo real confirmou debounce, uma consulta por
sequência de digitação, foco/caret, voltar/avançar, reload, IME, limpar texto
pendente, ausência de request para texto inválido e descarte de resposta antiga
atrasada. Filtros e reinício de cursor aprovados; viewport 320px sem overflow e
zero erro JavaScript. Harness versionado ampliado para digitação incremental;
PostgreSQL isolado + Chrome aprovados em 15,54s, com limpeza do banco/API de teste.
Execução direta por PowerShell e Playwright CLI, sem Cursor.

Correção de estabilidade em 2026-10-07: a key variável dos resultados provocava
remount e reinicialização com lista vazia (25 -> 0 -> 25 itens; altura 6018,5 ->
230,19 -> 6009,08px). Agora o container permanece montado e conserva a última
resposta até concluir. Intervalo controlado de refetch: altura 6067,28px antes
e durante, contador/lista/foco/scroll preservados e nenhum novo layout-shift.
Mobile 320px também manteve altura; erro não removeu dados nem alterou altura.
Isso não representa benchmark de CLS global, nem de CPU/renderizações.
Mudanças reais de conteúdo continuam alterando naturalmente a altura.

Teste versionado de estabilidade aprovado, inclusive teclado com sequência
580/5800/5800x/5800x3/5800x3d, loading inicial, empty após sucesso e erro/retry.
Regressões de IME, respostas fora de ordem, URL, filtros e cursor aprovadas.
23 testes frontend, lint, typecheck, build e Ruff aprovados. Harness isolado
PostgreSQL/Chrome aprovado em 16,55s, com banco/API temporários removidos.
