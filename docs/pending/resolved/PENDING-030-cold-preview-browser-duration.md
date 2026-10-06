# PENDING-030 — Duração do preview frio Magazine Luiza

- Status: RESOLVED
- Resolvida: 2026-10-05

## Resolução final

Causa demonstrada por subestágios: warmup/networkidle aguardavam tráfego sem relação com oferta. Corrigida prontidão na homepage e PDP, preservando pausa configurada e resolução anti-bot. Budget escolhido pelo usuário: 15 s frio/1 s cache. Três amostras finais reais: 13.772,60/7.171,11/7.466,80 ms; cache 0,38/0,31/0,40 ms; 25 imagens e oferta válida em todas, sem proxy. Não representa P95 populacional.

[Evidências e limites](../../performance/pending-recovery-2026-10-05.md).

## Histórico anterior à resolução


- Tipo: PERFORMANCE
- Prioridade: P2
- Área: crawler/magazineluiza
- Origem: 2026-10-05 — validação da importação de imagens (ADR 0048)
- Atualizado: 2026-10-05

## Contexto

Importação de catálogo e referências foi desacoplada do processamento de imagens.
O critério adicional de preview rápido não se confirmou no fetch frio da loja.

## Performance

- Operação: ProductScrapeService.scrape, include_images=true, PDP pública real S25 Ultra.
- Duração: preview frio 61.304,5 ms; browser_fetch 61.132,6 ms; cache 51,0 ms.
- Budget reportado: browser_navigation 5.000 ms; severity=SLOW.
- Frequência: uma execução fria e uma em cache; insuficiente para P95 ou regressão.
- Impacto: espera antes da aprovação, independente do POST de cadastro.
- Causa delimitada: tempo concentrado no browser_fetch; decomposição interna ainda necessária.
- Evidência: slow_operation operation=browser_fetch category=browser_navigation duration_ms=61132.6 expected_ms=5000 severity=SLOW.
- Comando: Compose exec --user app, ProductScrapeService sobre listing real já aprovado.
- Arquivos: crawler/services/product_scrape_service.py e services/html_fetcher.py.
- Investigação: leitura de cache/guard/fetch/extract; coleta real retornou 25 imagens;
  instrumentação proibiu register_references/persist_approved/upload_bytes/register_saved;
  contagem de catálogo e imagens inalterada. Reexecução em cache eliminou a espera.
- Possíveis soluções: primeiro decompor launch, queue, navegação, settle e extração;
  comparar com baseline validado e repetir amostras respeitando cooldown/C1.
- Done condition: causa do tempo frio demonstrada e budget acordado validado em loja real.

## Feito

Preview sem efeitos no catálogo/Drive confirmado. Browser roda como app; Camoufox
0.5.6 e capacidade C1 preservados. Cadastro já responde sem esperar esse pipeline.

## Falta

Medir os subestágios e otimizar somente mediante evidência e autorização específica
para alterar o fluxo operacional do scraper, caso necessária.

## Por que permanece aberta

Esta alteração corrigiu a espera de importação/Drive/AVIF. O fetch/browser é imutável
por padrão conforme AGENTS.md; não houve atualização operacional disfarçada para
reduzir essa nova medição. Não é uma declaração de ausência de solução nem BLOCKED.

## Relacionado

- [Relatório da importação](../../performance/import-images-2026-10-05.md)
- [Regras de performance](../../../.cursor/rules/performance.mdc)
- ADR 0039 e ADR 0048.
