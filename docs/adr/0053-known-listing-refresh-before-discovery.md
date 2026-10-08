# ADR-0053: Refresh de listings conhecidas antes do discovery

- Status: Accepted
- Data: 2026-10-07

## Contexto

Product Match usa uma referência sintética quando a identidade do produto já
está no catálogo. Nesse caminho, a loja da `reference_url` não está disponível
no item de referência e a exclusão da loja de origem não se aplica. O resolvedor
mantinha todas as lojas elegíveis em discovery, mesmo quando o produto já tinha
uma `StoreListing` ativa naquela loja. Uma SERP vazia podia então exibir
`no_match` junto de uma oferta persistida válida.

O sistema já separa Store Search/PDP (ADR 0038) e possui `OfferRefreshService`
com snapshots, eventos comerciais e integração com o agendamento do monitor.

## Decisão

Após resolver os targets elegíveis, particionar por listings ativas do mesmo
produto canônico:

- Loja com uma ou mais listings ativas: atualizar todas pela URL persistida
  usando `OfferRefreshService`, e excluir essa loja do discovery da Run.
- Loja sem listing ativa: manter discovery por Store Search e matcher.

Uma loja tem uma linha agregada no progresso. Os estados `refresh_updated`,
`refresh_unchanged`, `refresh_out_of_stock`, `refresh_removed`, `refresh_failed`
e `refresh_deferred` distinguem atualização conhecida de `no_match`, reservado
à descoberta sem candidato correspondente. Timeout, WAF e erro transitório
preservam a listing e não causam rediscovery imediato. Uma URL definitivamente
removida pode ser redescoberta na próxima Run, após deixar de ser ativa.

O `OfferRefreshService` serializa refreshes com row lock e respeita claims do
monitor. Refreshes iniciados pelo Match Run salvam snapshot/eventos e outcome da
loja na mesma transação; resultados terminais são reconhecidos no reclaim.
Snapshots sem alteração continuam seguindo a política já existente do serviço.

## Alternativas consideradas

- Excluir somente a loja da `reference_url`: falha com identidade sintética e
  não atende listings descobertas em Runs anteriores.
- Usar `skip_stores` para qualquer listing existente sem atualizar a oferta:
  preserva a SERP, mas deixa preços e disponibilidade obsoletos.
- Fazer refresh dentro de Product Match: duplicaria scraping, diff, histórico e
  hooks já fornecidos por `OfferRefreshService`.
- Rediscover após qualquer erro do refresh: transforma falhas temporárias em
  tráfego e falsos `no_match`.

## Consequências

- Uma relação produto/loja já aprendida deixa de consumir queries/SERP em Runs
  seguintes e a URL persistida passa a ser o caminho de atualização.
- A linha de progresso representa refresh e discovery; estados terminais de
  refresh entram no total concluído, mas não em `no_matches`.
- Mais de uma listing ativa por loja gera mais de um refresh e um outcome
  agregado. Se uma coleta falha, a linha informa falha sem apagar as demais.
- Validação live em 2026-10-07 com o produto da captura (water cooler MSI,
  KaBuM): a Run anterior fez 4 queries de SERP e terminou em `no_match` apesar
  da listing ativa. A Run corrigida atualizou a PDP persistida sem SERP, retornou
  `refresh_unchanged` e preservou os preços/seller; a linha da loja caiu de
  3.846 ms para 825 ms (78,5%, 3.021 ms economizados). Foram evitadas 4 queries
  e um falso `no_match` nessa loja. O total da Run variou de 373.476 ms para
  389.335 ms, portanto esta amostra não demonstra redução do tempo total: as
  outras dez lojas continuaram em discovery e tiveram variação normal. Os
  registros históricos não armazenavam uso de browser (`browser_used=NULL`),
  então não há delta confiável de navegações históricas; na atualização nova
  não houve `browser_fetch` para KaBuM.

## Referências

- [ADR 0036 — Product Match persistente](0036-persistent-product-match-runs.md)
- [ADR 0038 — PDP e Store Search separados](0038-pdp-search-capability-split.md)
- [Contrato de Matching](../matching/README.md)
