# PENDING-025 — Validar recuperação Camoufox e Match multi-loja no ambiente integrado

- Status: RESOLVED
- Tipo: TESTING
- Prioridade original: P1
- Área: crawler/matching
- Origem: 2026-09-25 — investigação de falha no Product Match
- Resolvida: 2026-09-25

## Contexto

A análise encontrou disputa pelo mesmo profile persistente entre processos:
API e match-runner tentaram usar `direct/locale_pt_br`, e o lock distribuído
anterior cobria somente cada fetch. A correção mantém e renova `ProfileLock`
durante a sessão warm e libera o profile no fechamento/idle. O ciclo foi
validado no ambiente Docker com a revisão atual.

## Feito

- API e match-runner foram reconstruídos com o código atual e recriados;
  Postgres, Redis e os demais serviços permaneceram em execução.
- API ficou healthy; os dois serviços aplicaram Alembic até o head.
- O browser beta31 concluiu buscas na MatchRun sem `camoufox_launch_failed` ou
  `camoufox_circuit_open_skip`.
- Enquanto o match-runner detinha a lease do profile, um probe executado pelo
  container API tentou obtê-la por 1 s e recebeu `PROFILE_LOCK_TIMEOUT`; não
  lançou outro browser.
- Depois do idle/fechamento, não havia processo Camoufox nem chave
  `scout:v1:profile_lock:*` no Redis.
- MatchRun real `def653b3-443c-46f9-b430-4d20cbc98fd4`, para Apple iPhone 15
  128GB Rosa: concluída em 356,2 s; 3 matches (`kabum`, `magazineluiza`,
  `nissei`), 6 resultados sem match e 14 candidate logs persistidos com
  título/URL/decisão. Nissei ficou em review com confiança 0,89; Kabum em
  `auto_match` com confiança 0,92.
- Shopping China concluiu em 21,0 s com 20 candidatos encontrados. Amazon BR
  concluiu em 82,8 s e Magalu em 157,4 s. Não foi tratado HTML bloqueado como
  ausência de produto.
- Best Buy recebeu queries em inglês (`apple iphone 15`, `iphone 15`);
  Amazon US recebeu também a query localizada `apple iphone 15 128gb pink`.
  Amazon BR e lojas brasileiras mantiveram `rosa`.
- Visão VIP e AliExpress deram timeout de slot na wave paralela; executados
  depois individualmente pela API, ambos concluíram sem erro, com
  `persist=false`: Visão VIP em 20,6 s e AliExpress em 161,0 s, este com cinco
  buscas diretas e sem proxy.
- Testes direcionados previamente executados: 152 aprovados, 2 ignorados;
  Ruff e `git diff --check` passaram.
- O runtime observado estava com embeddings desligados; não há evidência de
  causalidade entre embeddings e o launch. O experimento de embeddings foi
  posteriormente concluído apenas em shadow local; `active` permanece fora do
  escopo e desabilitado.

## Resolução e limite identificado

A falha de lifecycle/colisão de profile foi corrigida e confirmada no
ambiente. Cada integração apontada como afetada executou em MatchRun ou no
smoke individual. A execução paralela ainda revelou `BROWSER_QUEUE_TIMEOUT`
em dois stores, embora não haja launch concorrente, falha de launch ou circuito
aberto. Esse problema distinto de fila C1 está aberto em
[PENDING-026](../PENDING-026-match-wave-c1-browser-queue-timeout.md).

## Relacionado

- [ADR 0044](../../adr/0044-camoufox-profile-lock-warm-session-lifecycle.md)
- [PENDING-024](PENDING-024-product-match-embeddings-shadow.md): experimento
  local de evidência shadow, encerrado sem habilitar `active`
- [PENDING-026](../PENDING-026-match-wave-c1-browser-queue-timeout.md): fila
  C1 na wave paralela
