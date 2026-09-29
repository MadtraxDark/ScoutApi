# PENDING-017 — Reativar Mercado Livre e Shopee no Product Match

- Status: RESOLVED
- Tipo: INCOMPLETE
- Prioridade: P1
- Área: matching / mercadolivre / shopee
- Origem: 2026-09-22 — exclusão temporária do fluxo “Buscar preço em outras lojas”
- Atualizado: 2026-09-25

## Encerramento de escopo (2026-09-25)

O usuário decidiu manter Mercado Livre e Shopee desativadas no Product Match e
remover suas pendências da lista ativa. Este arquivamento não significa que a
instabilidade de login foi corrigida. `match_enabled=false` permanece
deliberado nas duas lojas. Reabrir somente com novo pedido para reativação.

## Contexto

Mercado Livre e Shopee continuam implementadas (spider, search, crawl manual),
mas foram retiradas do conjunto **elegível** do Product Match automático via
`StoreConfig.match_enabled=false` + `match_disabled_reason="login instability"`.

Motivo: paredes de login / soft-auth / session-gate ainda instáveis no fluxo
de Match (SERP + candidatos), gerando custo alto e resultados ruins.

## Feito

- Exclusão centralizada e reversível em `stores.py` (sem `if store ==` espalhado).
- `eligible_match_store_keys()` / `ProductMatchService._resolve_stores` omitem
  essas lojas (não ERROR / NO_MATCH).
- `GET /stores` expõe `match_enabled` + `match_disabled_reason` para o FE.
- Spiders, docs de loja e testes de parser preservados.

## Falta

- Estabilizar auth bypass / sessão para Match em ML e Shopee (ADR 0017/0018).
- Validação live do Match incluindo as duas lojas.
- Reativar: `match_enabled=True` (e limpar `match_disabled_reason`) em
  `STORE_CONFIGS` para `mercadolivre` e `shopee`.
- Atualizar `docs/crawler/stores/*.md` e fechar esta pendência.

## Por que não terminou

- Escopo desta tarefa era excluir temporariamente do Match, não resolver o
  login wall completo das duas lojas.

## Impacto

- “Buscar preço em outras lojas” não compara ML/Shopee até a reativação.
- Crawl manual e Offer Refresh dessas URLs continuam disponíveis.

## Done when

- Login/session estável o suficiente para Match live.
- `match_enabled=True` nas duas lojas.
- Teste unitário de elegibilidade + smoke Match passam com as lojas no conjunto.
- Esta pendência removida do índice ativo.
