"use client";

import matchStyles from "@/components/ProductMatchResults.module.css";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ImageViewer, { ImageWithState } from "@/components/ImageViewer";
import OfferPrice from "@/components/OfferPrice";
import { storeLogoDataUrl } from "@/components/StoreConfigurationForm";
import { catalogApi } from "@/utils/api/services";
import { startVisiblePolling } from "@/utils/visible-polling";
import { toGalleryProductImage } from "@/utils/api/product-images";
import { getStoreDisplayName } from "@/utils/store-display";
import { MatchRunHistory } from "@/components/MatchRunHistory";
import { ProgressiveOfferRow, ProductMatchStoreProgress, ReviewOffers } from "@/components/ProductMatchResults";
import { mergeMatchOffers } from "@/utils/match-run-offers";
import { ProductMatchBanner } from "@/components/ProductMatchBanner";
import { useProductMatchRun } from "@/hooks/useProductMatchRun";
import type { MatchRunStatusView, MatchStoreLiveView, CatalogOffer, CatalogProductImage, CatalogStore, Product, ProductImage } from "@/utils/api/contracts";
import {
  comparablePriceBrl,
  formatMoney,
  rawOfferPrice,
} from "@/utils/money";


const fields: Array<{ key: keyof Product; label: string; type?: string }> = [
  { key: "canonical_name", label: "Nome canônico" },
  { key: "brand", label: "Marca" },
  { key: "model", label: "Modelo" },
  { key: "category", label: "Categoria" },
/*
  { key: "name", label: "Nome" }, { key: "brand", label: "Marca" }, { key: "product_url", label: "URL do produto", type: "url" },
  { key: "seller", label: "Vendedor" }, { key: "shipping_from", label: "Envio a partir de" }, { key: "special_status", label: "Estado especial" },
  { key: "original_price", label: "Preço original", type: "number" }, { key: "pix_price", label: "Preço PIX", type: "number" }, { key: "pix_discount_percent", label: "Desconto PIX (%)", type: "number" },
  { key: "card_price", label: "Preço no cartão", type: "number" }, { key: "installments_quantity", label: "Quantidade de parcelas", type: "number" }, { key: "installment_amount", label: "Valor da parcela", type: "number" },
  { key: "price_difference_pix_to_card", label: "Diferença PIX x cartão", type: "number" },
*/
];

function Icon({ name }: { name: "arrow" | "back" | "check" | "close" | "image" | "plus" | "refresh" | "trash" | "up" | "down" | "left" | "right" }) {
  const paths = { arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>, back: <><path d="M19 12H5M11 18l-6-6 6-6" /></>, check: <path d="m5 12 4 4L19 6" />, close: <><path d="m6 6 12 12M18 6 6 18" /></>, image: <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8" cy="9" r="1.5" /><path d="m4 17 5-5 3 3 2-2 6 5" /></>, plus: <><path d="M12 5v14M5 12h14" /></>, refresh: <><path d="M20 11a8 8 0 0 0-14-4L4 9" /><path d="M4 4v5h5M4 13a8 8 0 0 0 14 4l2-2" /><path d="M20 20v-5h-5" /></>, trash: <><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></>, up: <path d="m6 14 6-6 6 6" />, down: <path d="m6 10 6 6 6-6" />, left: <path d="m14 6-6 6 6 6" />, right: <path d="m10 6 6 6-6 6" /> };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function toGalleryImage(image: CatalogProductImage): ProductImage {
  return toGalleryProductImage(image);
}

function resolvePrimaryImage(product: Product, gallery: ProductImage[]): ProductImage | null {
  const primaryUrl = product.primary_image_url?.trim();
  if (primaryUrl) {
    const byUrl = gallery.find((image) => image.image_url === primaryUrl || image.original_url === primaryUrl);
    if (byUrl) return byUrl;
  }

  return gallery.find((image) => image.is_main && image.image_url)
    ?? gallery.find((image) => image.id === product.primary_image_id && image.image_url)
    ?? gallery.find((image) => image.image_url)
    ?? null;
}

function offerPrice(offer: CatalogOffer): number | null {
  return rawOfferPrice(offer);
}

function storeLabel(offer: CatalogOffer): string {
  return getStoreDisplayName(
    offer.source,
    offer.canonical_url || offer.store?.hostname,
    offer.store?.name,
    offer.country,
  );
}

function storeInitials(label: string): string {
  return label.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "LO";
}


function formatRelativePast(iso: string | null | undefined, nowMs: number): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return null;
  const deltaSec = Math.max(0, Math.floor((nowMs - then) / 1000));
  if (deltaSec < 60) return "há menos de 1 min";
  if (deltaSec < 3600) return `há ${Math.floor(deltaSec / 60)} min`;
  if (deltaSec < 86400) return `há ${Math.floor(deltaSec / 3600)} h`;
  return `há ${Math.floor(deltaSec / 86400)} d`;
}

