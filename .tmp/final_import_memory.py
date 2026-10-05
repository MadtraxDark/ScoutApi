from pathlib import Path
p=Path('../PriceScout/memory/working/importacao-imagens-background.md')
s=p.read_text(encoding='utf-8').replace('- in_progress: validar tipos, lint, API e navegação real.','- completed: TypeScript e 10 testes direcionados passaram; catálogo e galeria reais inspecionados.\n- completed: API e recovery validados em testes backend e benchmark com Drive real.\n- limitation: lint global contém set-state-in-effect preexistente no admin; acompanhar pendência canônica.').replace('Medições e recovery estão em validação no ScoutApiV2.','Medições e recovery estão documentados no ScoutApiV2 em docs/performance/import-images-2026-10-05.md.')
p.write_text(s,encoding='utf-8')
