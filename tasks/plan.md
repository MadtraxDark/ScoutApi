# Plano: ofertas progressivas em Product Match

Data: 2026-10-05. Implementação autorizada posteriormente por “pode implementar tudo”. A investigação abaixo preserva o estado inicial. Contrato adotado no [ADR 0049](../docs/adr/0049-progressive-match-live-observations.md); operação e validação na [integração PriceScout](../docs/integration/pricescout.md).

## 1. Recomendação

Expor um snapshot leve em `GET /match-runs/{run_id}/live`, alimentado pelos outcomes que o worker já commita em `match_store_runs`. PriceScout consulta esse snapshot a cada 4 segundos e integra apenas os matches automáticos à área de ofertas, identificados como **Resultado parcial — encontrado nesta busca**. A Run continua sendo um job PostgreSQL, independente do browser. Listings, snapshots e enriquecimento GTIN continuam sendo finalizados pelo matcher.

Escolher inicialmente **UX progressiva**, não persistência progressiva de `StoreListing`. Há, porém, lacunas reais nos dados atuais: decisão do hit selecionado, roster das lojas, identidade para reconciliação e recuperação dos hits de attempts anteriores. Corrigi-las em etapas pequenas é necessário para satisfazer integralmente os requisitos. Um endpoint sobre os campos atuais, sozinho, não resolve todos eles.

## 2. Base verificada e limites da investigação

Os hashes foram consultados com `git ls-remote origin refs/heads/main`, sem atualizar branches:

| Projeto | HEAD local e main remoto |
|---|---|
| ScoutApiV2 | `ea8e3bcd9df427ebfa003e1330170c8ccc3dfe04` |
| PriceScout | `e7686ecc4c6d05483fba95b608f2edc1b67906ae` |

O backend não tinha alterações rastreadas. Havia diretórios temporários não rastreados. O frontend tinha alterações locais em hook, client, contracts, página, controle de requests e outras superfícies. Foram analisados o conteúdo de trabalho e o hook de HEAD, distinguindo-os abaixo. Essas alterações devem ser preservadas; a implementação futura deve revisar seu diff antes de editar os arquivos compartilhados.

Esta é uma investigação estática do código atual e pesquisa de fontes primárias. Não foi executada uma Run contra lojas reais nem validado um deployment. Tentativa de teste dirigido: Python global não contém `pytest`; o executável `.venv/Scripts/python.exe` foi bloqueado por Controle de Aplicativo. Portanto não há testes aprovados ou números de benchmark nesta entrega. A fase 0 prevê comprovação transacional em PostgreSQL/Docker, forma oficial do projeto.

## 3. Evidências e fluxo atual completo

Paths abaixo são relativos ao respectivo repositório. Links do backend apontam para a revisão verificada.