function formatAbsoluteLocal(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function formatCountdown(expiresAtIso: string, nowMs: number): string {
  const expires = Date.parse(expiresAtIso);
  if (!Number.isFinite(expires)) return "—";
  const remaining = Math.max(0, Math.floor((expires - nowMs) / 1000));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const seconds = remaining % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return `${days}d ${pad(hours % 24)}:${pad(minutes)}:${pad(seconds)}`;
  }
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function OfferMonitoringMeta({
  offer,
  onExpired,
}: {
  offer: CatalogOffer;
  onExpired?: () => void;
}) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const expiresAt = offer.promotion_commercially_active ? offer.promotion_expires_at : null;

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!expiresAt) return;
    const expires = Date.parse(expiresAt);
    if (!Number.isFinite(expires)) return;
    if (expires <= nowMs) onExpired?.();
  }, [expiresAt, nowMs, onExpired]);

  const lastChecked = formatRelativePast(offer.last_checked_at, nowMs);
  const nextCheck = formatAbsoluteLocal(offer.next_check_at);
  const remaining = expiresAt ? formatCountdown(expiresAt, nowMs) : null;
  const expiredLocally = Boolean(expiresAt && Date.parse(expiresAt) <= nowMs);

  return (
    <div className="persisted-offer-monitoring">
      {lastChecked && <span>Última verificação: {lastChecked}</span>}
      {nextCheck && <span>Próxima verificação: {nextCheck}</span>}
      {expiresAt && !expiredLocally && (
        <span>Promoção termina em: {remaining}</span>
      )}
      {expiresAt && expiredLocally && (
        <span>Promoção expirada — atualizando…</span>
      )}
    </div>
  );
}

