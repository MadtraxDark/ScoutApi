# Alertas de queda de preço

## Contrato

Favoritos e preferências pertencem ao usuário autenticado. A API deriva dono e
destino da identidade autenticada; não aceita endereço arbitrário do cliente.
O e-mail do JWT é sincronizado com a preferência nas operações autenticadas.
Sem e-mail, o usuário pode favoritar e receber notificações internas.

A queda é calculada com Decimal: PIX positivo, senão preço regular positivo,
senão preço original positivo. Somente `price_changed`, com preço anterior e
novo positivos, oferta disponível, mesma moeda e redução estrita é elegível.
Cada favorito recebe no máximo uma notificação por evento.

## API

- `GET /favorites` lista os favoritos do principal autenticado.
- `PUT /products/{product_id}/favorite` cria ou mantém o favorito.
- `DELETE /products/{product_id}/favorite` remove de forma idempotente.
- `PATCH /products/{product_id}/favorite` atualiza `notify_price_drop`.

As rotas são privadas e sujeitas ao rate limit padrão. A listagem é paginada.

## Entrega de e-mail

A API persiste `EmailDelivery` junto ao evento e à `UserNotification`.
O serviço Compose `email-worker` processa a outbox com leases, locks PostgreSQL
`SKIP LOCKED`, retries com backoff e máximo configurável. SMTP roda fora da
transação de coleta. A entrega é at-least-once: pode haver duplicação se o
processo cair após aceitação pelo SMTP e antes de marcar `sent`. Falhas permanentes
ou esgotadas ficam `failed`. Logs usam IDs e códigos sanitizados.

Configure o transporte em `.env.example`; veja [ADR 0054](../adr/0054-favorites-price-drop-email-outbox.md).
Gmail pessoal requer verificação em duas etapas e App Password; Workspace relay
exige configuração administrativa. Limites variam por conta e modalidade.

## Validação

Em 2026-10-15, foi corrigida a ausência de `PUT` na allowlist de métodos CORS,
que rejeitava o preflight do botão de favorito. O teste de regressão reproduziu
`400` antes da correção e passou com `200` depois. Na API Docker atualizada,
o navegador confirmou criação (`PUT 200`), persistência após recarregar,
listagem em Favoritos, alteração do alerta (`PATCH 200`) persistida após
recarregar e remoção (`DELETE 204`). O favorito criado no teste foi removido
ao final; o console permaneceu sem erros. Segurança e favoritos: 44 testes
aprovados.

Os testes cobrem preço elegível, eventos não elegíveis, isolamento, idempotência,
preferências, fan-out e classificação de falhas SMTP. Smoke SMTP real, restart
live e benchmark permanecem em [PENDING-035](../pending/PENDING-035-favorites-email-live-validation.md).
