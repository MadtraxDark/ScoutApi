# PENDING-031 — Implementar ofertas progressivas de Match Runs

- Status: RESOLVED
- Tipo: INCOMPLETE
- Prioridade: P2
- Área: matching/pricescout
- Origem: 2026-10-05 — planejamento e posterior autorização “pode implementar tudo”
- Atualizado: 2026-10-05

## Contexto

Plano e investigação: [tasks/plan.md](../../../tasks/plan.md). Contrato adotado no [ADR 0049](../../adr/0049-progressive-match-live-observations.md) e operação na [integração PriceScout](../../integration/pricescout.md). ADR 0036 permanece Accepted.

## Feito

- [x] Conferir hashes locais e main remoto de ambos os projetos.
- [x] Investigar os arquivos obrigatórios e distinguir alterações locais PriceScout.
- [x] Comparar alternativas e documentar recomendação de UX progressiva com polling.
- [x] Identificar ambiguidade review/match, roster ausente e recuperação incompleta de hits.
- [x] Definir contrato, fases, testes, metas e rollback.

## Checklist concluída

- [x] 0a: comprovar commit intermediário em PostgreSQL e falha de gravação.
- [x] 0b: caracterizar reclaim, consenso GTIN e fencing com testes dirigidos.
- [x] 1a: persistir staging sanitizado/decisão/contexto com migration compatível.
- [x] 1b: reidratar hits de attempts anteriores e reconciliar matched_listing_id no final.
- [x] 1c: registrar roster/progresso real das lojas.
- [x] Checkpoint: durabilidade/consenso/idempotência aprovados.
- [x] 2a: implementar read model live com projeção leve e FX bounded.
- [x] 2b: expor endpoint privado com visibility/rate scope e contrato documentado.
- [x] 3: adicionar client/contracts/scope no PriceScout preservando trabalho local.
- [x] 4: evoluir hook para um poll conhecido, recovery e snapshot terminal retido.
- [x] 5a: implementar merge puro, identificação e ordenação estáveis.
- [x] 5b: integrar cards parciais e progresso por loja na UI.
- [x] 5c: handoff terminal sem duplicação, perda de draft ou reviews confirmados.
- [x] Checkpoint: reload/navegação/falha/terminal funcionando no browser.
- [x] 6: registrar avaliação de B; manter adiada se nenhum benefício novo justificar.
- [x] 7a: executar E2E, benchmark de 1/10/50 observadores e smoke real delimitado.
- [x] 7b: atualizar documentação canônica, revisão final e instruções de rollout.

## Evidências de conclusão

71 testes dirigidos backend/PostgreSQL/segurança aprovados; migration upgrade,
downgrade e novo upgrade no PostgreSQL isolado. 54 testes frontend, typecheck,
lint direcionado e build aprovados. Chrome comprovou resultados de duas lojas,
review separado, reload, navegação, falha com resultado preservado, mobile e
handoff final sem perder rascunho. Smoke real: Pichau auto_match com listing
vinculado e KaBuM no_match, em 8,17s, acesso direto sem proxy pago.
Build Docker de API/worker e import OpenAPI/conexão ao PostgreSQL isolado
aprovados; containers existentes preservados.

Carga por 603,95s: grupos simultâneos 1/10/50, ciclos 4s, 9.211 HTTP 200,
P95 34,63/31,70/21,65ms; payload máximo 5.765 bytes. Memória estável em janela
de 200,76s (147,46–147,53 MiB), CPU 11% de um core. Dados de performance
registrados em [docs/performance.md](../../performance.md).

Na validação inicial havia três falhas Amazon e 37 erros mypy, registrados em
PENDING-028. A tarefa posterior corrigiu os checks globais (1090 testes backend,
77 frontend, Ruff/mypy/typecheck/lint verdes) e aplicou o rollout local:
nove Runs concluídas, workers parados durante a troca, migration 0032_match_live
antes dos processos novos, API saudável e workers reiniciados. Sem deploy público.
[Evidências atuais](../../performance/pending-recovery-2026-10-05.md).

## Critérios de conclusão

- Oferta automática aparece no próximo ciclo após commit sem esperar terminal.
- Run/resultados sobrevivem a reload/restart com recuperação consistente do catálogo final.
- Reviews nunca aparecem como confirmados; sem duplicação ou regressão de GTIN/concorrência.
- Testes, métricas e documentação previstos no plano aprovados; remover do índice ativo ao resolver.