const PersistedOffersList = memo(function PersistedOffersList({
  product,
  primaryImage,
  money,
  onSearch,
  searchBusy,
  searchLabel,
  onRefreshOffers,
  liveResults,
  observedRun,
}: {
  product: Product;
  primaryImage: ProductImage | null;
  money: (value: number | null, currency: string) => string;
  onSearch: () => void;
  searchBusy: boolean;
  searchLabel: string;
  onRefreshOffers?: () => void;
  liveResults: MatchStoreLiveView[];
  observedRun: MatchRunStatusView | null;
}) {
  const [stores, setStores] = useState<CatalogStore[]>([]);
  const hasProcessingStoreLogo = stores.some(
    (store) => store.logo_processing_status === "pending" || store.logo_processing_status === "processing",
  );

  useEffect(() => {
    let active = true;
    void catalogApi.listStores().then(({ stores: loadedStores }) => {
      if (active) setStores(loadedStores);
    }).catch(() => {
      // Keep the existing initials fallback when store metadata is unavailable.
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!hasProcessingStoreLogo) return;
    const polling = startVisiblePolling(async (signal) => {
      const result = await catalogApi.listStores({ signal });
      signal.throwIfAborted();
      setStores(result.stores ?? []);
    }, () => 3000, undefined, 3000);
    return () => polling.stop();
  }, [hasProcessingStoreLogo]);
  return (
    <div className="persisted-offers">
      {product.variants.map((variant, variantIndex) => {
        const reviewOffers = variant.offers.filter((offer) => offer.status === "review" || offer.match_decision === "review");
        const offers = mergeMatchOffers(
          variant.offers.filter((offer) => offer.status !== "review" && offer.match_decision !== "review"),
          variantIndex === 0 ? liveResults : [], Boolean(observedRun && !["pending", "running"].includes(observedRun.status)),
        );
        return (
          <section className="persisted-offer-variant" key={variant.id} aria-label="Ofertas do produto">
            {offers.length === 0 ? (
              <div className="persisted-offers-empty">
                <p>Nenhuma oferta encontrada.</p>
                <button className="outline-button" type="button" onClick={onSearch} disabled={searchBusy}>
                  {searchLabel}
                </button>
              </div>
            ) : (
              <div className="persisted-offer-list">
                {offers.map((row, index) => {
                  if (!row.offer && row.partial) return <ProgressiveOfferRow key={row.key} result={row.partial} run={observedRun} />;
                  const offer = row.offer!;
                  const price = offerPrice(offer);
                  const comparable = comparablePriceBrl(offer);
                  const isBest = index === 0 && comparable != null;
                  const label = storeLabel(offer);
                  const countryCode = label === "Amazon Brasil" ? "BR" : label === "Amazon EUA" ? "US" : null;
                  const matchingStore = stores.find((store) => {
                    const key = offer.source?.trim().toLowerCase().replace(/[\s-]+/g, "_");
                    const offerHost = (() => {
                      try { return new URL(offer.canonical_url || "").hostname.replace(/^www\./, "").toLowerCase(); }
                      catch { return offer.store?.hostname?.replace(/^www\./, "").toLowerCase() ?? ""; }
                    })();
                    return (key && store.id.toLowerCase() === key)
                      || (offerHost && (store.domains ?? [store.hostname]).some((domain) => domain.toLowerCase() === offerHost))
                      || store.name.localeCompare(label, "pt-BR", { sensitivity: "base" }) === 0;
                  });
                  const showPromoPrice = Boolean(offer.promotion_commercially_active);
                  const isBrl = (offer.currency || "BRL").toUpperCase() === "BRL";
                  return (
                    <article className={`persisted-offer-row ${isBest ? "is-best" : ""}`} key={row.key}>
                      <div className="persisted-offer-image">
                        <ImageWithState src={offer.image_url || primaryImage?.image_url || ""} alt={product.name} />
                      </div>
                      <div className="persisted-offer-main">
                        <OfferPrice
                          offer={offer}
                          isBest={isBest}
                          showPixLabel={offer.pix_price != null}
                          showPromoLabel={showPromoPrice}
                        />
                        <div className="persisted-offer-conditions">
                          {isBrl && offer.original_price != null && offer.original_price !== price && (
                            <span>De {money(offer.original_price, offer.currency)}</span>
                          )}
                          {isBrl && offer.card_price != null && offer.card_price !== price && (
                            <span>Cartão: {money(offer.card_price, offer.currency)}</span>
                          )}
                          {offer.installments != null && offer.installment_value != null && (
                            <span>
                              {offer.installments}x de {money(offer.installment_value, offer.currency)}
                              {offer.interest_free === true ? " sem juros" : ""}
                            </span>
                          )}
                        </div>
                        <div className="persisted-offer-meta">
                          {offer.discount_percent != null && offer.discount_percent > 0 && <span>Desconto de {offer.discount_percent.toFixed(0)}%</span>}
                          {offer.seller && <span>Vendido por {offer.seller}</span>}
                          {offer.delivery_responsible && offer.delivery_responsible.toLowerCase() !== (offer.seller || "").toLowerCase() && <span>Entrega: {offer.delivery_responsible}</span>}
                          {offer.available === false && <span>Indisponível</span>}
                        </div>
                        {row.partial && <span className={matchStyles.label}>Resultado parcial encontrado nesta busca; oferta do catálogo preservada</span>}
                        <OfferMonitoringMeta offer={offer} onExpired={onRefreshOffers} />
                      </div>
                      <div className="persisted-offer-store">
                        <div className="persisted-store-identity">
                          {matchingStore?.logo_url || matchingStore?.logo_svg
                            ? <ImageWithState className="persisted-store-logo" src={matchingStore.logo_url ?? storeLogoDataUrl(matchingStore.logo_svg!)} alt={`Logo de ${matchingStore.name}`} showFallbackLabel={false} />
                            : <span className="persisted-store-mark" aria-hidden="true">{storeInitials(label)}</span>}
                          <strong>{label}</strong>
                          {countryCode && <span className="persisted-store-country" aria-label={countryCode === "BR" ? "Brasil" : "Estados Unidos"}>{countryCode}</span>}
                        </div>
                        {offer.canonical_url && <a className="store-offer-link" href={offer.canonical_url} target="_blank" rel="noopener noreferrer">Ir à loja <Icon name="arrow" /></a>}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
            <ReviewOffers offers={reviewOffers} />
          </section>
        );
      })}
      {product.variants.length === 0 && liveResults.map((result) => <ProgressiveOfferRow key={result.id} result={result} run={observedRun} />)}
      {product.variants.length === 0 && liveResults.length === 0 && (
        <div className="persisted-offers-empty">
          <p>Nenhuma oferta encontrada.</p>
          <button className="outline-button" type="button" onClick={onSearch} disabled={searchBusy}>{searchLabel}</button>
        </div>
      )}
    </div>
  );
});

export default function ProductAdminPage({ params }: { params: Promise<{ id: string }> }) {
  const router = useRouter();
  const [id, setId] = useState("");
  const [product, setProduct] = useState<Product | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [images, setImages] = useState<ProductImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [lightbox, setLightbox] = useState<ProductImage | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<ProductImage | null>(null);
  const [deleteProcessing, setDeleteProcessing] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [productDeletePrompt, setProductDeletePrompt] = useState(false);
  const [productDeleteProcessing, setProductDeleteProcessing] = useState(false);
  const [exitPrompt, setExitPrompt] = useState(false);
  const [newImageUrl, setNewImageUrl] = useState("");
  const [addingImage, setAddingImage] = useState(false);
  async function load(productId: string) {
    setLoading(true); setError("");
    try { const result = await catalogApi.getProduct(productId); setProduct(result); setDraft(result); setImages(result.images.map(toGalleryImage)); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Não foi possível carregar o produto."); } finally { setLoading(false); }
  }

  useEffect(() => { void params.then((value) => { setId(value.id); void load(value.id); }); }, [params]);

  const hasPendingImages = Boolean(product?.images.some((image) =>
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

  const [matchHistoryKey, setMatchHistoryKey] = useState(0);
  const refreshGenerationRef = useRef(0);
  const productIdRef = useRef(id);
  useEffect(() => {
    productIdRef.current = id;
    refreshGenerationRef.current += 1;
    return () => { refreshGenerationRef.current += 1; };
  }, [id]);
  const refreshPersistedOffers = useCallback(async (run?: MatchRunStatusView) => {
    if (!id) return;
    const generation = ++refreshGenerationRef.current;
    let result: Awaited<ReturnType<typeof catalogApi.getProduct>> | undefined;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        result = await catalogApi.getProduct(id);
        break;
      } catch (err) {
        if (productIdRef.current !== id || generation !== refreshGenerationRef.current) return;
        if (attempt === 2) throw err;
        await new Promise((resolve) => window.setTimeout(resolve, 1000 * (attempt + 1)));
      }
      if (productIdRef.current !== id || generation !== refreshGenerationRef.current) return;
    }
    if (!result) return;
    if (productIdRef.current !== id || generation !== refreshGenerationRef.current) return;
    // Atualiza somente ofertas; preserva formulário e galeria em edição.
    setProduct((current) => current ? { ...current, variants: result.variants } : result);
    setDraft((current) => ({ ...current, variants: result.variants }));
    if (run) setMatchHistoryKey((key) => key + 1);
  }, [id]);
  const matchRun = useProductMatchRun(id, { onTerminal: refreshPersistedOffers });
  const searchingOtherStorePrices = matchRun.isActive || matchRun.isStarting;
  const matchButtonDisabled = matchRun.isActive || matchRun.isStarting || !id;
  const matchButtonLabel =
    matchRun.isStarting
      ? "Solicitando busca..."
      : matchRun.isActive
        ? "Busca em andamento"
        : "Buscar preços em outras lojas";
  const primary = useMemo(() => product ? resolvePrimaryImage(product, images) : null, [images, product]);
  const createdAtDate = product?.created_at ? new Date(product.created_at) : null;
  const createdAtLabel = createdAtDate && !Number.isNaN(createdAtDate.getTime())
    ? new Intl.DateTimeFormat("pt-BR", { dateStyle: "long" }).format(createdAtDate)
    : "Data não informada";
  const hasUnsavedChanges = useMemo(() => Boolean(product && JSON.stringify(draft) !== JSON.stringify(product)), [draft, product]);
  const update = (key: string, value: string) => setDraft((current) => ({ ...current, [key]: value === "" ? null : ["number"].includes(fields.find((field) => field.key === key)?.type || "") ? Number(value) : value }));

  async function save() {
    if (!id) return;
    setSaving(true); setNotice(""); setError("");
    const editableKeys = ["canonical_name", "brand", "model", "category"];
    const payload = Object.fromEntries(editableKeys.map((key) => [key, draft[key] ?? null]));
    try {
      const result = await catalogApi.updateProduct(id, payload);
      setProduct(result.product); setDraft(result.product); setNotice("Alterações salvas no produto canônico.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível salvar as alterações.");
    } finally { setSaving(false); }
  }
  async function arrange(imageIds: string[], primaryImageId?: string) {
    const selectedPrimary = primaryImageId || primary?.id;
    if (!selectedPrimary) return;
    const currentImages = images;
    const nextImages = imageIds.map((imageId) => currentImages.find((image) => image.id === imageId)).filter((image): image is ProductImage => Boolean(image));
    if (nextImages.length !== currentImages.length) {
      setError("Não foi possível preservar todas as imagens da galeria.");
      return;
    }
    const optimisticImages = nextImages.map((image) => ({ ...image, is_main: image.id === selectedPrimary }));
    setImages(optimisticImages);
    setProduct((current) => current ? {
      ...current,
      primary_image_id: selectedPrimary,
      primary_image_url: optimisticImages.find((image) => image.id === selectedPrimary)?.image_url ?? current.primary_image_url,
    } : current);
    setError("");
    setNotice("");
    try {
      const result = await catalogApi.updateProductImages(id, imageIds, selectedPrimary);
      const persistedImages = result.images.map(toGalleryImage);
      setImages(persistedImages);
      setProduct((current) => current ? {
        ...current,
        primary_image_id: result.primary_image_id,
        primary_image_url: persistedImages.find((image) => image.id === result.primary_image_id)?.image_url ?? current.primary_image_url,
      } : current);
      setNotice("Imagem principal atualizada.");
    } catch {
      setError("Não foi possível atualizar a galeria.");
      await load(id);
    }
  }
  async function deleteImage(image: ProductImage) { setDeleteError(""); setDeleteCandidate(image); }
  async function confirmDeleteProduct() {
    if (!id || productDeleteProcessing) return;
    setProductDeleteProcessing(true); setError("");
    try {
      await catalogApi.deleteProduct(id);
      router.replace("/admin?view=products");
    } catch (deleteProductError) {
      setError(deleteProductError instanceof Error ? deleteProductError.message : "Não foi possível excluir o produto.");
      setProductDeleteProcessing(false);
    }
  }
  async function confirmDeleteImage() { const image = deleteCandidate; if (!image || deleteProcessing) return; setDeleteProcessing(true); setDeleteError(""); try { await catalogApi.deleteProductImage(id, image.id); const next = images.filter((item) => item.id !== image.id); setImages(next); if (image.id === product?.primary_image_id) { const nextPrimary = next[0]; if (nextPrimary) await arrange(next.map((item) => item.id), nextPrimary.id); else setProduct((current) => current ? { ...current, primary_image_id: null } : current); } setNotice("Imagem excluída com sucesso."); window.setTimeout(() => setDeleteCandidate(null), 450); } catch { setDeleteError("Não foi possível excluir a imagem. Tente novamente."); } finally { setDeleteProcessing(false); } }
  async function addImage(event: React.FormEvent) { event.preventDefault(); if (!newImageUrl.trim()) return; setAddingImage(true); try { const result = await catalogApi.addProductImage(id, newImageUrl.trim()); setImages((current) => [...current, result.image]); setNewImageUrl(""); setNotice("Imagem adicionada à galeria."); } catch (addError) { setError(addError instanceof Error ? addError.message : "Não foi possível adicionar a imagem."); } finally { setAddingImage(false); } }
  const searchOtherStorePrices = matchRun.start;
  const refreshOffers = useCallback(() => {
    void refreshPersistedOffers().catch((err: unknown) => setError(err instanceof Error ? err.message : "Não foi possível atualizar ofertas."));
  }, [refreshPersistedOffers]);
  const money = useCallback((value: number | null, currency: string) =>
    value == null ? "Não informado" : formatMoney(value, currency), []);
  function requestExit() { if (hasUnsavedChanges) setExitPrompt(true); else router.push("/admin?view=products"); }
  function exitWithoutSaving() { setExitPrompt(false); router.push("/admin?view=products"); }
  if (loading) return <main className="product-admin-shell"><p className="eyebrow">Produto</p><h1>Carregando área de gerenciamento...</h1></main>;
  if (!product) return <main className="product-admin-shell"><Link className="back-link" href="/admin"><Icon name="back" /> Voltar ao catálogo</Link><section className="empty-state"><h1>Produto indisponível</h1><p>{error || "O produto não foi encontrado."}</p></section></main>;
  return <main className="product-admin-shell"><header className="product-admin-header">
  <div className="product-title-block">
    <Link className="back-link" href="/admin"><Icon name="back" /> Catálogo</Link>
    <p className="eyebrow">Área central do produto</p>
    <h1>{product.name}</h1>
    <p className="product-id">ID interno <code>{product.id}</code></p>
  </div>
  <div className="product-admin-actions">
    <div className="product-edit-actions">
      <button className="outline-button product-cancel-button" type="button" onClick={requestExit}>Cancelar</button>
      <button className="primary-button" type="button" onClick={() => void save()} disabled={saving}><Icon name={saving ? "refresh" : "check"} /> {saving ? "Salvando..." : "Salvar alterações"}</button>
    </div>
    <button className="danger-button product-delete-action" type="button" onClick={() => setProductDeletePrompt(true)} disabled={productDeleteProcessing}><Icon name="trash" /> Excluir produto</button>
  </div>
</header>{productDeletePrompt && <div className="confirm-backdrop" role="presentation" onMouseDown={(event) => { if (!productDeleteProcessing && event.target === event.currentTarget) setProductDeletePrompt(false); }}><section className="confirm-modal product-delete-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-product-title" aria-describedby="delete-product-description"><div className="confirm-modal-preview product-delete-preview">{primary ? <ImageWithState src={primary.image_url} alt="" /> : <div className="product-delete-image-empty"><Icon name="image" /><span>Sem imagem de capa</span></div>}</div><div className="confirm-modal-content"><div className="confirm-modal-heading"><div><p className="eyebrow">Ação destrutiva</p><h2 id="delete-product-title">Excluir produto?</h2></div><button className="icon-action" type="button" onClick={() => setProductDeletePrompt(false)} disabled={productDeleteProcessing} aria-label="Fechar"><Icon name="close" /></button></div><div className="product-delete-identity"><strong>{product.name}</strong><span><b>Data de criação</b>{createdAtLabel}</span></div><p id="delete-product-description">Tem certeza de que deseja excluir este produto? A galeria de imagens e o histórico de preços relacionado também serão removidos.</p><div className="confirm-modal-actions"><button className="outline-button" type="button" onClick={() => setProductDeletePrompt(false)} disabled={productDeleteProcessing}>Cancelar</button><button className="danger-button" type="button" onClick={() => void confirmDeleteProduct()} disabled={productDeleteProcessing}>{productDeleteProcessing ? <><span className="loading-spinner" aria-hidden="true" /> Excluindo produto...</> : "Excluir produto"}</button></div></div></section></div>}{exitPrompt && <div className="confirm-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setExitPrompt(false); }}><section className="confirm-modal exit-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="exit-product-title" aria-describedby="exit-product-description"><div className="confirm-modal-content"><div className="confirm-modal-heading"><div><p className="eyebrow">Alterações pendentes</p><h2 id="exit-product-title">Sair sem salvar?</h2></div><button className="icon-action" type="button" onClick={() => setExitPrompt(false)} aria-label="Fechar"><Icon name="close" /></button></div><p id="exit-product-description">As alterações feitas nesta edição serão descartadas e você voltará para a lista de Produtos.</p><div className="confirm-modal-actions"><button className="outline-button" type="button" onClick={() => setExitPrompt(false)}>Continuar editando</button><button className="danger-button" type="button" onClick={exitWithoutSaving}>Sair sem salvar</button></div></div></section></div>}{error && <p className="error-notice" role="alert">{error}</p>}{notice && <p className="success-notice" role="status">{notice}</p>}{(matchRun.isActive || matchRun.isStarting || matchRun.isRecovering) && (
        <ProductMatchBanner
          starting={matchRun.isStarting}
          recovering={matchRun.isRecovering}
          elapsed={matchRun.elapsedLabel}
          storesCompleted={matchRun.observedRun?.stores_completed}
          storesTotal={matchRun.observedRun?.stores_total}
        />
      )}
      <section className="other-store-price-panel">
        <div className="other-store-price-heading">
          <div>
            <p className="eyebrow">Ofertas do produto</p>
            <h2>Compare onde comprar</h2>
          </div>
          <button className="outline-button" type="button" onClick={() => void searchOtherStorePrices()} disabled={matchButtonDisabled}>{matchButtonLabel}</button>
        </div>
        {matchRun.error && <p className="error-notice" role="alert">{matchRun.error}</p>}
        <ProductMatchStoreProgress stores={matchRun.storeProgress} recovering={matchRun.isRecovering} />
        <PersistedOffersList
          liveResults={matchRun.liveResults}
          observedRun={matchRun.observedRun}
          product={product}
          primaryImage={primary}
          money={money}
          onSearch={searchOtherStorePrices}
          searchBusy={searchingOtherStorePrices}
          searchLabel={matchButtonLabel}
          onRefreshOffers={refreshOffers}
        />
        {id ? <MatchRunHistory productId={id} refreshKey={matchHistoryKey} /> : null}
      </section><div className="product-admin-grid">
  <section className="product-admin-main">
    <AdminSection title="Informações gerais">
      <div className="product-fields">
        {fields.slice(0, 6).map((field) => <label key={field.key}>{field.label}<input type={field.type || "text"} value={draft[field.key] == null ? "" : String(draft[field.key])} onChange={(event) => update(field.key, event.target.value)} /></label>)}
      </div>
      <dl className="product-facts">
        <div><dt>Origem</dt><dd>{getStoreDisplayName(product.source)}</dd></div>
        <div><dt>ID externo</dt><dd>{product.external_product_id}</dd></div>
        <div><dt>Loja</dt><dd>{product.store?.name ? getStoreDisplayName(product.store.name) : "Não identificada"}</dd></div>
      </dl>
    </AdminSection>
  </section>
  <section className="product-admin-side">
    <AdminSection title="Imagens do produto">
      <div className="product-image-library">
        <div className="product-primary-image" aria-label="Imagem principal do produto">
          {primary ? <ImageWithState key={primary.image_url} src={primary.image_url} alt={product.name} availabilityStatus={primary.image_status} retryable={primary.image_retryable} warningCode={primary.image_warning_code} /> : <div className="product-primary-image-empty"><Icon name="image" /><span>Nenhuma imagem vinculada a este produto.</span></div>}
          <span className="product-image-caption">Imagem principal</span>
        </div>
        <form className="image-add-form" onSubmit={addImage}>
          <label>Adicionar imagem por URL<input type="url" value={newImageUrl} onChange={(event) => setNewImageUrl(event.target.value)} placeholder="Cole uma URL HTTPS de imagem" required /></label>
          <button className="outline-button" type="submit" disabled={addingImage}><Icon name="plus" /> {addingImage ? "Adicionando..." : "Adicionar imagem"}</button>
        </form>
        {images.length === 0 ? <div className="image-empty"><Icon name="image" /><p>Nenhuma imagem vinculada a este produto.</p></div> : <div className="product-gallery">{images.map((image, index) => <article className={`managed-image ${primary?.id === image.id ? "is-primary" : ""}`} key={image.id}><button className="managed-image-preview" type="button" onClick={() => setLightbox(image)}><ImageWithState src={image.image_url} alt={product.name} availabilityStatus={image.image_status} retryable={image.image_retryable} warningCode={image.image_warning_code} showFallbackLabel={false} allowRetry={false} /></button><div className="managed-image-meta"><div className="managed-image-heading"><span className="image-order">{String(index + 1).padStart(2, "0")}</span><strong>{primary?.id === image.id ? <><Icon name="check" /> Imagem principal</> : "Imagem vinculada"}</strong></div><div className="image-actions"><button className="primary-image-action" type="button" onClick={() => void arrange(images.map((item) => item.id), image.id)} disabled={primary?.id === image.id}>Definir como principal</button><button className="icon-action" type="button" onClick={() => void arrange(images.map((item) => item.id).toSpliced(index, 1).toSpliced(index - 1, 0, image.id))} aria-label="Mover para cima" disabled={index === 0}><Icon name="up" /></button><button className="icon-action" type="button" onClick={() => void arrange(images.map((item) => item.id).toSpliced(index, 1).toSpliced(index + 1, 0, image.id))} aria-label="Mover para baixo" disabled={index === images.length - 1}><Icon name="down" /></button><button className="icon-action danger-action" type="button" onClick={() => void deleteImage(image)} aria-label="Excluir imagem"><Icon name="trash" /></button></div></div></article>)}</div>}
      </div>
    </AdminSection>
  </section>
</div>{deleteCandidate && <div className="confirm-backdrop" role="presentation" onMouseDown={(event) => { if (!deleteProcessing && event.target === event.currentTarget) setDeleteCandidate(null); }}><section className="confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-image-title" aria-describedby="delete-image-description"><div className="confirm-modal-preview"><ImageWithState src={deleteCandidate.image_url} alt="" availabilityStatus={deleteCandidate.image_status} retryable={deleteCandidate.image_retryable} /></div><div className="confirm-modal-content"><div className="confirm-modal-heading"><div><p className="eyebrow">Ação destrutiva</p><h2 id="delete-image-title">Excluir imagem?</h2></div><button className="icon-action" type="button" onClick={() => setDeleteCandidate(null)} disabled={deleteProcessing} aria-label="Fechar"><Icon name="close" /></button></div><p className="confirm-modal-image-name">{deleteCandidate.name || "Imagem vinculada"}</p><p id="delete-image-description">Esta imagem será removida da galeria do produto. Essa ação não pode ser desfeita.</p>{deleteError && <p className="error-notice" role="alert">{deleteError}</p>}<div className="confirm-modal-actions"><button className="outline-button" type="button" onClick={() => setDeleteCandidate(null)} disabled={deleteProcessing}>Cancelar</button><button className="danger-button" type="button" onClick={() => void confirmDeleteImage()} disabled={deleteProcessing}>{deleteProcessing ? <><span className="loading-spinner" aria-hidden="true" /> Excluindo imagem...</> : "Excluir imagem"}</button></div></div></section></div>}<ImageViewer key={lightbox?.id || "closed"} images={images.map((image) => ({ id: image.id, src: image.image_url, alt: product.name, availabilityStatus: image.image_status, retryable: image.image_retryable }))} initialIndex={Math.max(0, images.findIndex((image) => image.id === lightbox?.id))} title={product.name} open={Boolean(lightbox)} onClose={() => setLightbox(null)} /></main>;
}

function AdminSection({ title, children }: { title: string; children: React.ReactNode }) { return <section className="admin-section"><div className="admin-section-heading"><h2>{title}</h2></div>{children}</section>; }
