// =============================================================================
// Tipos da API ScoutApiV2 (contratos do backend)
// =============================================================================

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export type PublicUser = {
  id: string;
  display_name: string;
};

export type AuthSession = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: PublicUser;
};

// ---------------------------------------------------------------------------
// Produtos — respostas do ScoutApiV2
// ---------------------------------------------------------------------------

export type ProductListingView = {
  id: string;
  store: string;
  country: string;
  product_id: string;
  sku: string | null;
  gtin: string | null;
  url: string;
  canonical_url: string;
  status: string;
  title: string | null;
  match_decision: string;
  confidence: string;
  created_at: string;
  updated_at: string;
  monitoring_enabled?: boolean;
  last_checked_at?: string | null;
  next_check_at?: string | null;
  last_successful_check_at?: string | null;
  consecutive_failures?: number;
  price?: string | null;
  currency?: string | null;
  seller?: string | null;
  availability?: string | null;
  available?: boolean | null;
  pix_price?: string | null;
  original_price?: string | null;
  promotion_status?: string;
  promotion_expires_at?: string | null;
  promotion_type?: string | null;
  promotion_price?: string | null;
  promotion_conditions?: Record<string, unknown>;
  promotion_commercially_active?: boolean;
  /** Valor de referência em BRL (somente FX). Null se indisponível. */
  converted_price_brl?: string | number | null;
  exchange_rate?: string | number | null;
  exchange_rate_type?: string | null;
  exchange_rate_status?: string | null;
  exchange_rate_source?: string | null;
  exchange_rate_updated_at?: string | null;
};

/** ProductView retornado por GET /products e GET /products/{id}. */
export type ProductView = {
  id: string;
  title: string;
  brand: string | null;
  model: string | null;
  variant_key: string | null;
  attributes: Record<string, unknown>;
  gtins: string[];
  listings: ProductListingView[];
  images?: ApiProductImageView[];
  primary_image_url?: string | null;
  primary_image_status?: ImageAvailabilityStatus;
  primary_image_error_code?: string | null;
  primary_image_retryable?: boolean;
  created_at: string;
  updated_at: string;
};

export type ProductListResponse = {
  items: ProductView[];
  count: number;
  limit: number;
  offset: number;
};

/** Resposta canônica do ScoutApiV2 para galeria. */
export type ApiProductImageView = {
  image_id: string;
  product_id: string;
  position: number;
  is_main: boolean;
  source_url: string;
  original_url: string | null;
  optimized_url: string | null;
  display_url: string | null;
  image_status?: ImageAvailabilityStatus;
  image_error_code?: string | null;
  image_retryable?: boolean;
  image_warning_code?: string | null;
  original_status: string;
  optimized_status: string;
  original_width: number | null;
  original_height: number | null;
  optimized_error: string | null;
  created_at: string;
  updated_at: string;
};

export type ImageAvailabilityStatus =
  | "ready"
  | "missing"
  | "processing"
  | "temporarily_unavailable"
  | "permission_denied"
  | "not_found"
  | "storage_error"
  | "invalid_reference"
  | "load_failed";

export type ApiProductImageListResponse = {
  items: ApiProductImageView[];
  count: number;
};

export type ProductRegisterRequest = {
  images?: { source_url: string; position: number; is_main: boolean }[];
  title: string;
  brand?: string | null;
  model?: string | null;
  variant?: string | null;
  variant_key?: string | null;
  gtin?: string | null;
  attributes?: Record<string, unknown> | null;
  store?: string | null;
  country?: string | null;
  product_id?: string | null;
  sku?: string | null;
  url?: string | null;
  canonical_url?: string | null;
  /** Preço do preview — seed do OfferSnapshot inicial no ScoutApiV2. */
  price?: number | string | null;
  pix_price?: number | string | null;
  original_price?: number | string | null;
  currency?: string | null;
  seller?: string | null;
  available?: boolean | null;
};

export type ProductRegisterResponse = {
  created: boolean;
  listing_created: boolean;
  product: ProductView;
  listing: ProductListingView | null;
};

