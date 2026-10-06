# PENDING-026 — Evitar timeout de fila do browser na wave paralela C1

- Status: RESOLVED
- Tipo: BUG
- Prioridade: P1
- Área: matching/crawler
- Origem: 2026-09-25 — smoke live PENDING-025
- Resolvida: 2026-09-25

## Contexto

O Product Match integrado executou com `CAMOUFOX_BROWSER_CAPACITY=1` e não
apresentou launch failure. Porém a wave paralela com
`MATCH_STORE_CONCURRENCY=3` produziu `BROWSER_QUEUE_TIMEOUT` após 60 s em
Visão VIP e AliExpress enquanto uma busca Camoufox longa ocupava o slot C1.
Esses stores concluíram sem erro quando chamados individualmente depois da
Run. O profile lock impediu um segundo processo de lançar browser no mesmo
profile; o erro observado foi espera do slot local, antes de iniciar navegação.

## Evidência

- MatchRun `def653b3-443c-46f9-b430-4d20cbc98fd4`, produto Apple iPhone 15
  Rosa, revisão atual em Docker: Run completada em 356,2 s.
- Resultados detalhados: Visão VIP `BROWSER_QUEUE_TIMEOUT` em 60,0 s;
  AliExpress `BROWSER_QUEUE_TIMEOUT` em 60,0 s; os outros stores terminaram.
- Visão VIP isolada: 20,6 s, sem erro, rejects coerentes de iPhone 15 Pro.
- AliExpress isolado: 161,0 s, cinco buscas concluídas, sem erro e sem proxy.
- Configuração observada: browser capacity C1, timeout da fila 60 s,
  concorrência entre stores 3.
- Segunda observação integrada em 2026-09-25 durante a Run
  `ddb744c1-2fac-48e6-a567-f8a97b3d894e`: não houve `BROWSER_QUEUE_TIMEOUT`, mas
  AliExpress e Visão VIP terminaram com `STORE_WALL_TIMEOUT` após 200,1 s e
  203,7 s. A Run concluiu em 326,7 s; demais nove lojas concluíram. Portanto,
  aumentar a espera da fila ou renomear o erro não seria resolução: o limite
  efetivo de loja também não está encerrando a espera no prazo configurado.

## Feito

- `ProductMatchService` propaga o deadline monotônico da loja ao contexto das
  chamadas de busca e scrape, incluindo browser-posts executados dentro da
  busca. O timeout padrão da fila continua valendo para outros callers; no
  Match, a fila pode usar o orçamento restante da loja, sem ultrapassá-lo.
  Isso evita que vários timeouts independentes de 60 s somem além do limite de
  uma única loja.
- Quando a fila esgota o deadline, ela remove o waiter e não concede o slot
  depois. `ProductMatchService` traduz o `RequestError` recebido após o prazo
  em `STORE_WALL_TIMEOUT`.
- O fluxo HTTP-first continua concorrente: o contexto só afeta acquires do
  `BrowserScheduler`, portanto requisições que não usam browser não ficam
  serializadas por esta alteração.
- Testes unitários cobrem espera maior que o timeout padrão, expiração sem
  concessão tardia do slot e classificação como `STORE_WALL_TIMEOUT` após busca.

## Limites conhecidos

- Operações Camoufox já iniciadas e outros I/O bloqueantes não são interrompidos
  por este deadline; essa limitação não altera a correção da espera enfileirada.

## Impacto

Uma MatchRun paralela não falha mais por timeout fixo da fila de 60 s: a espera
está limitada pelo deadline da loja. A observação inicial de AliExpress e Visão
VIP em `STORE_WALL_TIMEOUT` foi investigada e corrigida em PENDING-027. C1
permanece validado; não aumente a capacidade de browser sem benchmark que
satisfaça ADR 0039.

## Relacionado

- ADR 0032: waves de Product Match e `MATCH_STORE_CONCURRENCY`
- ADR 0039: capacidade C1 do browser
- ADR 0044: lease do profile durante a sessão Camoufox
- `src/scout_api/modules/matching/product_match_service.py`
- `src/scout_api/modules/crawler/core/browser_scheduler.py`
- `src/scout_api/core/config.py`

## Pronto quando

- Uma MatchRun live em C1 confirma que stores browser-bound na wave paralela
  não expiram prematuramente em `BROWSER_QUEUE_TIMEOUT` e que o tempo de fila
  respeita `match_store_wall_timeout_seconds`.
- A solução preserva C1, mantém o timeout de launch de 45 s e conserva
  sobreposição para operações que não ocupam o browser.
- Testes demonstram concorrência, cancelamento na expiração do orçamento da loja
  e limite de tempo da fila.

## Resolução

- MatchRun live `f295d9e5-05e5-439f-a823-533fd86bb21d`: 11 stores, 336,7 s,
  sem `BROWSER_QUEUE_TIMEOUT`; AliExpress e Visão VIP encerraram com
  `STORE_WALL_TIMEOUT` em ~180,2 s. A causa residual foi corrigida e validada
  depois em PENDING-027.
- 14 testes direcionados aprovados; Ruff check e format check passaram.
- Mantidos C1, fila padrão de 60 s e launch timeout de 45 s.
