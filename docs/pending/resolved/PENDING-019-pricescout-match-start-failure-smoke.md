# PENDING-019 — Completar smoke de falhas no start do Product Match

- Status: RESOLVED
- Tipo: TESTING
- Prioridade: P2
- Área: frontend/product-match
- Origem: 2026-09-23 — investigação da confirmação entre PriceScout e ScoutApiV2
- Atualizado: 2026-09-25

## Contexto

O fluxo normal foi validado ao vivo: o clique recebeu `202 Accepted`, a Run foi
persistida e reclamada pelo `match-runner`, e o polling recuperou a Run após
reload. A matriz visual local agora cobre falhas de rede/HTTP, double-click,
start lento e renderização de `worker_lost` simulado. Ainda falta interromper o
processo real em uma Run descartável e observar a recuperação pela lease.

## Feito

- Smoke real com API + `match-runner`: POST 202, polling 200 e Run `running`
  com attempt 1, lease válida e atividade recente.
- Reload recupera a Run ativa e mantém o timer.
- `tests/unit/test_match_runs.py`: 21 testes passaram em 2026-09-25, incluindo
  start duplicado idempotente, lease/reclaim e semântica de Run ativa.
- `PriceScout/utils/match-run.test.ts`: 9 testes passaram, incluindo rejeição
  de resposta terminal, ausente ou de outro produto.
- Typecheck e ESLint direcionado do frontend passaram.
- Smoke visual inicial em `http://localhost:3000` numa sessão isolada: o
  `POST /products/{id}/match-runs` interceptado como `503 Service Unavailable`
  produziu um POST, restaurou o botão e não mostrou timer. Naquele primeiro
  teste o alerta não foi observado; a nova matriz abaixo confirmou o alerta
  genérico de indisponibilidade em `role=alert` (o client normaliza 5xx e não
  exibe o texto detalhado enviado pelo upstream).
- Inspeção read-only do código-fonte atual confirmou que o hook preenche
  `matchRun.error` e a página contém `<p role="alert">` para esse campo na
  seção de ofertas. O hook também limpava esse mesmo erro quando o polling
  seguinte retornava 200, o que podia remover o alerta poucos segundos após a
  falha de start. O PriceScout agora mantém erros de start separados dos erros
  de polling até uma nova tentativa ou start bem-sucedido.
- Regressão adicionada em `PriceScout/utils/match-run.test.ts`: falha de start
  permanece após polling bem-sucedido e é limpa ao iniciar nova tentativa.
  `npm run test:match-run`: 11 testes passaram; `npm run typecheck` e ESLint
  direcionado também passaram.
- Smoke visual adicional em Chrome headless com perfil temporário, usando a
  página de um produto real e interceptando todos os POSTs de start antes da
  API; nenhum `MatchRun` foi criado. HTTP 503 exibiu alerta, restaurou o botão
  e não mostrou contador. Double-click durante o start lento gerou um único
  POST. Falha de rede também exibiu alerta, restaurou o botão e não mostrou
  contador. Start aceito após 1,5 s manteve apenas a mensagem “solicitando” sem
  contador até a resposta 202; após a confirmação, o banner mostrou elapsed time
  e o botão permaneceu desabilitado.
- No mesmo smoke, a página recarregou uma Run ativa simulada, recebeu a transição
  para `worker_lost`, removeu o banner/contador, habilitou o botão e mostrou o
  aviso de busca interrompida. Naquele smoke, API e worker não foram
  interrompidos; o crash controlado está registrado na seção seguinte.
- A Run acidental `ddb744c1-2fac-48e6-a567-f8a97b3d894e` terminou normalmente
  às 19:36:17 UTC, com 10/11 lojas reportadas como concluídas; não foi
  interrompida.

## Smoke de recuperação após queda do worker (2026-09-25)

- Subi um Compose separado `codexpending019` com PostgreSQL e Redis próprios,
  API de desenvolvimento e `MATCH_RUN_TEST_INJECT_HANG=true`; a injeção impede
  qualquer scrape real.
- Criei produto e Run descartáveis apenas nesse banco. O worker assumiu a
  tentativa 1 (`running`, `attempts=1`) e registrou
  `match_run_test_inject_hang_armed`.
- Matei o container do worker com `SIGKILL`, iniciei outro processo e aguardei
  a lease de 60 s expirar. A mesma Run foi reclamada pelo novo processo com
  `attempts=2` e novo `claimed_at`; o log confirmou a segunda injeção de hang.
- Removi containers, rede e banco temporários com `docker compose down -v`.
  `docker ps -a` confirmou nenhum container `codexpending019`; o Compose
  principal permaneceu intacto e `/health` retornou API e banco `ok`.
- Junto da matriz Playwright isolada e dos testes de lease/reclaim já
  registrados acima, isso confirma a recuperação após interrupção abrupta sem
  criar estado em catálogo, Run ou ofertas do ambiente principal.

## Histórico da primeira tentativa

A primeira validação 503 não observou o alerta no browser. Uma tentativa do
harness usou `setTimeout`, indisponível no contexto do Playwright, e deixou uma
requisição seguir ao backend. A Run acidental
`ddb744c1-2fac-48e6-a567-f8a97b3d894e` concluiu normalmente às 19:36:17 UTC.
Naquele momento não houve interrupção de serviços e o Docker local não estava
acessível pelo harness; a matriz visual posterior e o crash isolado acima
completaram a cobertura.

## Impacto

O fluxo normal, as falhas HTTP/rede, deduplicação de double-click, timer após
confirmação, apresentação visual de `worker_lost` e reclaim real após queda do
worker foram verificados. O smoke de interrupção usou infraestrutura isolada e
descartável, sem scraping ou escrita no banco principal.

## Relacionado

- `PriceScout/hooks/useProductMatchRun.ts`
- `PriceScout/app/admin/produtos/[id]/page.tsx` — exibe `matchRun.error` em
  `role="alert"`; coberto pelo smoke Chrome headless isolado
- `PriceScout/utils/match-run.test.ts`
- `tests/unit/test_match_runs.py`
- `docs/adr/0036-persistent-product-match-runs.md`

## Pronto quando

Cada cenário de falha acima tiver teste automatizado ou smoke reproduzível,
com evidência de erro visível, timer consistente, deduplicação e recuperação
do estado após interrupção.
