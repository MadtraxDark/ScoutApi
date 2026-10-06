import { api, ApiError, apiUrl, hasApiConfiguration } from "@/utils/api/client";
import {
  apiImageToCatalogImage,
  pickPrimaryImageUrl,
  resolveMediaUrl,
  toGalleryProductImage,
} from "@/utils/api/product-images";
import { clearAccessToken, setAccessToken } from "@/utils/api/auth-token";
import { getStoreDisplayName } from "@/utils/store-display";
import type {
  AuthSession,
  CatalogOffer,
  CatalogOverview,
  CatalogProduct,
  CatalogProductImage,
  CatalogProductVariant,
  CatalogStore,
  MatchHit,
  MatchRequest,
  MatchResponse,
  MatchRunDetailView,
  MatchRunLiveView,
  MatchRunListResponse,
  MatchRunStatusView,
  NotificationListResponse,
  NotificationView,
  OfferRefreshRequest,
  OfferRefreshResponse,
  OtherStorePricesResponse,
  OtherStorePrice,
  ApiProductImageListResponse,
  ApiProductImageView,
  ProductImage,
  ProductListResponse,
  ProductPriceItem,
  ProductPreview,
  ProductPreviewResponse,
  ProductRegisterRequest,
  ProductRegisterResponse,
  ProductView,
  PublicUser,
  StoreInfo,
  UnreadCountResponse,
} from "@/utils/api/contracts";
import { parseMoney } from "@/utils/api/contracts";

// =============================================================================
// Tipos exportados para as páginas
// =============================================================================

export interface AuthUserResponse {
  user: {
    id: string;
    name: string | null;
    email: string | null;
  };
  /** Sempre false — admin UI é auth-only. Nunca use is_admin || true. */
  is_admin: boolean;
}

/** Mantido para compatibilidade com imports existentes. */
export interface AuthSessionData {
  success: boolean;
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: { id: string; email: string; name?: string | null };
  error?: string | null;
}

// =============================================================================
// Mappers — ProductView → CatalogProduct
// =============================================================================

