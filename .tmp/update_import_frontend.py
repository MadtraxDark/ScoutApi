from pathlib import Path

root = Path(r'C:\Users\Thyago\Documents\Visual Studio Code\PriceScout')
services = root / 'utils/api/services.ts'
text = services.read_text(encoding='utf-8')
start = text.index('    const result = await api.post<ProductRegisterResponse>("/products", body);', text.index('  async importProduct('))
end = text.index('\n  },', start)
text = text[:start] + '''    body.images = Array.from(new Set((product.images ?? [])
      .map((image) => image.url?.trim())
      .filter((url): url is string => Boolean(url))))
      .map((source_url, position) => ({ source_url, position, is_main: position === 0 }));
    const result = await api.post<ProductRegisterResponse>("/products", body);
    return {
      product: productViewToCatalogProduct(result.product),
      message: body.images.length
        ? "Produto salvo. As imagens serão processadas em segundo plano."
        : result.created ? "Produto cadastrado." : "Produto reutilizado.",
    };''' + text[end:]
services.write_text(text, encoding='utf-8')
contracts = root / 'utils/api/contracts.ts'
text = contracts.read_text(encoding='utf-8')
start = text.index('export type ProductRegisterRequest = {')
text = text[:start] + text[start:].replace('  title: string;', '  images?: { source_url: string; position: number; is_main: boolean }[];\n  title: string;', 1)
contracts.write_text(text, encoding='utf-8')
page = root / 'app/admin/page.tsx'
text = page.read_text(encoding='utf-8').replace('Aguardando o processamento da API...', 'Salvando produto no catálogo...')
page.write_text(text, encoding='utf-8')
memory = root / 'memory/working/importacao-imagens-background.md'
memory.write_text('''---
id: mem_importacao_imagens_background_20261005
type: working
status: draft
confidence: 0.95
source: agent
sources: [utils/api/services.ts, app/admin/page.tsx]
created_at: 2026-10-05
updated_at: 2026-10-05
expires_at: null
related: []
---

Objetivo: concluir a importação após persistir produto e referências de imagens.

- completed: localizar espera sequencial em importProduct, após POST /products.
- completed: enviar imagens aprovadas no mesmo POST, sem aguardar uploads individuais.
- completed: informar processamento em segundo plano.
- in_progress: validar tipos, lint, API e navegação real.

A API registra pending e utiliza a fila PostgreSQL existente. A galeria recebe
display_url da origem enquanto o original é preservado; AVIF pronta tem prioridade.
Nenhum timeout foi aumentado. Medições e recovery estão em validação no ScoutApiV2.
''', encoding='utf-8')
print('Atualizados services.ts, contracts.ts, page.tsx e registro de trabalho.')
