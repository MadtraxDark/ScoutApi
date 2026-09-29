# ADR-0044: ProfileLock acompanha a sessão Camoufox persistente

- Status: Accepted
- Data: 2026-09-25

## Contexto

O BrowserScheduler adquiria ProfileLock por fetch e o liberava ao terminar a
chamada, embora `CamoufoxHtmlFetcher` mantivesse a sessão persistente para
reutilização. Dois processos podiam então abrir o mesmo diretório de perfil ao
mesmo tempo. Em execução observada, API e match-runner usaram
`direct/locale_pt_br`; o match-runner esperou 45 s pelo Juggler enquanto a API
mantinha seu browser ativo.

## Decisão

- A lease global do perfil acompanha a vida da sessão warm, inclusive entre
  fetches. A vaga local do scheduler continua sendo devolvida após cada fetch.
- Leases Redis retidas têm renovação periódica com verificação de token. A
  sessão warm ociosa fecha em até 10 s, limitado pelo timeout de aquisição e
  pelo TTL, e libera o perfil. A chamada de navegação e o fechamento são
  serializados pelo owner do Playwright.
- Sem retenção confirmada (por exemplo, scheduler desabilitado ou coordenação
  fail-open), a sessão não permanece aberta após a chamada.
- Mantém-se `CAMOUFOX_BROWSER_CAPACITY=1` (C1); não se aumenta concorrência nem
  timeout de launch.

## Alternativas

- Lock apenas por fetch: rejeitado porque não protege o perfil enquanto o
  processo mantém browser/contexto persistente.
- Perfil diferente por processo: rejeitado porque permitiria múltiplos browsers
  e violaria a capacidade C1 validada.
- Lease retida sem renovação ou expiração ociosa: rejeitada por risco de lock
  órfão ou starvation.

## Consequências

O lock agora representa o proprietário efetivo do perfil, e o heartbeat
recupera leases expiradas sem permitir que outro processo use o perfil durante
uma sessão viva. A expiração ociosa possibilita handoff rápido. ADR 0039 segue
válida para C1, fila e slot; esta decisão especifica o ciclo de vida do
ProfileLock quando há sessão warm (ADR 0032).

## Validação e limites

Testes unitários cobrem renovação, exclusão entre schedulers e liberação após
fechamento/idle. A reprodução de loja ao vivo não foi repetida nesta alteração:
os containers existentes não foram reiniciados nem submetidos a novo browser.
O runtime beta31 observado tinha `MATCH_EMBEDDINGS_MODE=off`; portanto embeddings
não explicam a colisão de perfil observada.
