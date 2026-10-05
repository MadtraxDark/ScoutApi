from pathlib import Path
path = Path(r'C:\Users\Thyago\Documents\Visual Studio Code\PriceScout\utils\api\services.ts')
text = path.read_text(encoding='utf-8')
start = text.index('  async listProductImages(id: string, signal?: AbortSignal)')
end = text.index('  async addProductImage(', start)
text = text[:start] + text[end:]
text = text.replace('async listProductImages(id: string):', 'async listProductImages(id: string, signal?: AbortSignal):')
start = text.index('  async listProductImages(')
end = text.index('\n  },', start)
text = text[:start] + text[start:end].replace('{ cache: "no-store" }', '{ cache: "no-store", signal }') + text[end:]
path.write_text(text, encoding='utf-8')
