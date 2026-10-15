# PENDING-035: Valida??o live de alertas e SMTP Google

- Status: OPEN
- Tipo: TESTING
- Prioridade: P1
- Criado: 2026-10-11

## Objetivo

Validar o caminho persistente em staging: migration, queda detectada pelo monitor,
notification/outbox, worker, rein?cio e recebimento real pelo SMTP Google.

## Feito

- Implementa??o e testes unit?rios do dom?nio, fan-out idempotente e classifica??o
  de falhas SMTP.
- Worker usa lock/lease PostgreSQL e mant?m SMTP fora da transa??o de coleta.
- Configura??o local foi verificada somente quanto ? presen?a: n?o h?
  `EMAIL_ENABLED`, host, remetente, usu?rio ou senha SMTP no ambiente do agente.
- Pesquisa oficial consultada para App Password, SMTP Gmail/Workspace e limites;
  refer?ncias est?o no ADR 0054 e no documento de opera??o.

## Falta

- Aplicar upgrade/downgrade da migration em banco de staging.
- Validar entrega real com conta Google/Workspace autorizada e destinat?rio de
  controle; registrar modo SMTP, porta/TLS e recebimento sem salvar segredos.
- Reiniciar worker com outbox pendente e confirmar retomada.
- Medir dura??o de refresh antes/depois em ambiente representativo.

## Investiga??o

N?o h? configura??o SMTP no ambiente e n?o h? banco de staging indicado/configurado;
portanto nenhuma credencial foi inventada nem foi feita tentativa de conex?o.
Gmail pessoal requer verifica??o em duas etapas e App Password. Workspace pode usar
SMTP relay ap?s configura??o administrativa. Python `smtplib` suporta SMTP+STARTTLS
com contexto TLS verificado e timeout. Outbox PostgreSQL ? consistente com a fonte
de verdade e evita servi?o pago adicional. Ver [ADR 0054](../adr/0054-favorites-price-drop-email-outbox.md).

## Done when

- [ ] migration sobe e desce em staging.
- [ ] smoke SMTP confirma TLS, autentica??o, headers, conte?do multipart UTF-8, link e
  recebimento real; logs n?o cont?m segredo.
- [ ] delivery pending sobrevive ao restart do worker.
- [ ] benchmark documenta a lat?ncia do refresh e confirma aus?ncia de chamada SMTP
  s?ncrona.
