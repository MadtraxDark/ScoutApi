from pathlib import Path

root = Path(r'C:\Users\Thyago\Documents\Visual Studio Code\PriceScout')
services = root / 'utils/api/services.ts'
text = services.read_text(encoding='utf-8')
anchor = '  async addProductImage('
text = text.replace(anchor, '''  async listProductImages(id: string, signal?: AbortSignal): Promise<CatalogProductImage[]> {
    const result = await api.get<ApiProductImageListResponse>(
      `/products/${encodeURIComponent(id)}/images`, { signal, cache: "no-store" },
    );
    return (result.items ?? []).map(apiImageToCatalogImage);
  },

''' + anchor, 1)
services.write_text(text, encoding='utf-8')
page = root / 'app/admin/produtos/[id]/page.tsx'
text = page.read_text(encoding='utf-8')
anchor = '  const [matchHistoryKey, setMatchHistoryKey] = useState(0);'
text = text.replace(anchor, '''  const hasPendingImages = Boolean(product?.images.some((image) =>
    image.original_status === "pending" || image.original_status === "downloading"
    || image.optimized_status === "pending" || image.optimized_status === "processing",
  ));
  useEffect(() => {
    if (!id || !hasPendingImages) return;
    const polling = startVisiblePolling(async (signal) => {
      const gallery = await catalogApi.listProductImages(id, signal);
      signal.throwIfAborted();
      setImages(gallery.map(toGalleryImage));
      const main = gallery.find((image) => image.is_main) ?? gallery[0];
      const imageFields = {
        images: gallery,
        primary_image_id: main?.image_id ?? null,
        primary_image_url: main?.display_url ?? main?.original_url ?? null,
      };
      setProduct((current) => current ? { ...current, ...imageFields } : current);
      setDraft((current) => ({ ...current, ...imageFields }));
    }, () => 3000, undefined, 3000);
    return () => polling.stop();
  }, [id, hasPendingImages]);

''' + anchor, 1)
page.write_text(text, encoding='utf-8')
print('Adicionado polling leve da galeria enquanto imagens estão pendentes.')
