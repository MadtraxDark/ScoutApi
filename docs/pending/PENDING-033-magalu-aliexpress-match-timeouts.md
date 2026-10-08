# PENDING-033 — Fechar Product Match em Magalu e AliExpress

- Status: OPEN
- Tipo: INCOMPLETE
- Prioridade: P2
- Área: matching/magazineluiza, matching/aliexpress
- Origem: 2026-10-08 — Product Match real do MSI MAG Coreliquid A12 360
- Atualizado: 2026-10-08

## Contexto

Product Match identifica o MSI MAG Coreliquid A12 360 e mantém variantes de
modelo/tamanho separadas. Ainda falta obter uma conclusão terminal sem timeout
para Magalu e AliExpress: as Runs reais terminaram essas lojas com
`STORE_WALL_TIMEOUT`, então ausência de match nesses dois alvos não pode ser
tratada como ausência de produto.

## Feito

- Confirmado modelo `MAG CoreLiquid A12`, radiador 360 mm, ARGB e MPN
  `CLA12360`; catálogos também publicam `306-7ZWEM21-813` para o A12 360.
- Parser lida com título que apresenta `360mm` e compatibilidade Intel/AMD antes
  do modelo, extraindo `MAG Coreliquid A12` e radiador `360 mm`.
- MPNs diferentes não são conflito absoluto para coolers quando modelo/tamanho
  concordam; `A12 240` continua rejeitado contra `A12 360`.
- Match pelo modelo base é forte quando marca e modelo parseado coincidem;
  nenhum threshold foi reduzido.
- Run real `30346ea7-4e92-45d2-a530-d4d66bb05b3d`, worker com as correções
  carregadas: 11/11 lojas, 2 matches (Amazon BR/US), 2 refreshes inalterados
  (KaBuM/Pichau), 5 `no_match` e 2 erros `STORE_WALL_TIMEOUT`.
- Magalu retornou o candidato A12 360 com P/N `306-7ZWEM21-813`, rejeitou A13
  e encaminhou o A12 para scraping; o scrape falhou antes da decisão final.
  AliExpress avaliou três candidatos sem confirmar o A12.
- Regressões unitárias, suíte rápida e lint passaram.

## Falta

- Investigar por que os fetches/scrapes de Magalu e AliExpress excedem o
  orçamento de loja e obter estado terminal observável para esses alvos.
- Confirmar listing de Magalu se o PDP puder ser coletado; não inferir oferta,
  preço ou disponibilidade a partir de HTML de bloqueio.

## Por que não terminou

- Na Run `30346ea7-4e92-45d2-a530-d4d66bb05b3d`, Magalu terminou com
  `STORE_WALL_TIMEOUT` em 188341 ms e AliExpress em 180001 ms. Logs Magalu
  registraram `result=blocked`, tentativas de resolução de challenge e
  `match_candidate_scrape_failed`; o motivo específico do PDP não foi exposto.
  AliExpress avaliou candidatos não correspondentes antes do wall timeout.
- Não há evidência de hang do worker ou falha da Run inteira. Nenhum timeout foi
  aumentado.

## Investigação

### Causa conhecida

- Ambas as lojas excederam o wall timeout configurado. Magalu teve bloqueios
  upstream durante as tentativas; AliExpress gastou o orçamento avaliando
  candidatos sem correspondência.

### O que foi testado

- Quatro Runs reais locais, incluindo baseline e três após correções; a última
  Run é a referência para os resultados consolidados.
- A resolução de challenge existente foi executada; logs mostram tentativas
  `challenge_resolve_attempt`. Proxy permaneceu em fallback e não foi usado
  como primeira tentativa.
- A ladder manteve o budget atual de até cinco queries por loja.
- Testes cobrem prefixos, ordem dimensão/modelo, MPNs regionais, atributo de
  radiador ausente e rejeição explícita de 240 mm.

### Fontes consultadas

- Código de timeout/progresso: `MATCH_STORE_WALL_TIMEOUT_SECONDS`,
  `match_store_summary` e `match_candidate_scrape_failed` no worker.
- Contratos: `docs/crawler/contracts.md` e `docs/matching/README.md`.
- Regras operacionais: `.cursor/rules/captcha-challenge-resolution.mdc` e
  `.cursor/rules/proxy-cost-mode.mdc`.

### Alternativas avaliadas / descartadas

- Aumentar o wall timeout sem medir a etapa causal — descartado; pode alongar
  Runs sem resolver bloqueio.
- Tratar HTML bloqueado como `no_match` ou criar oferta com dados da SERP —
  descartado por semântica e precisão.
- Ativar proxy como primeira tentativa — descartado; contradiz Proxy Cost Mode.

### Condição para continuar

- Reproduzir os fluxos com logs de fetch/PDP suficientes para distinguir
  challenge, cooldown, profile lock e timeout de adapter; testar a alternativa
  apropriada sem alterar fetch/browser fora das exceções aceitas.

## Impacto

- Amazon BR e US confirmam match; KaBuM e Pichau preservam listings conhecidas.
- Em Magalu e AliExpress, preço/estoque e listing não tiveram confirmação final
  nesta Run.

## Relacionado

- `docs/crawler/contracts.md`
- `docs/matching/README.md`
- `docs/performance.md`
- ADR 0014, ADR 0017 e ADR 0018
- Run `30346ea7-4e92-45d2-a530-d4d66bb05b3d`

## Pronto quando

- Uma Run real com o worker atualizado terminar Magalu e AliExpress sem
  `STORE_WALL_TIMEOUT` ou erro opaco de scraping.
- A página do A12 360 em Magalu, se acessível, for coletada e pontuada pelo
  contrato de modelo base/radiador/MPN regional; variantes irmãs permanecem
  rejeitadas.
- Oferta inexistente ou inacessível continua sem ser fabricada.
