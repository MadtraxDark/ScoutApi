from pathlib import Path
p=Path('docs/performance/import-images-2026-10-05.md');s=p.read_text(encoding='utf-8');s=s.replace('O limite de 20 continua; não há benchmark de quantidade inválida acima desse limite.','O limite de 20 continua. Preview real retornou 25 URLs; PriceScout envia as primeiras 20 URLs únicas e informa explicitamente o limite/excedentes, evitando rejeitar o produto inteiro. Teste frontend cobre esse caso.');s=s.replace('A duração do preview real depende do crawler e não foi otimizada nem prometida por esta alteração.','Preview real Magazine Luiza: 61.304,5 ms frio (browser_fetch 61.132,6 ms), 51,0 ms em cache, 25 URLs. Catálogo e imagens permaneceram inalterados; chamadas de registro e upload foram instrumentadas para falhar se acionadas. Preview frio ainda não atende ao critério de rapidez: PENDING-030 registra a investigação de PERFORMANCE. Não foi alterado fetch, waits, fingerprint, proxy ou lifecycle.');p.write_text(s,encoding='utf-8')
p=Path('docs/pending/PENDING-030-cold-preview-browser-duration.md');p.write_text('''# PENDING-030 — Duração do preview frio Magazine Luiza

- Status: OPEN
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

- [Relatório da importação](../performance/import-images-2026-10-05.md)
- [Regras de performance](../../.cursor/rules/performance.mdc)
- ADR 0039 e ADR 0048.
''',encoding='utf-8')
p=Path('docs/pending/README.md');s=p.read_text(encoding='utf-8');i=s.find('## Resolvidas');s=s[:i]+'- [PENDING-030 — Preview frio Magazine Luiza](PENDING-030-cold-preview-browser-duration.md) — OPEN / PERFORMANCE / P2; browser_fetch de 61,1 s, cache de 51 ms.\n\n'+s[i:];p.write_text(s,encoding='utf-8')
p=Path('docs/integration/pricescout.md');s=p.read_text(encoding='utf-8');s+='\nA importação respeita o limite atual de 20 imagens por produto: preview com mais\nURLs envia as primeiras 20 URLs únicas e informa explicitamente a quantidade\nexcedente na mensagem de conclusão. Não aumenta o limite de persistência.\n';p.write_text(s,encoding='utf-8')