| Fonte real | Comportamento observado |
|---|---|
| [router.py](https://github.com/MadtraxDark/ScoutApiV2/blob/ea8e3bcd9df427ebfa003e1330170c8ccc3dfe04/src/scout_api/modules/matching/router.py#L929) | POST start retorna 202; endpoints active/status usam `_RL_POLL`; history/details usam `_RL_DEFAULT`; autenticação, permissão `match` e acesso ao produto são exigidos. |
| `match_run_service.py`: `start`, `get_active`, `get_status` | Uma Run por produto; reutiliza ativa; stale no start vira `worker_lost`; active exige lease válida. Visibility passa por `ProductRegistrationService.get_product(...viewer=principal)`. |
| `match_run_repository.py`: `get_with_details` | `selectinload` de stores e candidates; não é projeção apropriada para polling leve. |
| [match_run_worker.py](https://github.com/MadtraxDark/ScoutApiV2/blob/ea8e3bcd9df427ebfa003e1330170c8ccc3dfe04/src/scout_api/modules/matching/match_run_worker.py#L245) | Claim commitado; libera lock da Run antes do match; heartbeat separado; callback abre sessão própria, aplica outcome e commita. `outcome_lock` serializa callbacks do mesmo worker. |
| [product_match_service.py](https://github.com/MadtraxDark/ScoutApiV2/blob/ea8e3bcd9df427ebfa003e1330170c8ccc3dfe04/src/scout_api/modules/matching/product_match_service.py#L394) | Resolve elegíveis, remove `skip_stores`; waves serial/paralela/serial; search, candidatos, scrape e decisão; callback quando termina a loja. |
| `product_match_service.py`: linhas 995–1035 | Adiciona hit selecionado a `matches`, emite progresso e chama outcome; `matched_price = pix_price or original_price`; estado `match` não inclui a decisão selecionada. |
| `product_match_service.py`: linhas 1206–1230 e `_persist` | Após todas as waves, resolve GTIN; chama `_persist`, que faz upsert canonical/listing, snapshot, event, schedule e `flush`. O commit externo ocorre depois; `flush` não torna o catálogo visível a outras sessões. |
| `gtin_learning.py`: `resolve_trusted_gtin` | Só `auto_match` participa do consenso; valida GTIN e marca; múltiplos valores rejeitam consenso. Aprendizado intermediário e fallback de `learned` também existem: manter sem alterar inadvertidamente sua semântica. |
| `models.py`, `schemas.py`, `match_run_serializers.py` | Store outcome durável contém título/URL/preço/moeda/confiança/reasons, timestamps, erros, queries e candidates; status compacto não contém stores. |
| `docs/adr/0036-persistent-product-match-runs.md`, `docs/integration/pricescout.md` | Job PostgreSQL, lease/reclaim, polling, sem SSE; liveness não é apenas status. |
| PriceScout `hooks/useProductMatchRun.ts` | Poll de `/active`; quando active desaparece e há ID observado, consulta status por ID. Apenas summary chega à página. |
| PriceScout `app/admin/produtos/[id]/page.tsx` | `onTerminal` chama `load(id)`; `load` substitui product/draft/images e ativa loading global. `PersistedOffersList` lê `product.variants[].offers`, ordena por BRL comparável e usa key `offer.id`. |
| PriceScout `ProductMatchBanner.tsx` / `MatchRunHistory.tsx` | Banner mostra tempo/contadores; histórico carrega details sob demanda, incluindo candidates. |
| PriceScout `utils/api/services.ts`, `contracts.ts` | Métodos start/active/status/history/details existentes; Decimal já tratado como `string | number`; mapper de listings cria `CatalogOffer`. |

Fluxo: POST → pending commit → claim `SKIP LOCKED`/lease commit → identidade do catálogo primeiro, scrape de referência como fallback → busca por loja → outcome commit em sessão curta → consenso GTIN → `_persist`/flush → finalize completed e notificação → commit em `sweep_once` → polling detecta terminal → frontend recarrega produto.

As ofertas só aparecem no final porque os commits intermediários são de **evidências da Run**, não de listings; a API compacta não as expõe; a página só atualiza o produto no terminal. O callback acontece ao encerrar a avaliação da loja, não a cada candidato SERP, não a cada decisão individual e não depois do commit final do catálogo. A UX poderá mostrar o resultado no primeiro polling que ocorrer **após o commit do outcome**.

## 4. Lacunas que mudam o planejamento

1. **Review também é match no outcome.** Worker usa `include_review=True`; `best_by_store` aceita review; o callback grava `status="match"` sem `hit.decision`. Confiança não é substituto seguro da decisão. Não inferir decisão dos candidate logs: são truncados, podem conter vários candidatos e não identificam inequivocamente o vencedor.
2. **Roster/progresso inicial não existem.** `stores_total` começa em zero e só é atribuído em finalize; `MatchStoreRun` é criado no outcome, normalmente já terminal. Ausência de linha não prova que uma loja está executando. Não hardcode nove lojas nem marque todas como buscando.
3. **Reclaim preserva outcome, mas não reconstrói hit.** `terminal_store_keys()` pula lojas anteriores; `_match_with_reference` inicia `matches=[]`; `_persist` recebe apenas hits desta attempt. Não há reconstrução de `ProductPriceItem` a partir das linhas anteriores. Após crash antes do commit final, um outcome pode sobreviver sem virar listing. Isso é uma lacuna de consistência atual, não garantia de recovery completo.
4. **Callback pode falhar silenciosamente para a execução.** Worker loga exceção de persistência de outcome e continua. `completed` não garante que todos os outcomes estejam consultáveis. Fase 0 deve exercitar essa falha e decidir recuperação durável antes de liberar a feature.
5. **Preço parcial não tem todas as condições.** Callback usa Pix/original; catálogo usa `product_offer_from_price_item`, FX e regras comerciais. Não inventar disponibilidade, parcelamento, promoção ou preço de cartão a partir do escalar parcial.
6. **Lease expirada produz 204 em `/active`.** Após reload durante essa janela, descobrir somente via active perde acesso visual temporário aos resultados existentes. Precisa fallback ao histórico mais recente por produto, sem chamar uma Run stale de ativa.
7. **Review persiste com `status="review"`, mas mapper frontend não preserva a distinção.** O mapa atual de `view.listings` para ofertas não filtra nem retém decisão/status. A transição terminal deve preservar essa informação para não transformar review em confirmação ao recarregar o produto.

## 5. Alternativas de transporte

| Alternativa | Benefício | Custo/risco neste stack | Decisão |
|---|---|---|---|
| A: poll `/details` | Campos já disponíveis | JSON de queries/reasons/evidence, até 40 candidates/store, queries ORM extras; bucket default disputa CRUD; ainda falta decisão selecionada | Apenas relatório sob demanda |
| B: `/live` leve | Reusa commits PostgreSQL, reload e worker; um request por ciclo conhecido | Pequena latência de polling; projeção e contratos novos | Recomendada |
| C: SSE | Notificação rápida | Reconexão/replay/auth/proxy; exige snapshot durável de qualquer forma e revisão de ADR 0036 | Não justificado |
| D: WebSocket | Bidirecionalidade | Nenhuma necessidade bidirecional real; infra/ownership/reconnect extra | Descartado |
| E: Redis Pub/Sub | Sinalização entre processos | Entrega at-most-once, mensagem perdida offline; não é recuperação | Opcional futuro, sem incluir nesta entrega |

SSE como transporte de um job durável não necessariamente faz o job depender da conexão; o problema é reintroduzir complexidade sem provar insuficiência do polling. Uma atualização em até um ciclo de 4s atende o requisito expresso, com dados já commitados.

Pesquisa: o [padrão assíncrono Microsoft](https://learn.microsoft.com/en-us/azure/architecture/patterns/asynchronous-request-reply) sustenta status polling para operações longas. [SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks) exige sessão por thread. [Redis](https://redis.io/docs/latest/develop/pubsub/) documenta entrega at-most-once; [MDN EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource) e [WebSocket](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API) descrevem conexões persistentes. O [código/documentação Celery](https://github.com/celery/celery/blob/main/docs/userguide/tasks.rst) também alerta para custo de polling de banco. São comparações de padrões, não propostas de novas dependências. Não foi necessário adotar Celery, Redis Streams ou serviço pago.

## 6. Persistência recomendada e recovery

### A: UX progressiva — escolhida

Outcomes persistidos são resultados observados, exibidos como parciais. Canonical/listings/snapshots/eventos continuam no fluxo final. Uma Run falha preserva os resultados da Run e o catálogo anterior; resultados não reconciliados permanecem com aviso **Busca interrompida; resultado não confirmado no catálogo**, consultáveis pelo ID/histórico. Não promover automaticamente ao catálogo nem descartá-los silenciosamente. Se um commit parcial do catálogo existir por caminho excepcional, prevalece o dado retornado pela API e o estado da Run segue failed; não pressupor rollback perfeito sem teste.

`auto_match` selecionado → resultado progressivo; `review` → progresso indica **Requer revisão**, evidência fica no relatório; `reject`, `no_match`, `error` → nenhum card de oferta. `matches_found` mantém semântica legada e pode contar reviews; novo `auto_matches_found` conta apenas decisões explícitas confirmáveis. Falta de preço mostra produto encontrado com **Preço não informado**, sem participação em menor preço. Disponibilidade desconhecida nunca vira `available=true`.

Para recovery consistente mantendo A, propor staging durável do **hit selecionado**, não listings antecipados: campo `matched_decision` e payload versionado, sanitizado, com somente `ProductPriceItem`/identidade/condições necessárias para reconstruir `MatchHit` e persistir ao final. Não guardar HTML, cookies, headers, proxy, metadata arbitrária ou credenciais. Definir allowlist e limite de tamanho. Armazenar no mesmo commit do outcome. Reidratar hits das lojas puladas em nova attempt e uni-los aos atuais **antes** do consenso e `_persist`. Guardar também contexto da referência/identidade necessário para repetibilidade, em payload versionado da Run. Manter decisões já tomadas; não resscrapear lojas terminalizadas.

É uma correção delimitada da recuperação, justificada pelo requisito obrigatório de restart/consistência. Separá-la do transporte/UI, com testes próprios. Outcomes legados sem payload: decisão desconhecida não é auto_match; não fabricar produto completo a partir de título/preço. Política explícita: exibir evidência histórica, diagnosticar recuperação incompleta e encerrar com código documentado caso precise finalizar um hit irrecuperável. Não fingir sucesso integral. Novas Runs passam a ter o contexto completo.

`matched_listing_id` surgiu no commit `c38dc1f`, migration `0028_product_match_runs.py` e model. Busca em src/tests/docs/migrations só encontrou essas declarações: sem writer, sem serializer, sem FK e sem justificativa específica documentada. A intenção de vínculo é inferência do nome, não fato comprovado. Usá-lo **na reconciliação final**, preenchendo-o com `MatchHit.listing_id` após `_persist` e antes do commit terminal. Não é chave idempotente de snapshot por si só; não usá-lo para declarar persistido antes do commit. Se listing for removido, ID pode ficar histórico; o frontend confirma existência em GET produto.

Roster real: callback observacional após `_resolve_stores`, antes de aplicar skip, registra rows pending para os targets reais e `stores_total` em sessão curta; não recalcular elegíveis em cada GET. Reclaim reusa roster/contexto da Run. Callback de início pode atualizar pending→running sem tocar fetch/waits/browser; transação curta e fencing. Se não for implementado, label pendente é **Aguardando**, nunca execução inventada. Não reduzir callbacks terminais a progresso em memória.

Fencing: código atual verifica worker_id para outcomes/finalize; heartbeat também verifica attempts. Fortalecer mutações com worker_id + attempts + status e lock curto/UPDATE condicionado. O lock Python não protege outros processos. Commit dos outcomes, contadores e payload deve ser atômico. Falha de gravação exige política visível de retry limitado/recuperação; não acrescentar retries do scraper. Commit catálogo + vínculos + terminal + notificação deve ter teste de rollback e janela de crash. Validar fencing final antes de escrever catálogo; não manter row lock da Run durante operações externas longas.

### B: persistência definitiva por loja — adiada

Benefício: ofertas sobrevivem independentemente do sucesso final e GET produto muda cedo. Custo: upsert/reparent, snapshot/event/schedule e fencing em transações paralelas; separação de consenso GTIN; chave idempotente por Run/store para snapshot/event; reconciliação de conflitos e promoção de reviews. Unique listing atual não impede snapshots duplicados. Só adotar se o negócio exigir aproveitar definitivamente matches de Runs falhas. Não mover `_persist` para `process_one`. Se adotada futuramente, uma sessão nova por operação e transação única listing+snapshot+outcome+marcador idempotente; consenso/enriquecimento separado no final.

## 7. Contrato API proposto

`GET /match-runs/{run_id}/live` → 200 para Run conhecida, inclusive terminal/stale; nunca 204 por lease expirada. Auth + `require_permission("match")` + mesma visibility do produto + `_RL_POLL`. 404 indistinguível para Run inexistente/inacessível; 401/403, 429 com Retry-After e 503 conforme padrões existentes. Sem escrita, scrape ou refresh FX neste GET. Cache inicialmente `Cache-Control: private, no-store`.

Schemas:

```text
MatchRunLiveView
  run: MatchRunStatusView                 # reaproveita contrato existente
  is_effectively_active: bool            # calculado no backend pela lease
  auto_matches_found: int
  stores: list[MatchStoreLiveView]        # snapshot completo pequeno

MatchStoreLiveView
  id: UUID
  store: str                             # chave operacional/locale estável
  store_display_name: str | null
  status: MatchStoreRunStatus
  started_at, finished_at: datetime | null
  matched_decision: auto_match | review | null
  matched_listing_id: UUID | null
  matched_store: str | null               # origem normalizada para catalog join
  matched_country: str | null
  matched_product_id: str | null
  matched_canonical_url: str | null
  matched_url, matched_title: str | null
  matched_price: Decimal | null
  matched_currency: str | null
  matched_confidence: Decimal | null
  converted_price_brl: Decimal | null
  exchange_rate_status: str | null
  exchange_rate_updated_at: datetime | null
  error_code: str | null
```

Identidade comercial é necessária porque target `amazon_br` pode produzir listing `amazon` e country BR; comparar só `store` falha. Esses campos podem ser projetados do payload sanitizado persistido, sem novas colunas individuais. Decimal é string JSON no contrato proposto; client aceita string/number como já faz, normaliza valores finitos apenas para apresentação. Confidence não é preço. Campos matched são null em pending/running/no_match/error. Resultados não fazem duas listas redundantes: `liveResults` é derivado no frontend de stores com decisão explícita auto_match.

Sem candidates, reasons, embeddings, queries, payload de staging, worker_id ou reference internals. Erro é código conhecido traduzido pela camada de labels; mensagem sensível não atravessa live. Detalhes continuam em `/details`.

Backend: `MatchRunRepository.get_live_snapshot` usa projeção explícita run+stores e visibility equivalente à regra existente. Reusar `can_access_product` com o canonical mínimo necessário; não copiar parcialmente a regra de ownership. `get_status` hoje chama `ProductRegistrationService.get_product`, que monta listings, imagens e ProductView: não reutilizar esse caminho pesado dentro de live. Preferir uma consulta de snapshot run/stores para evitar contadores de um commit e stores de outro sob READ COMMITTED; alternativa é derivar agregados das stores retornadas e documentar consistência. Não chamar `get_with_details`, não lazy-load candidates. `MatchRunService.get_live` aplica autorização e cálculo da atividade/FX; serializer independente `match_run_to_live`.

Conversão: reusar políticas de `modules/exchange/conversion_service.py` e `enrich.py`, memoizar por moeda no request ou carregar cotações em lote pelo repository. Não executar `attach_conversion` indiscriminadamente por card criando N+1. FX vem exclusivamente de dados existentes no banco, sem provedor externo no polling. Sem cotação, ordenar estrangeiro no fim e mostrar valor original; nunca comparar USD bruto com BRL. Budget inicial: 1 query snapshot + até 1 query visibility se necessária + 1 query FX em lote; máximo 3 queries de dados, auth medido separadamente.

Índices existentes: PK da Run, `ix_match_store_runs_run_id`, unique `(run_id,store)`, produto/started_at para histórico. Suficientes para lookup live; nenhum índice novo proposto sem EXPLAIN. **Endpoint isolado não requer migration; solução completa recomendada requer migration** para decisão/payload de hit e contexto da Run. Roster e matched_listing_id usam estrutura existente. Próxima migration deve usar número livre na execução, sem assumir que `0032` continuará disponível; upgrade/downgrade e compatibilidade de rows antigas obrigatórios.

## 8. Revision, ETag e cadência

Primeira versão sem contador revision persistido, cursor ou 304. `last_activity_at` já existe mas também muda no claim/touch; `stores_completed` sozinho não detecta terminal/attempt/FX. Fazer comparação estrutural dos campos visíveis e preservar objetos de stores inalteradas; separar timer de elapsed da lista. Snapshot de poucas lojas é mais simples que deltas, tombstones e replay.

| Intervalo | Requests/min por observador | RPS para 1 / 10 / 50 observadores | Espera média teórica |
|---|---:|---|---:|
| 2s | 30 | 0,5 / 5 / 25 | 1s |
| 3s | 20 | 0,33 / 3,33 / 16,67 | 1,5s |
| 4s | 15 | 0,25 / 2,5 / 12,5 | 2s |
| 5s | 12 | 0,2 / 2 / 10 | 2,5s |

São estimativas, sem latência/requests iniciais; scheduling após conclusão soma duração da consulta. Escolher **4s**, uma chamada por ciclo de Run conhecida, pause em aba oculta, refresh ao foco com dedupe, backoff/Retry-After e jitter pequeno para distribuir carga. 2s só após benchmark/necessidade demonstrada. `poll` default é 300/min por identidade autenticada (IP fallback), compartilhado com notificações; não é quota global de 50 usuários. Múltiplas abas da mesma identidade precisam entrar no benchmark; não aumentar limites para esconder duplicação.

No frontend local, adicionar `/live` à regex de `utils/api/rate-limit-scope.ts`; hoje ela reconhece `/match-runs/{id}` exato, não seu subpath. Preservar `request-control`, cooldown por scope e dedupe. No main publicado esses auxiliares ainda não existem; integrar mudanças locais revisadas antes de depender deles.

[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-none-match) permite GET condicional com 304. Adotar só se payload/tráfego medidos justificarem: ETag do conteúdo efetivamente retornado, incluindo FX/attempt/atividade, auth antes de 304, cache privado e CORS para headers. Client atual trata `!response.ok` como erro e não implementa 304/cache da representação: requer alteração explícita de client, testes e invalidação por sessão. 304 economiza bytes, não automaticamente SQL nem tokens de rate limit. Hash sintético pode ser usado futuramente sem migration, mas não elimina consultas por si só.

## 9. Fluxo PriceScout, merge e reconciliação

Main publicado: setInterval de 4s inclusive idle; AbortController não é repassado nos requests do hook. Local: `startVisiblePolling`, um flight, sinal propagado, pausa hidden, idle 60s, foco e backoff. Base futura deve incorporar o comportamento local, não regressar ao loop antigo.

Hook retorna `activeRun`, `observedRun`, `isActive`, `isStarting`, `elapsedLabel`, `liveResults`, `storeProgress`, estado de recuperação e erro. `observedRun`/resultados permanecem no terminal; não apagá-los quando `activeRun` vira null.

1. Abrir produto: carregar catálogo e consultar `/active` uma vez. Run encontrada → guardar ID → chamar live imediatamente.
2. Run conhecida: só live por ciclo. Start 202 já fornece ID; não consultar active de novo antes de live.
3. Sem active: consultar histórico mais recente uma vez (já há `listMatchRuns`), identificar última Run pending/running stale ou terminal relevante e recuperar live. Não marcar stale como ativa. History fornece ID sem depender de localStorage. Ida/volta durante restart recupera resultados commitados; exibir **Aguardando recuperação** com timer ativo pausado, permitir novo start conforme política existente.
4. Idle: discovery moderado (local 60s) e foco para descobrir Run iniciada em outra aba. Terminal: interromper loop ativo e manter snapshot; somente discovery idle continua.
5. Unmount aborta observação; nunca cancela job. Mudança de productId/sessão limpa estado e invalida respostas tardias com sinal/generation. Single flight de POST e polling preservado.

Render: progresso por loja separado das ofertas, labels `Aguardando`, `Buscando`, `Encontrado`, `Requer revisão`, `Sem correspondência`, `Erro`. Usar layers `utils/display/` e `utils/store-display.ts`; sem IDs crus. Regiões aria-live discretas para chegada de oferta, não ler lista inteira a cada tick.

Criar modelo de apresentação discriminado `persisted | partial`, sem fingir que parcial é `CatalogOffer` completo. Área visual comum com marcador; ações de catálogo/monitoramento apenas para persistidos. Cache de objetos por `(run.id, storeRun.id)`; updates idênticos preservam referência. Não usar índice de array como key.

Merge identifica equivalência primeiro por `matched_listing_id`; fallback por origem normalizada + country + product_id, depois URL canônica usando a identidade enviada pelo backend. Não deduplicar só por loja: uma loja pode ter listings legítimos distintos. Não remover parâmetros arbitrariamente no browser. Separar key de identidade comercial da key operacional da busca.

Se parcial corresponde a oferta já persistida, manter uma linha com observação parcial explicitamente marcada; não sobrescrever silenciosamente o preço canônico. Novo resultado entra com key estável. Ordenar por mesma política `utils/money.ts`: preço BRL comparável crescente, sem conversão no fim, empate preserva posição/identidade anterior. Mudança legítima de menor preço pode mover a linha; memoização evita remontar todas. Parcial não recebe indicação de promoção/Pix sem condição explícita persistida.

No terminal, consumir snapshot final antes de parar polling. Recarregar catálogo em background **sem `load` global**, sem limpar lista, draft ou galeria. Aplicar dados persistidos e remoção dos parciais equivalentes em uma atualização do modelo visível, preservando key da linha durante handoff. `matched_listing_id` resolve joins de URL/alias e mudança de canonicalização. Sucesso sem listing equivalente: manter aviso de não reconciliação, retry limitado do GET e evidência histórica; não afirmar que foi salvo. Failed: catálogo anterior permanece, resultados conhecidos ficam marcados não confirmados. Reload terminal recupera via histórico+live sem toast retroativo. Novo start troca conjunto de resultados da Run exibida; histórico continua disponível.

Preservar os campos existentes `status`/`match_decision` de `ProductListingView` no mapper/contracts; review fica em seção de revisão ou badge próprio, nunca entre ofertas confirmadas. Isso é necessário também após reconciliação terminal.

## 10. Plano de implementação em etapas pequenas

Checklist concluída: [PENDING-031](../docs/pending/resolved/PENDING-031-progressive-match-results.md). A implementação foi autorizada após este planejamento. Estimativas originais são de escopo, não promessa de horas.

| Etapa | Arquivos previstos (existentes, salvo indicação) | Dependência / aceite / verificação |
|---|---|---|
| 0a — prova de commit | `tests/unit/test_match_runs.py`, novo `tests/integration/test_match_run_live.py` | Nenhuma. Pausar fake matcher após primeiro callback; outra sessão PostgreSQL lê outcome antes do retorno. Medir callback→commit; testar exceção de commit. Sem lojas reais para essa prova. |
| 0b — caracterizar reclaim/GTIN | `tests/unit/test_match_runs.py`, `tests/unit/test_matching_regression.py` | 0a. Reproduzir crash entre outcome e `_persist`, consenso com hits de attempts distintas e fencing. Registrar evidência da lacuna, sem tratá-la como garantia existente. |
| 1a — staging explícito | `models.py`, nova migration, `product_match_service.py`, `match_run_service.py`, `match_run_worker.py` | 0b. Persistir decisão/payload allowlist/contexto; nullable para legado; roundtrip e limite/segurança. Nenhuma alteração de fetch/browser. |
| 1b — recovery consistente | `product_match_service.py`, `match_run_worker.py`, `match_run_service.py`, novo helper de staging se necessário, testes de Run | 1a. Reidratar lojas puladas antes de consenso/final; fences e vínculos finais; crash não duplica snapshot/event/notificação. Legacy insuficiente não finaliza falsamente. |
| 1c — roster/progresso | `product_match_service.py`, `match_run_service.py`, `match_run_repository.py`, `match_run_worker.py`, testes de Run | 1a. Targets reais persistidos antes da busca, estados de início observáveis; reclaim mantém roster; stores_total correto durante Run. |
| Checkpoint — durabilidade | Testes unit e integration direcionados | 1b/1c. Outcomes antigos + novos chegam ao final com consenso equivalente; nenhuma regressão de concorrência C1. |
| 2a — read model live | `schemas.py`, `match_run_repository.py`, `match_run_serializers.py`, `match_run_service.py` | 1a/1c. Projeção sem logs, FX bounded, counts claros, sem writes. Unit/integration de serialização e SQL count. |
| 2b — endpoint e segurança | `router.py`, novo teste de API live, `docs/integration/pricescout.md` | 2a. Permissão/ownership, scope poll, 404/429, terminal/stale; contrato OpenAPI e payload budget. |
| 3 — client | PriceScout `utils/api/contracts.ts`, `services.ts`, `rate-limit-scope.ts` local, testes do client | 2b. Método `getMatchRunLive`, sinal, Decimal normalizado, scope correto. `npm run typecheck`, testes requests. |
| 4 — estado de observação | `hooks/useProductMatchRun.ts`, `utils/match-run.ts`, testes de match-run e helper de reducer proposto | 3. Um poll conhecido, stale recovery, terminal retido, cancel/generation, sem toast duplicado. Fake clock e requests controlados. |
| 5a — merge puro | Novo `utils/match-run-offers.ts` e teste, contracts de ofertas | 4. Dedupe ID/alias/URL, stable keys, tie ordering, FX indisponível, review separado. Node tests existentes via tsx. |
| 5b — UI integrada | Página `[id]/page.tsx`, `ProductMatchBanner.tsx`, novo componente de resultados/progresso, CSS se necessário | 5a. Cards chegam antes do terminal; nenhum loading global por polling; labels e acessibilidade. Browser smoke controlado. |
| 5c — handoff | Página, hook, mapper em `services.ts`, `MatchRunHistory.tsx` se necessário | 5b/1b. Background refresh preserva draft, troca parcial→persistido atômica, review não vira confirmação. Teste E2E de terminal/falha/reload. |
| 6 — decisão B | Documento de decisão posterior, sem código agora | Não bloqueia A. Só abrir implementação B com benefício de negócio, idempotência e testes de consenso definidos. |
| 7a — validação integrada | Novos testes E2E, harness/scripts de benchmark | 5c. Matriz abaixo, Docker, performance medida; usar ferramentas de browser existentes antes de adicionar runner/dependência. |
| 7b — contrato operacional | `docs/integration/pricescout.md`, `docs/matching/README.md`, `docs/performance.md`, ADR Proposed novo se aprovada regra durável | 7a. Review de documentação; atualizar pending com evidência e fechar somente após aceite. ADR 0036 permanece Accepted. |

Dividir 1a/1b em commits menores de schema, serialização e wiring se ultrapassarem cinco arquivos de produção. Contract backend antecede client; implementação da UI não redefine regra de domínio.

## 11. Testes obrigatórios

| Cenário isolado de teste | Resultado esperado |
|---|---|
| Loja rápida 5s, demais 120s | Resultado no próximo ciclo após commit, sem depender de terminal. |
| Duas lojas paralelas em instantes distintos | Duas inserções separadas; contadores não perdem update; sessão por operação. |
| F5 / sair e voltar | active+live ou fallback history+live recompõem resultados PostgreSQL, sem estado React anterior. |
| Lease expirada / worker restart | Não fingir execução ativa; recuperar resultados e retomar ID quando reclaim ocorre. |
| Reclaim após outcome e antes do catálogo | Hit reidratado participa do consenso e persistência; outcome terminal não resscrapeado. |
| Crash após flush e antes do commit final | Rollback de catálogo/links; attempt seguinte não duplica snapshot/event/notification. |
| Worker antigo retorna depois do reclaim | Fencing rejeita outcome/finalize e escrita no catálogo. |
| Review selecionado, reject, no_match, error | Review explicitamente em revisão; demais sem card; nenhum candidate SERP vira oferta. |
| Falha fatal após matches | Cards parciais preservados e sinalizados; catálogo anterior estável; history recuperável. |
| Terminal/GET produto atrasado ou falhando | Mantém observações; refresh limitado; sem sumiço ou duplicação; draft não perde edição. |
| URL normalizada, amazon_br→amazon/BR, listing existente | Uma linha por identidade; handoff via ID; nenhuma dedupe indevida por loja. |
| FX fresh/stale/unavailable; preço null/zero | Mesmo critério monetário atual; sem fabricar BRL; metadata coerente e ordem estável. |
| 429/Retry-After, foco, hidden, token/logout, troca produto | Um flight, cooldown correto, sem aplicar resposta tardia/sessão antiga. |
| Legado sem decisão/payload | Estado desconhecido seguro; sem elevar confiança por heurística. |
| Ownership e query count | Run alheia 404; sem candidate SELECT e sem relação lazy; ausência de secrets. |
| Resultado final sem crash | Listings, condições, GTIN e reviews equivalentes ao fluxo atual; apenas momento da UX muda. |

Unit: schema/serializer/reducer/merge/staging/idempotência. Integration PostgreSQL: commits realmente independentes, fencing e snapshot consistente; SQLite não comprova locks/isolamento de produção. E2E com dados explicitamente isolados de teste: fluxo visível, reload, navegação, terminal e fallback. Depois smoke real curto com lojas elegíveis, sem alterar capacidade Camoufox nem repetir toda coleta só para medir render.

Comandos futuros: `python -m pytest tests/unit/test_match_runs.py`, testes dirigidos novos, `make test`, `ruff check .`, `ruff format --check .`, `mypy src`; no PriceScout `npm run test:match-run`, `npm run test:requests` quando helpers locais incorporados, `npm run test:money`, `npm run typecheck`, lint e build. Executar via ambiente oficial Docker e reportar etapa/duração. Não executar suíte live indiscriminada.

## 12. Benchmark e metas propostas

Metas de aceite, **não medições**: 1/10/50 observadores com Run ativa, 10 min cada depois de warmup, distribuição de 4s com jitter; fixture de 9 stores apenas no harness isolado e caso adversarial com 40 candidates/store para provar exclusão dos logs. Separar usuários distintos de várias abas da mesma identidade. Simular commits espaçados e terminal; comparar live/status/details no mesmo ambiente/API+Postgres.

Medir queries por request (separar auth), SQL total/P95, conexões/locks, payload bruto e comprimido, API P50/P95/P99, requests/min, 429 por scope, renders/remounts por linha, commit→primeiro paint. Ferramentas: listener SQL/performance já existente, logs `timed`, client traces e React Profiler; reportar versão/ambiente/configuração, nunca tokens.

Budgets iniciais: até 3 queries de dados; zero SELECT de candidates; payload P95 até 20 KiB sem compressão com 9 lojas; API P95 até 250ms no ambiente integrado representativo; commit→paint P95 até 5s em página visível e rede saudável; zero 429 por fluxo normal com uma aba/identidade e notificações usuais; polling sem alteração não remonta linhas nem reordena ofertas. Com 50 observadores: 12,5 RPS nominais e até 37,5 queries/s de dados, excluindo auth; medir viabilidade, não assumir. Se falhar, investigar N+1/auth/pool/latência antes de reduzir frequência ou adicionar cache/ETag. Não rodar 50 browsers de scraping: carga de leitura e capacidade C1 são dimensões diferentes.

## 13. Riscos, rollout, rollback e documentação

Riscos principais: review confirmado indevidamente; staging com dado sensível; loss de hits em reclaim; contadores inconsistentes; aliases de loja; terminal apagando parcial antes do catálogo; global loading destruindo draft; polling herdando scope errado; conversão N+1; rows legadas e zumbis. As etapas e testes anteriores são condições de liberação, não opcionais silenciosos.

Rollout: migration aditiva nullable → worker/staging/recovery/roster → endpoint live → client/UI. Não ativar frontend antes do backend; validar compatibilidade com Runs abertas durante deploy. Fazer teste de crash com nova versão antes da UX. O endpoint antigo permanece para outros consumidores; details mantém seu papel. Se necessário rollback de UI por flag explícita centralizada, documentar env no README/.env.example/Compose/configuração aplicáveis; não introduzir flag sem necessidade de rollout demonstrada.

Rollback: desativar consumo live no frontend e voltar ao acompanhamento summary/refresh terminal; manter coluna/payload e leitura histórica, sem apagar resultados. API aditiva pode permanecer. Reverter worker que usa staging só após drenar Runs novas ou garantir compatibilidade; downgrade destrutivo de colunas não é primeiro recurso. Não reintroduzir SSE. Comparar resultado final e métricas antes/depois; nenhuma mudança de fetch, fingerprint, proxy, browser lifecycle ou capacidade é parte deste plano.

Documentation impact review concluído para esta **proposta**: conhecimento registrado aqui e rastreado em pending; contratos canônicos e ADRs Accepted não foram alterados para fingir feature implementada. Na implementação, atualizar integração, matching, performance e novo ADR de decisão se aprovado. Planejamento concluído; implementação e comprovação de runtime permanecem deliberadamente futuras, conforme pedido.

## Resposta objetiva

O PriceScout mostra cada correspondência automática lendo, por polling leve de uma Run conhecida, os `MatchStoreRun` já commitados pelo callback `on_store_outcome`. Faz merge estável de observações parciais na área de ofertas e só troca para listings após confirmar o catálogo final. PostgreSQL preserva resultados para reload/navegação; decisão explícita, roster e staging dos hits garantem progresso honesto e recuperação consistente após restart. Não é necessário SSE/WebSocket nem persistir definitivamente cada oferta antes do consenso.
