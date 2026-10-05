from pathlib import Path

root = Path(r'C:\Users\Thyago\Documents\Visual Studio Code\PriceScout')
page = root / 'app/admin/page.tsx'
text = page.read_text(encoding='utf-8')
anchor = '  const matchedStore = useMemo(() => {'
text = text.replace(anchor, '''  const hasPendingProductImages = catalogProducts.some((product) => product.images.some((image) =>
    image.original_status === "pending" || image.original_status === "downloading"
    || image.optimized_status === "pending" || image.optimized_status === "processing",
  ));
  useEffect(() => {
    if (!hasPendingProductImages) return;
    const polling = startVisiblePolling(async (signal) => {
      const result = await catalogApi.listProducts({ signal });
      signal.throwIfAborted();
      setCatalogProducts(result.products);
    }, () => 3000, undefined, 3000);
    return () => polling.stop();
  }, [hasPendingProductImages]);

''' + anchor, 1)
page.write_text(text, encoding='utf-8')
services = root / 'utils/api/services.ts'
text = services.read_text(encoding='utf-8')
text = text.replace('async listProducts():', 'async listProducts(options: { signal?: AbortSignal } = {}):')
start = text.index('  async listProducts(')
end = text.index('\n  },', start)
text = text[:start] + text[start:end].replace('cache: "no-store",', 'cache: "no-store",\n      signal: options.signal,') + text[end:]
services.write_text(text, encoding='utf-8')
