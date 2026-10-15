# ADR-0054: Favoritos e alertas de queda com outbox de e-mail

- Status: Accepted
- Data: 2026-10-11

## Contexto

Usu?rios autenticados precisam salvar produtos e acompanhar redu??es reais de pre?o.
A captura de ofertas persiste snapshots e eventos comerciais no PostgreSQL; enviar
e-mail durante a atualiza??o da oferta aumentaria a lat?ncia e poderia perder avisos
se o processo falhasse ap?s o commit.

## Problema / decis?o necess?ria

Como vincular favoritos ao usu?rio, gerar avisos sem falsos positivos e entregar
e-mails de forma resiliente sem introduzir outro servi?o de filas?

## Alternativas consideradas

- Envio SMTP s?ncrono no refresh: rejeitado por acoplar disponibilidade/lat?ncia do
  SMTP ? coleta.
- Tarefa em mem?ria ap?s a resposta: rejeitada por n?o sobreviver a rein?cio.
- Broker externo: desnecess?rio para o volume/infraestrutura atual e adiciona opera??o.
- Outbox PostgreSQL com worker separado: adotado; usa a fonte de verdade existente.

## Decis?o

`ProductFavorite` e prefer?ncias pertencem ao usu?rio autenticado. O pre?o efetivo
usa PIX positivo quando dispon?vel, sen?o pre?o positivo e, por ?ltimo, pre?o
original positivo. S? um `price_changed` persistido, com oferta dispon?vel, mesma
moeda e redu??o estrita gera notifica??o. Remo??o/indisponibilidade, aumento,
igualdade, mudan?a de moeda e falha de scraping n?o geram alerta.

O fan-out grava `UserNotification` e `EmailDelivery` na transa??o do evento da
oferta. Uma constraint ?nica por favorito/evento torna a cria??o idempotente. Um
worker PostgreSQL separado reivindica linhas com lock e `SKIP LOCKED`, lease e
backoff limitado; SMTP acontece fora da transa??o. Credenciais e endere?o do
destinat?rio v?m da configura??o confi?vel, nunca do cliente. O worker usa TLS
verificado (STARTTLS ou SSL) e recusa autentica??o sem TLS.

Entrega SMTP ? **at-least-once**: uma queda ap?s o servidor aceitar o e-mail e
antes do commit local pode causar duplicata. Message-ID est?vel auxilia deduplica??o
no destino, mas n?o oferece exactly-once. Falhas transit?rias s?o retentadas at? o
limite configurado; falhas permanentes v?o para `failed`.

## Justificativa

O outbox fecha a janela entre persistir a queda e agendar a notifica??o. O worker
independente mant?m a coleta sem depend?ncia do SMTP e permite operar com Gmail
SMTP ou relay Workspace sem depender de um servi?o pago de fila.

## Consequ?ncias positivas

- Favoritos e notifica??es ficam isolados por usu?rio e exigem autentica??o.
- A gera??o do alerta ? at?mica com o evento de pre?o.
- O envio n?o bloqueia a coleta e pode ser retomado ap?s rein?cio.
- SMTP ? configur?vel e desligado por padr?o.

## Trade-offs / consequ?ncias negativas

- Entrega ? at-least-once, com pequena janela poss?vel de duplica??o.
- PostgreSQL recebe trabalho de polling e precisa de reten??o/monitoramento para
  estados `failed`.
- Gmail tem cotas por conta/plano; a taxa real depende da conta e do modo SMTP.

## Opera??o

Configure `EMAIL_ENABLED=true`, `EMAIL_FROM_ADDRESS`, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_STARTTLS`/`SMTP_SSL` e, conforme o servidor, `SMTP_USERNAME`/`SMTP_PASSWORD`.
Para Gmail pessoal, use senha de app ap?s habilitar verifica??o em duas etapas; para
Workspace, um administrador pode configurar SMTP relay. Nunca grave credenciais no
Git. `EMAIL_WORKER_ENABLED` controla o worker dedicado.

## Refer?ncias

- [ADR 0019 ? Product Matching e hist?rico de ofertas](0019-product-matching.md)
- [Persist?ncia PostgreSQL/Supabase](../persistence/supabase-postgres.md)
- [Alertas de queda de pre?o](../notifications/price-drop-alerts.md)