function listingToCatalogOffer(
  listing: ProductView["listings"][number],
  productId: string,
  index: number,
): CatalogOffer {
  const toNumber = (value: string | number | null | undefined): number | null => {
    if (value == null || value === "") return null;
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const price = toNumber(listing.price);
  const pix = toNumber(listing.pix_price);
  const original = toNumber(listing.original_price);
  const promoActive = Boolean(listing.promotion_commercially_active);
  const storeKey = listing.store;
  return {
    id: listing.id ?? `${productId}-listing-${index}`,
    catalog_product_variant_id: productId,
    store_id: storeKey,
    source: storeKey,
    status: listing.status,
    match_decision: listing.match_decision,
    external_product_id: listing.product_id,
    canonical_url: listing.canonical_url,
    seller: listing.seller ?? null,
    delivery_responsible: null,
    original_price: original,
    pix_price: pix,
    card_price: price,
    discount_percent: null,
    installments: null,
    installment_value: null,
    interest_free: null,
    image_url: null,
    currency: listing.currency || "BRL",
    country: listing.country,
    market: listing.country,
    store: {
      id: storeKey,
      name: getStoreDisplayName(storeKey, listing.canonical_url || listing.url),
      hostname: "",
    },
    history: [],
    last_checked_at: listing.last_checked_at ?? null,
    next_check_at: listing.next_check_at ?? null,
    promotion_expires_at: promoActive ? (listing.promotion_expires_at ?? null) : null,
    promotion_status: listing.promotion_status ?? null,
    promotion_commercially_active: promoActive,
    available: listing.available ?? null,
    converted_price_brl: toNumber(listing.converted_price_brl),
    exchange_rate: toNumber(listing.exchange_rate),
    exchange_rate_type: listing.exchange_rate_type ?? null,
    exchange_rate_status: listing.exchange_rate_status ?? null,
    exchange_rate_source: listing.exchange_rate_source ?? null,
    exchange_rate_updated_at: listing.exchange_rate_updated_at ?? null,
  };
}

function productViewToCatalogProduct(
  view: ProductView,
  gallery: CatalogProductImage[] = [],
): CatalogProduct {
  const categoryAttr = view.attributes?.category;
  const category = typeof categoryAttr === "string" ? categoryAttr : null;

  const offers: CatalogOffer[] = view.listings.map((listing, idx) =>
    listingToCatalogOffer(listing, view.id, idx),
  );

  const variant: CatalogProductVariant = {
    id: view.id,
    catalog_product_id: view.id,
    attributes: Object.fromEntries(
      Object.entries(view.attributes ?? {})
        .filter(([, v]) => v !== null && v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    ),
    variant_fingerprint: view.variant_key ?? "",
    identity_status: "confirmed",
    offers,
  };

  const firstListing = view.listings[0] ?? null;
  const fromApi = (view.images ?? []).map(apiImageToCatalogImage);
  const resolvedGallery = gallery.length > 0 ? gallery : fromApi;
  const primaryUrl = pickPrimaryImageUrl(view.primary_image_url, resolvedGallery);
  const mainImage = resolvedGallery.find((image) => image.is_main) ?? resolvedGallery[0] ?? null;

  return {
    id: view.id,
    canonical_name: view.title,
    brand: view.brand,
    model: view.model,
    category,
    identity_attributes: view.attributes ?? {},
    identity_status: "confirmed",
    variants: [variant],
    images: resolvedGallery,
    created_at: view.created_at,
    updated_at: view.updated_at,
    name: view.title,
    product_url: firstListing?.canonical_url ?? "",
    source: firstListing?.store ?? "",
    external_product_id: firstListing?.product_id ?? "",
    primary_image_url: primaryUrl,
    primary_image_status: view.primary_image_status,
    primary_image_error_code: view.primary_image_error_code,
    primary_image_retryable: view.primary_image_retryable,
    primary_image_id: mainImage?.image_id ?? null,
    store: firstListing ? { name: firstListing.store, hostname: "" } : null,
  };
}

function storeInfoToCatalogStore(store: StoreInfo): CatalogStore {
  return {
    id: store.key,
    name: getStoreDisplayName(store.key, null, store.display_name),
    hostname: store.domains[0] ?? "",
    url: store.domains[0] ? `https://${store.domains[0]}` : `#${store.key}`,
    country: store.country,
    currency: store.currency,
    supported_country_currency_pairs: store.supported_country_currency_pairs,
    logo_svg: store.logo_svg ?? null,
    logo_url: resolveMediaUrl(store.logo_url),
    logo_processing_status: store.logo_processing_status ?? "ready",
    productCount: 0,
    updated_at: new Date().toISOString(),
    domains: store.domains,
    supports_images: store.supports_images,
    supports_search: store.supports_search,
    implemented: store.implemented,
    match_enabled: store.match_enabled ?? true,
    match_disabled_reason: store.match_disabled_reason ?? null,
    image_fetch_cost: store.image_fetch_cost ?? (store.supports_images ? "low" : "unsupported"),
    default_include_images:
      store.default_include_images ??
      (store.supports_images && (store.image_fetch_cost ?? "low") === "low"),
  };
}

// =============================================================================
// Mappers — ProductPriceItem → ProductPreview
// =============================================================================

function productPriceItemToPreview(item: ProductPriceItem, sourceUrl: string): ProductPreview {
  const original = parseMoney(item.original_price);
  const pix = parseMoney(item.pix_price);
  const card = parseMoney(item.price);

  const pixDiscountPercent =
    pix !== null && original !== null && original > 0
      ? Math.round(((original - pix) / original) * 100)
      : parseMoney(item.discount_percentage);

  const images = (item.images ?? [])
    .filter((img): img is string => typeof img === "string" && img.trim() !== "")
    .map((url) => ({ url, alt: null, width: null, height: null }));

  const installments =
    item.installment_count != null && item.installment_price != null
      ? {
          quantity: item.installment_count,
          amount: parseMoney(item.installment_price) ?? 0,
          total:
            (parseMoney(item.installment_price) ?? 0) * item.installment_count,
          interestFree: null as boolean | null,
        }
      : null;

  return {
    source: item.store,
    url: item.canonical_url || item.url || sourceUrl,
    productId: item.product_id,
    sourceProductId: item.sku,
    name: item.title,
    brand: item.brand,
    model: item.model,
    variant: item.variant,
    country: item.country,
    currency: item.currency,
    gtin: item.gtin,
    gtin_type: null,
    gtin_confidence: null,
    gtin_source: null,
    mpn: null,
    originalPrice: original,
    pixPrice: pix,
    pixDiscountPercent,
    cardPrice: card,
    installments,
    priceDifferencePixToCard: pix !== null && card !== null ? card - pix : null,
    seller: item.seller,
    deliveryResponsible: null,
    special_status: null,
    shipping_from: null,
    taxonomy: [],
    main_image: images[0]?.url ?? null,
    images,
    image_status: (() => {
      const meta = (item as { metadata?: Record<string, unknown> }).metadata;
      const status = meta?.image_status;
      if (
        status === "success" ||
        status === "empty" ||
        status === "omitted" ||
        status === "error" ||
        status === "partial"
      ) {
        return status;
      }
      if (images.length > 0) return "success";
      return null;
    })(),
    image_error: (() => {
      const meta = (item as { metadata?: Record<string, unknown> }).metadata;
      const err = meta?.image_error;
      return typeof err === "string" ? err : null;
    })(),
  };
}

// =============================================================================
// Mappers — MatchResponse → OtherStorePricesResponse
// =============================================================================

function matchHitToOtherStorePrice(hit: MatchHit): OtherStorePrice {
  const product = hit.product;
  const original = parseMoney(product.original_price);
  const pix = parseMoney(product.pix_price);
  const card = parseMoney(product.price);

  const pixDiscountPercent =
    pix !== null && original !== null && original > 0
      ? Math.round(((original - pix) / original) * 100)
      : parseMoney(product.discount_percentage);

  const installments =
    product.installment_count != null && product.installment_price != null
      ? {
          quantity: product.installment_count,
          amount: parseMoney(product.installment_price) ?? 0,
          total:
            (parseMoney(product.installment_price) ?? 0) * product.installment_count,
          interestFree: null as boolean | null,
        }
      : null;

  return {
    storeId: hit.store,
    displayName: getStoreDisplayName(
      hit.store,
      product.canonical_url || product.url,
      hit.store_display_name,
    ),
    url: product.canonical_url || product.url,
    currency: product.currency,
    originalPrice: original,
    pixDiscountPercent,
    pixPrice: pix,
    cardPrice: card,
    installments,
    seller: product.seller ?? null,
    deliveryResponsible: null,
  };
}

function matchResponseToOtherStorePrices(match: MatchResponse): OtherStorePricesResponse {
  const diagnostics: Array<Record<string, unknown>> = [
    ...match.errors.map((e) => ({ ...e, status: "error" })),
    ...match.unmatched_stores.map((store) => ({
      store,
      status: "unmatched",
    })),
  ];
  const priced = match.matches.filter(
    (hit) => hit.decision === "auto_match" || hit.decision === "review",
  );
  return {
    prices: priced.map(matchHitToOtherStorePrice),
    diagnostics,
  };
}

/**
 * Escolhe URL de referência confiável para Product Match.
 * Nunca usa variants[0]/offers[0] cegamente.
 */
export function selectReferenceUrl(product: CatalogProduct): string {
  const offers = product.variants.flatMap((variant) => variant.offers);
  const scored = offers
    .map((offer) => {
      const url = (offer.canonical_url || "").trim();
      if (!url) return null;
      let score = 10;
      if (offer.available !== false) score += 5;
      if (
        offer.pix_price != null ||
        offer.card_price != null ||
        offer.original_price != null
      ) {
        score += 4;
      }
      if (product.source && offer.store_id === product.source) score += 3;
      if (offer.promotion_commercially_active) score += 1;
      return { url, score };
    })
    .filter((row): row is { url: string; score: number } => row != null)
    .sort((left, right) => right.score - left.score);

  const best = scored[0]?.url || (product.product_url || "").trim();
  if (!best) {
    throw new Error("O produto nao possui URL de referencia para busca.");
  }
  return best;
}

function moneyOrUndefined(
  value: number | null | undefined,
): string | undefined {
  if (value == null || !Number.isFinite(value) || value <= 0) return undefined;
  return value.toFixed(2);
}

// =============================================================================
// authApi
// =============================================================================

export const authApi = {
  /**
   * Inicia o fluxo OAuth Google.
   * GET /auth/google → {authorization_url} → redireciona o browser.
   */
  async startGoogleLogin(_nextPath?: string): Promise<string> {
    if (!hasApiConfiguration()) throw new Error("Conexao com a API nao configurada.");
    const result = await api.get<{ authorization_url: string }>("/auth/google");
    return result.authorization_url;
  },

  /** URL imediata para OAuth (sem fetch, para uso em href). */
  signInUrl(_nextPath: string): string | null {
    if (!hasApiConfiguration()) return null;
    try {
      return apiUrl("/auth/google");
    } catch {
      return null;
    }
  },

  /**
   * Lê o access_token do fragment da URL, armazena em memória e limpa o hash.
   * Deve ser chamado antes de me() na página de callback.
   */
  captureAccessTokenFromHash(): string | null {
    if (typeof window === "undefined") return null;
    const hash = window.location.hash.substring(1);
    const params = new URLSearchParams(hash);
    const token = params.get("access_token");
    if (token) {
      setAccessToken(token);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      return token;
    }
    return null;
  },

  /** GET /auth/me — confirma identidade. Requer Bearer. */
  async me(options: { signal?: AbortSignal } = {}): Promise<AuthUserResponse> {
    const user = await api.get<PublicUser>("/auth/me", options);
    return {
      user: { id: user.id, name: user.display_name, email: null },
      is_admin: false,
    };
  },

  /** POST /auth/refresh — renova access token via cookie HttpOnly. */
  async refreshSession(): Promise<AuthSession> {
    const result = await api.post<AuthSession>("/auth/refresh");
    if (result.access_token) setAccessToken(result.access_token);
    return result;
  },

  /** POST /auth/logout → 204. Limpa token em memória. */
  async signOut(): Promise<void> {
    clearAccessToken();
    try {
      await api.post<void>("/auth/logout");
    } catch {
      // Ignorar erros de logout — sessão local já invalidada
    }
  },
};

// =============================================================================
// catalogApi — façade com os mesmos nomes de métodos usados pelas páginas
// =============================================================================

export const catalogApi = {
  // -------------------------------------------------------------------------
  // Visão geral — derivada de products + stores
  // -------------------------------------------------------------------------
  async getOverview(): Promise<CatalogOverview> {
    const [productsResult, storesResult] = await Promise.all([
      this.listProducts(),
      this.listStores(),
    ]);
    const products = productsResult.products;
    const stores = storesResult.stores;
    const allOffers = products.flatMap((p) => p.variants.flatMap((v) => v.offers));

    return {
      summary: {
        products: products.length,
        variants: products.reduce((sum, p) => sum + p.variants.length, 0),
        offers: allOffers.length,
        stores: stores.length,
      },
      recent_products: products.slice(0, 10).map((p) => ({
        id: p.id,
        name: p.name || p.canonical_name,
        brand: p.brand,
        created_at: p.created_at,
        image_url: p.primary_image_url ?? null,
        variants: p.variants.length,
        offers: p.variants.reduce((sum, v) => sum + v.offers.length, 0),
      })),
      single_offer_variants: [],
      best_price_opportunities: [],
      alerts: [],
      recent_activity: [],
    };
  },

  // -------------------------------------------------------------------------
  // Listagem de produtos — GET /products
  // -------------------------------------------------------------------------
  async listProducts(options: { signal?: AbortSignal } = {}): Promise<{ products: CatalogProduct[] }> {
    const result = await api.get<ProductListResponse>("/products", {
      query: { limit: 100, offset: 0 },
      cache: "no-store",
      signal: options.signal,
    } as Parameters<typeof api.get>[1]);
    return { products: (result.items ?? []).map((view) => productViewToCatalogProduct(view)) };
  },

  // -------------------------------------------------------------------------
  // Produto individual — GET /products/{id} + galeria
  // -------------------------------------------------------------------------
  async getProduct(id: string): Promise<CatalogProduct> {
    const [view, gallery] = await Promise.all([
      api.get<ProductView>(
        `/products/${encodeURIComponent(id)}`,
        { cache: "no-store" } as Parameters<typeof api.get>[1],
      ),
      this.listProductImages(id).catch(() => [] as CatalogProductImage[]),
    ]);
    return productViewToCatalogProduct(view, gallery);
  },

  async listProductImages(id: string, signal?: AbortSignal): Promise<CatalogProductImage[]> {
    const result = await api.get<ApiProductImageListResponse>(
      `/products/${encodeURIComponent(id)}/images`,
      { cache: "no-store", signal } as Parameters<typeof api.get>[1],
    );
    return (result.items ?? []).map(apiImageToCatalogImage);
  },

  // -------------------------------------------------------------------------
  // Preview — POST /crawl (include_images conforme checkbox do usuário)
  // Sem preview_id de servidor; use id local se necessário.
  // -------------------------------------------------------------------------
  async previewProduct(
    url: string,
    options: { includeImages?: boolean } = {},
  ): Promise<ProductPreviewResponse> {
    const include_images = options.includeImages === true;
    const item = await api.post<ProductPriceItem>("/crawl", { url, include_images });
    const preview = productPriceItemToPreview(item, url);
    return { product: preview };
  },

  /** Stub de compatibilidade — lança para acionar fallback local. */
  async getPreview(_previewId: string): Promise<ProductPreviewResponse> {
    throw new ApiError("Preview nao disponivel no servidor.", 404, "PREVIEW_NOT_FOUND");
  },

  /** Stub de compatibilidade — sem estado server-side de preview. */
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  async discardPreview(_previewId: string): Promise<void> {
    // Sem ação: ScoutApiV2 não mantém previews no servidor
  },

  // -------------------------------------------------------------------------
  // Importar produto — POST /products
  // -------------------------------------------------------------------------
  async importProduct(
    product: ProductPreview,
    _previewId?: string,
  ): Promise<{ product: CatalogProduct; message?: string }> {
    const body: ProductRegisterRequest = {
      title: product.name ?? "Produto sem nome",
      brand: product.brand,
      model: product.model ?? null,
      variant: product.variant ?? null,
      gtin: product.gtin,
      store: product.source,
      country: product.country ?? "BR",
      product_id: product.productId,
      sku: product.sourceProductId,
      url: product.url,
      canonical_url: product.url,
      attributes: {},
      price: moneyOrUndefined(product.cardPrice ?? product.originalPrice),
      pix_price: moneyOrUndefined(product.pixPrice),
      original_price: moneyOrUndefined(product.originalPrice),
      currency: product.currency ?? "BRL",
      seller: product.seller,
      available: true,
    };
    const approvedImageUrls = Array.from(new Set((product.images ?? [])
      .map((image) => image.url?.trim())
      .filter((url): url is string => Boolean(url))));
    body.images = approvedImageUrls.slice(0, 20)
      .map((source_url, position) => ({ source_url, position, is_main: position === 0 }));
    const result = await api.post<ProductRegisterResponse>("/products", body);
    return {
      product: productViewToCatalogProduct(result.product),
      message: body.images.length
        ? approvedImageUrls.length > 20
          ? `Produto salvo. As primeiras 20 de ${approvedImageUrls.length} imagens serão processadas em segundo plano (limite do catálogo).`
          : "Produto salvo. As imagens serão processadas em segundo plano."
        : result.created ? "Produto cadastrado." : "Produto reutilizado.",
    };
  },

  // -------------------------------------------------------------------------
  // Atualizar produto — PATCH /products/{id}
  // -------------------------------------------------------------------------
  async updateProduct(
    id: string,
    input: Record<string, unknown>,
  ): Promise<{ product: CatalogProduct }> {
    const body: Record<string, unknown> = {};
    if ("canonical_name" in input) body.title = input.canonical_name;
    if ("brand" in input) body.brand = input.brand;
    if ("model" in input) body.model = input.model;
    if ("category" in input) body.attributes = { category: input.category };
    await api.patch<ProductView>(`/products/${encodeURIComponent(id)}`, body);
    const product = await this.getProduct(id);
    return { product };
  },

  // -------------------------------------------------------------------------
  // Excluir produto — DELETE /products/{id} → 204
  // -------------------------------------------------------------------------
  async deleteProduct(id: string): Promise<void> {
    await api.delete<void>(`/products/${encodeURIComponent(id)}`);
  },

  // -------------------------------------------------------------------------
  // Lojas — GET /stores (somente leitura)
  // -------------------------------------------------------------------------
  async listStores(options: { signal?: AbortSignal } = {}): Promise<{ stores: CatalogStore[] }> {
    const result = await api.get<{ stores: StoreInfo[] }>("/stores", {
      cache: "no-store",
      signal: options.signal,
    } as Parameters<typeof api.get>[1]);
    return { stores: (result.stores ?? []).map(storeInfoToCatalogStore) };
  },

  async uploadStoreLogo(id: string, file: File): Promise<{ store: CatalogStore }> {
    const form = new FormData(); form.append("file", file);
    const result = await api.post<{ store: StoreInfo }>(`/admin/stores/${encodeURIComponent(id)}/logo`, form);
    return { store: storeInfoToCatalogStore(result.store) };
  },

  async updateStore(id: string, input: Record<string, unknown>): Promise<{ store: CatalogStore }> {
    const result = await api.patch<{ store: StoreInfo & { logo_svg?: string | null } }>(
      `/admin/stores/${encodeURIComponent(id)}`,
      { display_name: input.name, logo_svg: input.logo_svg ?? null },
    );
    return { store: storeInfoToCatalogStore(result.store) };
  },

  // Imagens — ScoutApiV2 gallery (Google Drive storage no backend)
  // -------------------------------------------------------------------------
  async addProductImage(
    id: string,
    imageUrl: string,
    options?: { position?: number; is_main?: boolean },
  ): Promise<{ image: ProductImage }> {
    const created = await api.post<ApiProductImageView>(
      `/products/${encodeURIComponent(id)}/images`,
      {
        source_url: imageUrl,
        position: options?.position ?? null,
        is_main: options?.is_main ?? false,
      },
    );
    const mapped = apiImageToCatalogImage(created);
    return { image: toGalleryProductImage(mapped) };
  },

  async updateProductImages(
    id: string,
    imageIds: string[],
    primaryImageId: string,
  ): Promise<{
    product_id: string;
    primary_image_id: string;
    images: CatalogProductImage[];
  }> {
    const body = {
      images: imageIds.map((imageId, position) => ({
        image_id: imageId,
        position,
        is_main: imageId === primaryImageId,
      })),
    };
    const result = await api.patch<ApiProductImageListResponse>(
      `/products/${encodeURIComponent(id)}/images`,
      body,
    );
    const images = (result.items ?? []).map(apiImageToCatalogImage);
    const main = images.find((image) => image.is_main) ?? images[0];
    return {
      product_id: id,
      primary_image_id: main?.image_id ?? primaryImageId,
      images,
    };
  },

  async deleteProductImage(id: string, imageId: string): Promise<void> {
    await api.delete<void>(
      `/products/${encodeURIComponent(id)}/images/${encodeURIComponent(imageId)}`,
    );
  },

  // -------------------------------------------------------------------------
  // Busca de preços (não-streaming) — POST /match
  // -------------------------------------------------------------------------
  async searchOtherStorePrices(productId: string): Promise<OtherStorePricesResponse> {
    const product = await this.getProduct(productId);
    const referenceUrl = selectReferenceUrl(product);
    // Atualiza listings existentes antes da descoberta (fase de sincronização).
    try {
      await this.refreshOffers(productId);
    } catch {
      // Refresh parcial não bloqueia a descoberta em outras lojas.
    }
    const body: MatchRequest = {
      reference_url: referenceUrl,
      canonical_product_id: productId,
      persist: true,
      include_review: true,
      include_images: false,
    };
    const matchResponse = await api.post<MatchResponse>("/match", body);
    return matchResponseToOtherStorePrices(matchResponse);
  },

  // -------------------------------------------------------------------------
  // Match Run persistente — POST/GET (polling; substitui SSE)
  // -------------------------------------------------------------------------
  async startMatchRun(productId: string, signal?: AbortSignal): Promise<MatchRunStatusView> {
    return api.post<MatchRunStatusView>(
      `/products/${encodeURIComponent(productId)}/match-runs`,
      undefined, { signal },
    );
  },

  async getActiveMatchRun(productId: string, signal?: AbortSignal): Promise<MatchRunStatusView | null> {
    const result = await api.get<MatchRunStatusView | undefined>(
      `/products/${encodeURIComponent(productId)}/match-runs/active`,
      { cache: "no-store", signal },
    );
    return result ?? null;
  },

  async getMatchRunLive(runId: string, signal?: AbortSignal): Promise<MatchRunLiveView> {
    return api.get<MatchRunLiveView>(`/match-runs/${encodeURIComponent(runId)}/live`, { cache: "no-store", signal });
  },

  async getMatchRun(runId: string, signal?: AbortSignal): Promise<MatchRunStatusView> {
    return api.get<MatchRunStatusView>(
      `/match-runs/${encodeURIComponent(runId)}`,
      { cache: "no-store", signal },
    );
  },

  async listMatchRuns(
    productId: string,
    options: { limit?: number; offset?: number; signal?: AbortSignal } = {},
  ): Promise<MatchRunListResponse> {
    return api.get<MatchRunListResponse>(
      `/products/${encodeURIComponent(productId)}/match-runs`,
      {
        query: { limit: options.limit ?? 20, offset: options.offset ?? 0 },
        cache: "no-store",
        signal: options.signal,
      } as Parameters<typeof api.get>[1],
    );
  },

  async getMatchRunDetails(runId: string): Promise<MatchRunDetailView> {
    return api.get<MatchRunDetailView>(
      `/match-runs/${encodeURIComponent(runId)}/details`,
      { cache: "no-store" } as Parameters<typeof api.get>[1],
    );
  },

  // -------------------------------------------------------------------------
  // Atualizar ofertas — POST /offers/refresh
  // -------------------------------------------------------------------------
  async refreshOffers(productId: string): Promise<OfferRefreshResponse> {
    const body: OfferRefreshRequest = { canonical_product_id: productId };
    return api.post<OfferRefreshResponse>("/offers/refresh", body);
  },
};

// =============================================================================
// notificationsApi — central persistente de notificações
// =============================================================================

export const notificationsApi = {
  async list(options: {
    limit?: number;
    offset?: number;
    unreadOnly?: boolean;
    signal?: AbortSignal;
  } = {}): Promise<NotificationListResponse> {
    return api.get<NotificationListResponse>("/notifications", {
      query: {
        limit: options.limit ?? 30,
        offset: options.offset ?? 0,
        unread_only: options.unreadOnly ?? false,
      },
      cache: "no-store",
      signal: options.signal,
    } as Parameters<typeof api.get>[1]);
  },

  async unreadCount(): Promise<number> {
    const result = await api.get<UnreadCountResponse>("/notifications/unread-count", {
      cache: "no-store",
    } as Parameters<typeof api.get>[1]);
    return result.unread_count;
  },

  async markRead(notificationId: string): Promise<NotificationView> {
    return api.post<NotificationView>(
      `/notifications/${encodeURIComponent(notificationId)}/read`,
    );
  },

  async markAllRead(): Promise<UnreadCountResponse> {
    return api.post<UnreadCountResponse>("/notifications/read-all");
  },
};