export type ProductUpdateRequest = {
  title?: string | null;
  brand?: string | null;
  model?: string | null;
  variant?: string | null;
  attributes?: Record<string, unknown> | null;
};

// ---------------------------------------------------------------------------
// Crawl — POST /crawl
// ---------------------------------------------------------------------------

export type CrawlRequest = {
  url: string;
  include_images?: boolean;
};

/** Item retornado pelo endpoint POST /crawl. Decimais como strings JSON. */
export type ProductPriceItem = {
  store: string;
  country: string;
  product_id: string;
  sku: string | null;
  gtin: string | null;
  title: string;
  brand: string | null;
  model: string | null;
  variant: string | null;
  seller: string | null;
  url: string;
  canonical_url: string;
  currency: string;
  price: string;
  original_price: string | null;
  discount_percentage: string | null;
  pix_price: string | null;
  installment_price: string | null;
  installment_count: number | null;
  shipping_price: string | null;
  available: boolean;
  images: string[];
  scraped_at: string;
  last_changed_at: string | null;
  metadata: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// Match — POST /match (execução síncrona legada)
// ---------------------------------------------------------------------------

export type MatchReason = {
  code: string;
  detail: string;
  score?: number | null;
};

export type MatchHit = {
  store: string;
  store_display_name?: string | null;
  country: string;
  listing_id: string | null;
  decision: "auto_match" | "review" | "reject";
  confidence: string;
  reasons: MatchReason[];
  product: ProductPriceItem;
  search_query: string | null;
};

export type MatchStoreError = {
  store: string;
  store_display_name?: string | null;
  code: string;
  message: string;
};

export type MatchRequest = {
  reference_url: string;
  canonical_product_id?: string | null;
  stores?: string[];
  include_review?: boolean;
  persist?: boolean;
  include_images?: boolean;
  max_candidates_per_store?: number;
};

export type MatchResponse = {
  canonical_product_id: string | null;
  reference: ProductPriceItem;
  matches: MatchHit[];
  unmatched_stores: string[];
  errors: MatchStoreError[];
  discovered_gtin: string | null;
  gtin_source: string | null;
};

// ---------------------------------------------------------------------------
// Offers — POST /offers/refresh
// ---------------------------------------------------------------------------

export type OfferRefreshRequest = {
  canonical_product_id?: string;
  listing_ids?: string[];
  urls?: string[];
  include_details?: boolean;
};

export type OfferRefreshResponse = {
  refreshed: number;
  errors: Array<{ url: string; error: string }>;
};

// ---------------------------------------------------------------------------
// Stores — GET /stores
// ---------------------------------------------------------------------------

export type StoreInfo = {
  key: string;
  display_name?: string;
  country: string;
  currency: string;
  supported_country_currency_pairs: Array<[string, string]>;
  domains: string[];
  implemented: boolean;
  supports_search: boolean;
  supports_images: boolean;
  match_enabled?: boolean;
  match_disabled_reason?: string | null;
  image_fetch_cost?: "low" | "high" | "unsupported";
  default_include_images?: boolean;
  logo_svg?: string | null;
  logo_url?: string | null;
  logo_mime_type?: string | null;
  logo_processing_status?: "ready" | "pending" | "processing" | "failed";
};

// =============================================================================
// Tipos de UI — usados pelas páginas (mantidos para compatibilidade)
// =============================================================================

// ---------------------------------------------------------------------------
// Helpers monetários
// ---------------------------------------------------------------------------

/**
 * Converte valor decimal (string ou número) em número, ou null se inválido.
 * Use apenas para exibição — nunca para cálculos financeiros.
 */
export function parseMoney(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : parseFloat(String(value));
  return Number.isFinite(parsed) ? parsed : null;
}

/** Reexport — formatação central em `utils/money.ts`. */
export { formatMoney } from "@/utils/money";

// ---------------------------------------------------------------------------
// Tipos de UI para produto (usados por páginas e componentes existentes)
// ---------------------------------------------------------------------------

export type ProductPreview = {
  source: string;
  url: string;
  productId: string | null;
  sourceProductId?: string | null;
  name: string | null;
  brand: string | null;
  model?: string | null;
  variant?: string | null;
  country?: string | null;
  currency?: string | null;
  brand_assets?: EntityAssetsPayload | null;
  store_assets?: EntityAssetsPayload | null;
  brand_confidence?: number | null;
  brand_source?: string | null;
  gtin?: string | null;
  gtin_type?: string | null;
  gtin_confidence?: number | null;
  gtin_source?: string | null;
  mpn?: string | null;
  originalPrice: number | null;
  pixPrice: number | null;
  pixDiscountPercent: number | null;
  cardPrice: number | null;
  installments: { quantity: number; amount: number; total: number; interestFree?: boolean | null } | null;
  priceDifferencePixToCard: number | null;
  seller: string | null;
  seller_assets?: EntityAssetsPayload | null;
  deliveryResponsible?: string | null;
  delivery_assets?: EntityAssetsPayload | null;
  special_status: string | null;
  shipping_from: string | null;
  taxonomy: Array<{ level: number; name: string; slug: string }>;
  main_image?: string | null;
  images: Array<{ url: string; alt: string | null; width: number | null; height: number | null }>;
  image_status?: "success" | "partial" | "empty" | "omitted" | "error" | null;
  image_error?: string | null;
};

export type ProductPreviewResponse = {
  product: ProductPreview;
  preview_id?: string;
  expires_at?: string;
  status?: "ready";
};

export type ExistingCatalogProduct = {
  id: string;
  name: string | null;
  brand?: string | null;
  product_url?: string | null;
  primary_image_url?: string | null;
  main_image_url?: string | null;
  created_at?: string | null;
};

// ---------------------------------------------------------------------------
// Entity assets (legado — mantido para compatibilidade com ReviewPreview)
// ---------------------------------------------------------------------------

export type EntityAssets = {
  logo?: string | null;
  symbol?: string | null;
  icon?: string | null;
  alt?: string | null;
  visual?: LogoVisualMetadata | null;
};

export type LogoVisualMetadata = {
  logo_id?: string | null;
  analysis_status?: "pending" | "ready" | "failed" | null;
  dominant_color?: string | null;
  average_luminance?: number | null;
  has_transparency?: boolean | null;
  recommended_background?: "light" | "dark" | "neutral" | null;
  foreground_color?: string | null;
};

export type BrandAssetRecord = {
  asset_type?: string;
  brand?: string;
  available?: boolean;
  source?: string;
  asset_url?: string | null;
  logo_id?: string | null;
  visual?: LogoVisualMetadata | null;
  drive_file_ids?: string[];
  drive_file_names?: string[];
  error?: string | null;
};

export type BrandAssetsResponse = {
  brand: string;
  requested_assets?: string[];
  assets: Record<string, BrandAssetRecord>;
};

export type EntityAssetsPayload = EntityAssets | BrandAssetsResponse | Record<string, BrandAssetsResponse>;

// ---------------------------------------------------------------------------
// Tipos do catálogo (UI) — usados pelas páginas existentes
// ---------------------------------------------------------------------------

export type OfferPriceHistory = {
  id: number;
  catalog_offer_id: string;
  original_price: number | null;
  pix_price: number | null;
  card_price: number | null;
  installments: number | null;
  installment_value: number | null;
  interest_free: boolean | null;
  currency: string;
  country: string;
  market: string;
  captured_at: string;
};

export type CatalogOffer = {
  match_decision?: string;
  status?: string;
  id: string;
  catalog_product_variant_id: string;
  store_id: string;
  source: string;
  external_product_id: string | null;
  canonical_url: string;
  seller: string | null;
  delivery_responsible: string | null;
  original_price: number | null;
  pix_price: number | null;
  card_price: number | null;
  discount_percent: number | null;
  installments: number | null;
  installment_value: number | null;
  interest_free: boolean | null;
  image_url: string | null;
  currency: string;
  country: string;
  market: string;
  store?: { id: string; name: string; hostname: string } | null;
  history: OfferPriceHistory[];
  last_checked_at?: string | null;
  next_check_at?: string | null;
  promotion_expires_at?: string | null;
  promotion_status?: string | null;
  promotion_commercially_active?: boolean;
  available?: boolean | null;
  /** Valor de referência em BRL = preço estrangeiro × cotação (sem impostos). */
  converted_price_brl?: number | null;
  exchange_rate?: number | null;
  exchange_rate_type?: string | null;
  exchange_rate_status?: string | null;
  exchange_rate_source?: string | null;
  exchange_rate_updated_at?: string | null;
};

export type CatalogProductVariant = {
  id: string;
  catalog_product_id: string;
  attributes: Record<string, string>;
  variant_fingerprint: string;
  identity_status: "confirmed" | "ambiguous";
  offers: CatalogOffer[];
};

export type CatalogProductImage = {
  image_id: string;
  product_id?: string;
  original_url: string;
  optimized_image_url?: string | null;
  display_url?: string | null;
  position: number;
  is_main: boolean;
  status: string;
  original_status?: string;
  optimized_status?: string;
  drive_original_id?: string | null;
  drive_avif_id?: string | null;
  error?: string | null;
  image_status?: ImageAvailabilityStatus;
  image_error_code?: string | null;
  image_retryable?: boolean;
  image_warning_code?: string | null;
};

export type CatalogProduct = {
  id: string;
  canonical_name: string;
  brand: string | null;
  model: string | null;
  category: string | null;
  identity_attributes: Record<string, unknown>;
  identity_status: "confirmed" | "ambiguous";
  variants: CatalogProductVariant[];
  images: CatalogProductImage[];
  created_at: string;
  updated_at: string;
  // Campos legados — preenchidos pelo mapper para compatibilidade com páginas
  name: string;
  product_url: string;
  source: string;
  external_product_id: string;
  primary_image_url?: string | null;
  primary_image_status?: ImageAvailabilityStatus;
  primary_image_error_code?: string | null;
  primary_image_retryable?: boolean;
  primary_image_id?: string | null;
  store?: { name: string; hostname: string } | null;
};

export type CatalogOverview = {
  summary: { products: number; variants: number; offers: number; stores: number };
  recent_products: Array<{ id: string; name: string; brand: string | null; created_at: string; image_url: string | null; variants: number; offers: number }>;
  single_offer_variants: Array<{ product_id: string; variant_id: string; product_name: string; variant_label: string | null; store_name: string | null }>;
  best_price_opportunities: Array<{ product_id: string; variant_id: string; product_name: string; variant_label: string | null; lowest_price: number; highest_price: number; savings: number; currency: string; offers: Array<{ store_name: string | null; price: number; url: string | null }> }>;
  alerts: Array<{ kind: string; label: string; count: number; product_ids: string[] }>;
  recent_activity: Array<{ kind: string; label: string; product_id: string | null; product_name: string | null; occurred_at: string }>;
};

export type CatalogStore = {
  id: string;
  name: string;
  hostname: string;
  url: string;
  country: string;
  currency: string;
  supported_country_currency_pairs?: Array<[string, string]>;
  logo_svg?: string | null;
  logo_url?: string | null;
  logo_processing_status?: "ready" | "pending" | "processing" | "failed";
  productCount: number;
  updated_at: string;
  domains?: string[];
  supports_images?: boolean;
  supports_search?: boolean;
  implemented?: boolean;
  match_enabled?: boolean;
  match_disabled_reason?: string | null;
  image_fetch_cost?: "low" | "high" | "unsupported";
  default_include_images?: boolean;
};

/** Alias de compatibilidade — mapeado a partir de ProductView pelo serviço. */
export type Product = CatalogProduct;

// ---------------------------------------------------------------------------
// Preços em outras lojas (resultado síncrono legado)
// ---------------------------------------------------------------------------

export type OtherStorePricesResponse = {
  variant?: CatalogProductVariant;
  prices?: OtherStorePrice[];
  diagnostics: Array<Record<string, unknown>>;
};

export type OtherStorePrice = {
  storeId: string;
  displayName: string;
  url: string;
  currency: string;
  originalPrice: number | null;
  pixDiscountPercent: number | null;
  pixPrice: number | null;
  cardPrice: number | null;
  installments: { quantity: number; amount: number; total: number; interestFree: boolean | null } | null;
  seller: string | null;
  deliveryResponsible: string | null;
};

// ---------------------------------------------------------------------------
// Match Run persistente — polling (substitui SSE)
// ---------------------------------------------------------------------------

export type MatchRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type MatchStoreRunStatus =
  | "pending"
  | "running"
  | "match"
  | "no_match"
  | "error";

export type MatchRunStatusView = {
  id: string;
  product_id: string;
  status: MatchRunStatus;
  started_at: string;
  finished_at: string | null;
  last_activity_at: string;
  claimed_at?: string | null;
  /** Início da attempt ativa (claimed_at) ou started_at — base do timer UX. */
  active_since?: string | null;
  attempts?: number;
  total_duration_ms: number | null;
  stores_total: number;
  stores_completed: number;
  matches_found: number;
  no_matches: number;
  errors: number;
  failure_code: string | null;
  failure_message: string | null;
  already_active: boolean;
};

export type MatchRunListResponse = {
  items: MatchRunStatusView[];
};

export type MatchCandidateLogView = {
  sequence: number;
  title: string | null;
  url: string | null;
  store_product_id: string | null;
  decision: string | null;
  confidence: string | number | null;
  reasons: string[];
  duration_ms: number | null;
};

export type MatchStoreRunView = {
  id: string;
  store: string;
  store_display_name: string | null;
  status: MatchStoreRunStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  queries: string[];
  queries_count: number;
  candidates_found: number;
  candidates_evaluated: number;
  matched_url: string | null;
  matched_title: string | null;
  matched_price: string | number | null;
  matched_currency: string | null;
  matched_confidence: string | number | null;
  matched_reasons: string[];
  error_code: string | null;
  error_message: string | null;
  search_duration_ms: number | null;
  candidate_fetch_duration_ms: number | null;
  candidates: MatchCandidateLogView[];
};

export type MatchRunDetailView = {
  run: MatchRunStatusView;
  stores: MatchStoreRunView[];
};

export type MatchStoreLiveView = Pick<MatchStoreRunView,
  "id" | "store" | "store_display_name" | "status" | "started_at" | "finished_at" |
  "matched_url" | "matched_title" | "matched_price" | "matched_currency" |
  "matched_confidence" | "error_code"
> & {
  matched_decision: "auto_match" | "review" | null;
  matched_listing_id: string | null;
  matched_store: string | null;
  matched_country: string | null;
  matched_product_id: string | null;
  matched_canonical_url: string | null;
  converted_price_brl: string | number | null;
  exchange_rate_status: string | null;
  exchange_rate_updated_at: string | null;
};

export type MatchRunLiveView = {
  run: MatchRunStatusView;
  is_effectively_active: boolean;
  auto_matches_found: number;
  stores: MatchStoreLiveView[];
};

export type NotificationView = {
  id: string;
  type: string;
  product_id: string | null;
  match_run_id: string | null;
  title: string;
  message: string;
  created_at: string;
  read_at: string | null;
  metadata: Record<string, unknown>;
};

export type NotificationListResponse = {
  items: NotificationView[];
  unread_count: number;
};

export type UnreadCountResponse = {
  unread_count: number;
};

// ---------------------------------------------------------------------------
// Imagens de produto (UI)
// ---------------------------------------------------------------------------

export type ProductImage = {
  id: string;
  name: string;
  image_url: string;
  original_url?: string;
  sort_order: number;
  is_main?: boolean;
  image_status?: ImageAvailabilityStatus;
  image_error_code?: string | null;
  image_retryable?: boolean;
  image_warning_code?: string | null;
};

export type TaxonomyItem = {
  level: number;
  category?: { id: string; name: string; slug: string; level: number } | null;
};
