from pathlib import Path
p=Path('../PriceScout/utils/api/import-product.test.ts')
p.write_text('''import assert from "node:assert/strict";
import { it } from "node:test";

it("salva referências em um POST sem aguardar uploads de imagens", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000";
  const { api } = await import("./client");
  const { catalogApi } = await import("./services");
  const originalPost = api.post;
  const originalGet = api.get;
  const calls: Array<{ path: string; body: unknown }> = [];
  api.post = (async (path: string, body: unknown) => {
    calls.push({ path, body });
    assert.equal(path, "/products", "upload individual não pertence ao caminho crítico");
    return { created: true, product: {
      id: "saved-product", title: "Produto aprovado", attributes: {}, listings: [],
      images: [], created_at: "2026-10-05T00:00:00Z", updated_at: "2026-10-05T00:00:00Z",
    } };
  }) as typeof api.post;
  api.get = (async () => { throw new Error("GET adicional bloqueia a conclusão"); }) as typeof api.get;
  try {
    const result = await catalogApi.importProduct({
      name: "Produto aprovado", source: "magazineluiza", url: "https://www.magazineluiza.com.br/p/123",
      images: [{ url: "https://cdn.example/1.jpg" }, { url: " https://cdn.example/1.jpg " }, { url: "https://cdn.example/2.jpg" }],
    } as Parameters<typeof catalogApi.importProduct>[0]);
    assert.equal(result.product.id, "saved-product");
    assert.match(result.message ?? "", /segundo plano/);
    assert.equal(calls.length, 1);
    assert.deepEqual((calls[0].body as { images: unknown }).images, [
      { source_url: "https://cdn.example/1.jpg", position: 0, is_main: true },
      { source_url: "https://cdn.example/2.jpg", position: 1, is_main: false },
    ]);
  } finally {
    api.post = originalPost;
    api.get = originalGet;
  }
});
''', encoding='utf-8')

