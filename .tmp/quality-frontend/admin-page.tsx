"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { gsap } from "gsap";
import Link from "next/link";
import ShinyText from "@/components/ShinyText";
import ImageViewer, { ImageWithState } from "@/components/ImageViewer";
import StoreConfigurationForm, { storeLogoDataUrl as svgDataUrl } from "@/components/StoreConfigurationForm";
import { catalogApi } from "@/utils/api/services";
import { startVisiblePolling } from "@/utils/visible-polling";
import { ApiError } from "@/utils/api/client";
import { resolveMediaUrl } from "@/utils/api/product-images";
import { imageAvailabilityView } from "@/utils/display/image-availability";
import type { CatalogOverview as CatalogOverviewData, CatalogProduct, CatalogStore, ExistingCatalogProduct, ProductPreview } from "@/utils/api/contracts";
import {
  defaultIncludeImagesForStore,
  matchStoreByHostname,
  type StoreCapability,
} from "@/utils/storeCapabilities";
import { getStoreDisplayName } from "@/utils/store-display";
import { NotificationPanel } from "@/components/NotificationPanel";

type View = "overview" | "products" | "stores" | "product";
type ScrapeStatus = "idle" | "loading" | "ready" | "error";
type ImportStepStatus = "pending" | "active" | "completed" | "error";
type ImportStep = { id: string; label: string; status: ImportStepStatus };

function toStoreCapability(store: CatalogStore): StoreCapability {
  return {
    key: store.id,
    domains: store.domains?.length ? store.domains : store.hostname ? [store.hostname] : [],
    implemented: store.implemented !== false,
    supports_images: store.supports_images !== false,
    supports_search: store.supports_search,
    match_enabled: store.match_enabled,
    match_disabled_reason: store.match_disabled_reason,
    image_fetch_cost: store.image_fetch_cost ?? "low",
    default_include_images: store.default_include_images === true,
  };
}

function progressMessagesForPreview(includeImages: boolean): string[] {
  const base = [
    "Acessando página do produto...",
    "Identificando produto...",
    "Coletando preços e condições...",
    "Buscando marca e vendedor...",
  ];
  if (includeImages) {
    return [...base, "Buscando imagens...", "Preparando preview..."];
  }
  return [...base, "Preparando preview..."];
}

function duplicateProductFromError(error: unknown): ExistingCatalogProduct | null {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.details || typeof error.details !== "object") return null;
  const d = error.details as Record<string, unknown>;

  // Formato ScoutApiV2: error.details = {code, message, existing_product, ...}
  if ("existing_product" in d && d.existing_product && typeof d.existing_product === "object") {
    const ep = d.existing_product as Record<string, unknown>;
    return {
      id: String(ep.id ?? ""),
      name: ep.title ? String(ep.title) : ep.name ? String(ep.name) : null,
      brand: ep.brand != null ? String(ep.brand) : null,
      created_at: ep.created_at ? String(ep.created_at) : null,
    };
  }

  // Formato legado: error.details = {detail: {existing_product}}
  if ("detail" in d && d.detail && typeof d.detail === "object") {
    const inner = d.detail as { existing_product?: ExistingCatalogProduct };
    return inner.existing_product ?? null;
  }

  return null;
}

function formatCatalogDate(value?: string | null): string {
  if (!value) return "Data não informada";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Data não informada";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "long" }).format(date);
}

const importStepDefinitions = [
  { id: "request", label: "Enviando a prévia aprovada..." },
  { id: "processing", label: "Salvando produto no catálogo..." },
  { id: "refresh", label: "Atualizando o catálogo..." },
] as const;

function initialImportSteps(): ImportStep[] {
  return importStepDefinitions.map((step) => ({ ...step, status: "pending" }));
}

function parseHttpUrl(value: string): URL | null {
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

function Icon({ name }: { name: "grid" | "box" | "store" | "bell" | "plus" | "arrow" | "search" | "check" | "external" | "refresh" | "chevron-left" | "chevron-right" | "chevron-down" | "close" | "pencil" | "image" }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    box: <><path d="m4 7 8-4 8 4v10l-8 4-8-4V7Z" /><path d="m4 7 8 4 8-4M12 11v10" /></>,
    store: <><path d="M4 10v10h16V10M3 10l2-6h14l2 6" /><path d="M3 10c0 1.2 1 2 2.5 2S8 11.2 8 10c0 1.2 1 2 2.5 2s2.5-.8 2.5-2c0 1.2 1 2 2.5 2s2.5-.8 2.5-2c0 1.2 1 2 2.5 2s2.5-.8 2.5-2M9 20v-5h6v5" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
    search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    external: <><path d="M14 5h5v5M19 5l-8 8" /><path d="M19 14v5H5V5h5" /></>,
    refresh: <><path d="M20 11a8 8 0 0 0-14.7-4L3 10" /><path d="M3 5v5h5M4 13a8 8 0 0 0 14.7 4L21 14" /><path d="M21 19v-5h-5" /></>,
    "chevron-left": <path d="m15 18-6-6 6-6" />,
    "chevron-right": <path d="m9 18 6-6-6-6" />,
    "chevron-down": <path d="m6 9 6 6 6-6" />,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    pencil: <><path d="m4 20 4.2-.9L19 8.3a2 2 0 0 0-2.8-2.8L5.4 16.3 4 20Z" /><path d="m14.7 6.3 3 3" /></>,
    image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" /></>,
  };
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function Logo() { return <div className="logo"><span className="logo-mark"><span /></span><span>price<span className="logo-accent">scout</span></span></div>; }

function MediaImage({ src, alt, className = "", showFallbackLabel = true, availabilityStatus, retryable = false }: { src: string; alt: string; className?: string; showFallbackLabel?: boolean; availabilityStatus?: import("@/utils/api/contracts").ImageAvailabilityStatus; retryable?: boolean }) {
  return <ImageWithState src={src} alt={alt} className={className} showFallbackLabel={showFallbackLabel} availabilityStatus={availabilityStatus} retryable={retryable} allowRetry={false} />;
}

function StoreLogo({ store }: { store: CatalogStore }) {
  const [driveFailure, setDriveFailure] = useState<ReturnType<typeof imageAvailabilityView> | null>(null);
  const [svgFailure, setSvgFailure] = useState(false);
  const resolvedUrl = store.logo_url ? resolveMediaUrl(store.logo_url) : null;
  const svgUrl = store.logo_svg ? svgDataUrl(store.logo_svg) : null;

  if (resolvedUrl && !driveFailure) {
    return <ImageWithState src={resolvedUrl} alt={store.name} showFallbackLabel={false} allowRetry={false} onImageFailure={setDriveFailure} />;
  }
  if (svgUrl && !svgFailure) {
    return <span className="store-logo-fallback" title={driveFailure?.message}>
      <ImageWithState src={svgUrl} alt={store.name} showFallbackLabel={false} allowRetry={false} onImageFailure={() => setSvgFailure(true)} />
      {driveFailure && <><span className="store-logo-warning-marker" aria-hidden="true">!</span><span className="sr-only" role="status">{driveFailure.message} Exibindo a versão vetorial de backup.</span></>}
    </span>;
  }
  const failureMessage = driveFailure?.message ?? (svgFailure ? imageAvailabilityView("load_failed").message : "Nenhuma logo cadastrada.");
  return <span className="store-logo-unavailable" role="img" aria-label={failureMessage} title={failureMessage}><Icon name="store" /></span>;
}

const navItems: { id: View; label: string; icon: "grid" | "box" | "store" }[] = [
  { id: "overview", label: "Visão geral", icon: "grid" },
  { id: "products", label: "Produtos cadastrados", icon: "box" },
  { id: "stores", label: "Lojas cadastradas", icon: "store" },
  { id: "product", label: "Adicionar produto", icon: "box" },
];

export default function Home() {
  const [view, setView] = useState<View>("overview");
  const [notice, setNotice] = useState("");
  const [productUrl, setProductUrl] = useState("");
  const [scrapeStatus, setScrapeStatus] = useState<ScrapeStatus>("idle");
  const [scrapedProduct, setScrapedProduct] = useState<ProductPreview | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [scrapeError, setScrapeError] = useState("");
  const [existingProductId, setExistingProductId] = useState<string | null>(null);
  const [duplicateProduct, setDuplicateProduct] = useState<ExistingCatalogProduct | null>(null);
  const [approvedPreview, setApprovedPreview] = useState(false);
  const [discardPrompt, setDiscardPrompt] = useState(false);
  const [reviewedUrl, setReviewedUrl] = useState("");
  const [progressIndex, setProgressIndex] = useState(0);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [catalogProducts, setCatalogProducts] = useState<CatalogProduct[]>([]);
  const [catalogStores, setCatalogStores] = useState<CatalogStore[]>([]);
  const [editingStore, setEditingStore] = useState<CatalogStore | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [importing, setImporting] = useState(false);
  const importInFlightRef = useRef(false);
  const [importSteps, setImportSteps] = useState<ImportStep[]>(initialImportSteps);
  const [importError, setImportError] = useState("");
  const importPanelRef = useRef<HTMLDivElement | null>(null);
  const importSuccessRef = useRef<HTMLDivElement | null>(null);
  const previewRef = useRef<HTMLElement | null>(null);
  const [includeImagesOverride, setIncludeImagesOverride] = useState<boolean | null>(null);

  const hasProcessingStoreLogo = catalogStores.some(
    (store) => store.logo_processing_status === "pending" || store.logo_processing_status === "processing",
  );

  useEffect(() => {
    if (!hasProcessingStoreLogo) return;
    const polling = startVisiblePolling(async (signal) => {
      const result = await catalogApi.listStores({ signal });
      signal.throwIfAborted();
      setCatalogStores(result.stores ?? []);
    }, () => 3000, undefined, 3000);
    return () => polling.stop();
  }, [hasProcessingStoreLogo]);
  const hasPendingProductImages = catalogProducts.some((product) => product.images.some((image) =>
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

  const matchedStore = useMemo(() => {
    const parsed = parseHttpUrl(productUrl);
    if (!parsed) return null;
    return matchStoreByHostname(
      parsed.hostname,
      catalogStores.map(toStoreCapability),
    );
  }, [productUrl, catalogStores]);

  const includeImages = includeImagesOverride ?? defaultIncludeImagesForStore(matchedStore);

  useEffect(() => {
    if (!importing || !importPanelRef.current) return;
    gsap.fromTo(importPanelRef.current, { opacity: 0.72, y: 6 }, { opacity: 1, y: 0, duration: 0.35, ease: "power2.out" });
  }, [importing, importSteps]);

  useEffect(() => {
    const markImage = (image: HTMLImageElement, state: "loaded" | "error") => {
      image.classList.remove("image-loaded", "image-error");
      image.classList.add(`image-${state}`);
    };
    const handleImageLoad = (event: Event) => {
      const image = event.target;
      if (image instanceof HTMLImageElement) markImage(image, "loaded");
    };
    const handleImageError = (event: Event) => {
      const image = event.target;
      if (image instanceof HTMLImageElement) markImage(image, "error");
    };
    const scanCompleteImages = () => document.querySelectorAll<HTMLImageElement>("img").forEach((image) => {
      if (image.complete) markImage(image, image.naturalWidth > 0 ? "loaded" : "error");
    });
    const observer = new MutationObserver(scanCompleteImages);
    scanCompleteImages();
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("load", handleImageLoad, true);
    document.addEventListener("error", handleImageError, true);
    return () => {
      observer.disconnect();
      document.removeEventListener("load", handleImageLoad, true);
      document.removeEventListener("error", handleImageError, true);
    };
  }, [catalogProducts, scrapedProduct]);

  useEffect(() => {
    if (!scrapedProduct || !previewRef.current) {
      setPreviewVisible(false);
      return;
    }
    const previewElement = previewRef.current;
    const observer = new IntersectionObserver(([entry]) => {
      setPreviewVisible(entry.isIntersecting && entry.intersectionRatio >= 0.2);
    }, { threshold: [0, 0.2, 1] });
    observer.observe(previewElement);
    return () => observer.disconnect();
  }, [scrapedProduct]);

  useEffect(() => {
    const savedPreview = window.localStorage.getItem("pricescout:product-preview");
    const restorePreview = async () => {
      if (!savedPreview) return;
      try {
        const saved = JSON.parse(savedPreview) as { previewId?: string; product?: ProductPreview; expiresAt?: string; url?: string };
        if (saved.expiresAt && new Date(saved.expiresAt) <= new Date()) throw new Error("expired");
        let result: { product?: ProductPreview };
        let restoredPreviewId: string | null = null;
        try {
          if (saved.previewId) {
            result = await catalogApi.getPreview(saved.previewId);
            restoredPreviewId = saved.previewId;
          } else {
            result = { product: saved.product };
          }
        } catch {
          // O snapshot local continua utilizavel, mas a referencia remota nao.
          // Manter o previewId faria a proxima importacao consultar um registro
          // inexistente novamente em vez de usar o produto preservado.
          result = { product: saved.product };
        }
        if (!result.product) throw new Error("missing");
        setPreviewId(restoredPreviewId);
        window.localStorage.setItem("pricescout:product-preview", JSON.stringify({
          previewId: restoredPreviewId,
          product: result.product,
          expiresAt: restoredPreviewId ? saved.expiresAt : undefined,
          url: saved.url ?? result.product.url,
        }));
        setView("product");
        setProductUrl(saved.url ?? result.product.url);
        setReviewedUrl(saved.url ?? result.product.url);
        setScrapedProduct(result.product);
        setScrapeStatus("ready");
      } catch {
        window.localStorage.removeItem("pricescout:product-preview");
      }
    };
    void restorePreview();
    void Promise.all([catalogApi.listProducts(), catalogApi.listStores()]).then(([productsResult, storesResult]) => {
      setCatalogProducts(productsResult.products ?? []);
      setCatalogStores(storesResult.stores ?? []);
      setCatalogError("");
    }).catch(() => setCatalogError("Não foi possível carregar os registros do catálogo.")).finally(() => setCatalogLoading(false));
    const params = new URLSearchParams(window.location.search);
    const requestedView = params.get("view");
    if (requestedView === "overview" || requestedView === "products" || requestedView === "stores" || requestedView === "product") {
      window.setTimeout(() => setView(requestedView), 0);
    }
  }, []);

  async function reloadCatalog(): Promise<boolean> {
    try {
      const [productsResult, storesResult] = await Promise.all([catalogApi.listProducts(), catalogApi.listStores()]);
      setCatalogProducts(productsResult.products ?? []);
      setCatalogStores(storesResult.stores ?? []);
      setCatalogError("");
      return true;
    } catch {
      setCatalogError("Não foi possível atualizar os registros do catálogo.");
      return false;
    }
  }

  function revealPreview() {
    previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!parseHttpUrl(productUrl)) { setScrapeStatus("error"); setScrapeError("Informe um link de produto válido usando http:// ou https://."); return; }
    if (previewId) void catalogApi.discardPreview(previewId).catch(() => undefined);
    window.localStorage.removeItem("pricescout:product-preview");
    setPreviewId(null); setExistingProductId(null); setDuplicateProduct(null); setScrapeStatus("loading"); setScrapeError(""); setNotice(""); setScrapedProduct(null); setApprovedPreview(false); setProgressIndex(0);
    const messages = progressMessagesForPreview(includeImages);
    let currentProgressIndex = 0;
    const progressTimer = window.setInterval(() => {
      currentProgressIndex = (currentProgressIndex + 1) % messages.length;
      setProgressIndex(currentProgressIndex);
    }, 1200);
    try {
      const result = await catalogApi.previewProduct(productUrl, { includeImages });
      if (!result.product) throw new Error("A API não retornou a prévia do produto.");
      setProgressIndex(messages.length - 1);
      setScrapedProduct(result.product); setReviewedUrl(productUrl); setPreviewId(result.preview_id ?? null); setScrapeStatus("ready");
      window.localStorage.setItem("pricescout:product-preview", JSON.stringify({ previewId: result.preview_id, product: result.product, expiresAt: result.expires_at, url: productUrl }));
      window.setTimeout(revealPreview, 80);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Não foi possível coletar os dados do produto.";
      const duplicate = duplicateProductFromError(error);
      setExistingProductId(duplicate?.id ?? null);
      setDuplicateProduct(duplicate);
      setScrapeStatus("error"); setScrapeError(duplicate ? "Produto já cadastrado. A prévia não pode ser importada novamente." : `Falha na etapa “${messages[currentProgressIndex]}” ${message}`);
    } finally {
      window.clearInterval(progressTimer);
    }
  }

  async function approvePreview() {
    if (!scrapedProduct || importing || importInFlightRef.current || approvedPreview || duplicateProduct) return;
    importInFlightRef.current = true;
    setImporting(true);
    setImportError("");
    setNotice("");
    setImportSteps(initialImportSteps());
    const setStep = (id: string, status: ImportStepStatus) => setImportSteps((steps) => steps.map((step) => step.id === id ? { ...step, status } : step));
    const completeStep = (id: string) => { setStep(id, "completed"); };
    let currentStepId = "request";
    try {
      currentStepId = "request";
      setStep(currentStepId, "active");
      completeStep("request");
      currentStepId = "processing";
      setStep(currentStepId, "active");
      const result = await catalogApi.importProduct(scrapedProduct, previewId ?? undefined);
      completeStep("processing");
      currentStepId = "refresh";
      setStep(currentStepId, "active");
      if (!await reloadCatalog()) throw new Error("As tabelas do catálogo não confirmaram a atualização.");
      completeStep("refresh");
      setApprovedPreview(true);
      setNotice(result.message || "Produto importado e confirmado pela API.");
      window.setTimeout(() => {
        setImporting(false);
        importInFlightRef.current = false;
        if (importSuccessRef.current) gsap.fromTo(importSuccessRef.current, { opacity: 0, y: 8, scale: 0.98 }, { opacity: 1, y: 0, scale: 1, duration: 0.45, ease: "power2.out" });
      }, 420);
      window.setTimeout(() => {
        setProductUrl("");
        setScrapedProduct(null);
        setPreviewId(null);
        setExistingProductId(null);
        setDuplicateProduct(null);
        window.localStorage.removeItem("pricescout:product-preview");
        setScrapeStatus("idle");
        setScrapeError("");
        setApprovedPreview(false);
        setReviewedUrl("");
        setNotice("");
        setImportError("");
        setImportSteps(initialImportSteps());
        setProgressIndex(0);
        setPreviewVisible(false);
      }, 2100);
    } catch (error) {
      setStep(currentStepId, "error");
      const message = error instanceof Error ? error.message : "Não foi possível concluir a importação.";
      const duplicate = duplicateProductFromError(error);
      setExistingProductId(duplicate?.id ?? null);
      setDuplicateProduct(duplicate);
      if (duplicate) {
        if (previewId) void catalogApi.discardPreview(previewId).catch(() => undefined);
        window.localStorage.removeItem("pricescout:product-preview");
        setPreviewId(null);
      }
      setImportError(duplicate ? "Produto já cadastrado. A importação foi interrompida." : message);
      setNotice(duplicate ? "A importação foi interrompida porque o produto já está cadastrado." : "A importação foi interrompida. Os dados da prévia foram preservados para nova tentativa.");
      setImporting(false);
      importInFlightRef.current = false;
    }
  }

  function changeProductUrl(value: string) {
    setProductUrl(value);
    setIncludeImagesOverride(null);
    if (value !== reviewedUrl) {
      setExistingProductId(null);
      setDuplicateProduct(null);
    }
    if (scrapedProduct && !discardPrompt && value !== reviewedUrl) setDiscardPrompt(true);
  }

  function keepPreviousProduct() {
    setProductUrl(reviewedUrl);
    setDiscardPrompt(false);
  }

  function discardPreviousProduct() {
    if (previewId) void catalogApi.discardPreview(previewId).catch(() => undefined);
    window.localStorage.removeItem("pricescout:product-preview");
    setScrapedProduct(null);
    setPreviewId(null);
    setExistingProductId(null);
    setDuplicateProduct(null);
    setApprovedPreview(false);
    setReviewedUrl("");
    setDiscardPrompt(false);
    setScrapeStatus("idle");
    setProgressIndex(0);
    setPreviewVisible(false);
    setNotice("");
  }

  async function saveStore(input: { name: string; url: string; country: string; currency: string; logo_svg?: string | null; logo_file?: File | null }) {
    if (!editingStore) return;
    try {
      if (input.logo_file) await catalogApi.uploadStoreLogo(editingStore.id, input.logo_file);
      await catalogApi.updateStore(editingStore.id, input);
      await reloadCatalog();
      setEditingStore(null);
      setNotice("Loja atualizada com sucesso.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Não foi possível atualizar a loja.");
      throw error;
    }
  }

  function openView(nextView: View) {
    setView(nextView);
    setNotice("");
    setScrapeError("");
    const params = new URLSearchParams(window.location.search);
    params.set("view", nextView);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
  }

  return <main className="admin-shell">
    <aside className="sidebar"><div className="sidebar-top"><Logo /><span className="admin-badge">admin</span></div><nav className="main-nav" aria-label="Navegação principal"><p className="nav-label">Workspace</p>{navItems.map((item) => <button key={item.id} className={`nav-item ${view === item.id ? "active" : ""}`} onClick={() => openView(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === "overview" && <span className="nav-arrow"><Icon name="arrow" /></span>}</button>)}</nav><div className="sidebar-note"><span className="live-dot" /><div><strong>API conectada</strong><span>Operações centralizadas</span></div></div><div className="sidebar-bottom"><div className="user-profile"><span className="avatar">--</span><span><strong>Conta administrativa</strong><small>Identidade não carregada</small></span></div></div></aside>
    <section className="content-shell"><header className="topbar"><div className="mobile-logo"><Logo /></div><div className="search-box"><Icon name="search" /><input aria-label="Buscar no PriceScout" placeholder="Buscar no catálogo" /></div><div className="topbar-actions"><NotificationPanel /><div className="topbar-divider" /><span className="environment">API externa</span></div></header><div className="content">{catalogError && <p className="error-notice" role="status">{catalogError}</p>}{view === "overview" ? <Overview openView={openView} /> : view === "products" ? <CatalogView kind="products" openView={openView} products={catalogProducts} stores={catalogStores} loading={catalogLoading} /> : view === "stores" ? <CatalogView kind="stores" products={catalogProducts} stores={catalogStores} loading={catalogLoading} onEditStore={setEditingStore} /> : <FormView notice={notice} onSubmit={submitProduct} productUrl={productUrl} onProductUrlChange={changeProductUrl} scrapeStatus={scrapeStatus} scrapedProduct={scrapedProduct} scrapeError={scrapeError} existingProductId={existingProductId} duplicateProduct={duplicateProduct} approvedPreview={approvedPreview} onApprove={approvePreview} progressMessage={progressMessagesForPreview(includeImages)[progressIndex] ?? "Preparando preview..."} previewVisible={previewVisible} onRevealPreview={revealPreview} previewRef={previewRef} importing={importing} importSteps={importSteps} importError={importError} importPanelRef={importPanelRef} importSuccessRef={importSuccessRef} matchedStore={matchedStore} includeImages={includeImages} onIncludeImagesChange={(value) => { setIncludeImagesOverride(value); }} />}</div>{discardPrompt && scrapedProduct && <DiscardDialog product={scrapedProduct} onKeep={keepPreviousProduct} onDiscard={discardPreviousProduct} />}{editingStore && <div className="store-editor-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingStore(null); }}><section className="store-editor" role="dialog" aria-modal="true" aria-labelledby="store-editor-title"><div className="store-editor-heading"><div className="store-editor-header-text"><div className="store-editor-kicker"><Icon name="store" /><span>Loja cadastrada</span></div><h2 id="store-editor-title">Editar loja</h2></div><button className="store-editor-close lightbox-close" type="button" onClick={() => setEditingStore(null)} aria-label="Fechar edição"><Icon name="close" /></button></div><StoreConfigurationForm store={editingStore} onCancel={() => setEditingStore(null)} onSave={saveStore} /></section></div>}</section>
  </main>;
}

function Overview({ openView }: { openView: (view: View) => void }) {
  const [data, setData] = useState<CatalogOverviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    void catalogApi.getOverview().then((result) => { setData(result); setError(""); }).catch(() => setError("Nao foi possivel carregar os indicadores do catalogo.")).finally(() => setLoading(false));
  }, []);
  if (loading) return <section className="empty-state" role="status"><h2>Carregando visao geral...</h2><p>Consultando os registros atuais do catalogo.</p></section>;
  if (!data) return <><div className="page-intro"><div><p className="eyebrow">Visao geral</p><h1>Seu catalogo em uma leitura.</h1></div><button className="primary-button" onClick={() => openView("product")}><Icon name="plus" /> Adicionar produto</button></div><section className="empty-state" role="alert"><h2>Nao foi possivel carregar a visao geral</h2><p>{error}</p></section></>;
  const { summary, recent_products, best_price_opportunities, single_offer_variants, alerts, recent_activity } = data;
  const hasRecords = summary.products + summary.variants + summary.offers + summary.stores > 0;
  const date = (value: string) => formatCatalogDate(value);
  if (!hasRecords) return <><div className="page-intro"><div><p className="eyebrow">Visao geral</p><h1>Seu catalogo comeca aqui.</h1><p className="intro-copy">Adicione um produto para comecar a acompanhar o catalogo.</p></div><button className="primary-button" onClick={() => openView("product")}><Icon name="plus" /> Adicionar produto</button></div><section className="empty-state"><span className="empty-icon"><Icon name="grid" /></span><h2>Nenhum dado disponivel</h2><p>Os indicadores aparecerao aqui quando houver registros reais confirmados.</p><div className="empty-actions"><button className="primary-button" onClick={() => openView("product")}><Icon name="plus" /> Adicionar produto</button></div></section></>;
  return <div className="overview-page"><div className="page-intro"><div><p className="eyebrow">Visao geral</p><h1>Seu catalogo em uma leitura.</h1><p className="intro-copy">Acompanhe produtos, cobertura de ofertas e oportunidades calculadas a partir dos dados persistidos.</p></div><div className="overview-actions"><button className="primary-button" onClick={() => openView("product")}><Icon name="plus" /> Adicionar produto</button></div></div>{error && <p className="error-notice" role="status">{error} Os demais dados continuam disponiveis.</p>}<section className="overview-metrics" aria-label="Resumo do catalogo">{[["Produtos", summary.products, "box"], ["Variacoes", summary.variants, "grid"], ["Ofertas", summary.offers, "arrow"], ["Lojas", summary.stores, "store"]].map(([label, value, icon]) => <article className="overview-metric" key={String(label)}><div><span>{label}</span><Icon name={icon as "box" | "grid" | "arrow" | "store"} /></div><strong>{value}</strong></article>)}</section><div className="overview-columns"><section className="overview-panel"><div className="section-heading"><div><p className="eyebrow">Catalogo</p><h2>Produtos recentes</h2></div><button className="text-button" onClick={() => openView("products")}>Ver produtos <Icon name="arrow" /></button></div>{recent_products.length ? <div className="overview-list">{recent_products.map((product) => <Link className="overview-product" href={`/admin/produtos/${product.id}`} key={product.id}><div className="overview-product-image">{product.image_url ? <MediaImage src={product.image_url} alt="" showFallbackLabel={false} /> : <Icon name="image" />}</div><div><strong>{product.name}</strong><span>{product.brand || "Marca nao informada"}</span><small>{date(product.created_at)} · {product.variants} variacoes · {product.offers} ofertas</small></div><Icon name="arrow" /></Link>)}</div> : <p className="overview-muted">Nenhum produto recente.</p>}</section><section className="overview-panel"><div className="section-heading"><div><p className="eyebrow">Comparacao</p><h2>Melhores oportunidades</h2></div></div>{best_price_opportunities.length ? <div className="overview-list">{best_price_opportunities.map((item) => <article className="overview-opportunity" key={item.variant_id}><div><strong>{item.product_name}</strong><span>{item.variant_label || "Variacao unica"}</span>{item.offers.slice(0, 3).map((offer) => <small key={`${item.variant_id}-${offer.store_name}`}>{offer.store_name || "Loja"}: {money(offer.price)}</small>)}</div><b>Economia {money(item.savings)}</b></article>)}</div> : <p className="overview-muted">Ainda nao ha duas ofertas comparaveis na mesma variacao.</p>}</section></div><div className="overview-columns"><section className="overview-panel"><div className="section-heading"><div><p className="eyebrow">Cobertura</p><h2>Precisam de comparacao</h2></div></div>{single_offer_variants.length ? <div className="overview-list">{single_offer_variants.map((item) => <Link className="overview-simple-row" href={`/admin/produtos/${item.product_id}`} key={item.variant_id}><span><strong>{item.product_name}</strong><small>{item.variant_label || "Variacao unica"} · {item.store_name || "Uma loja associada"}</small></span><Icon name="arrow" /></Link>)}</div> : <p className="overview-muted">Nao ha variacoes com apenas uma oferta.</p>}</section><section className="overview-panel"><div className="section-heading"><div><p className="eyebrow">Saude do catalogo</p><h2>Alertas reais</h2></div></div>{alerts.length ? <div className="overview-list">{alerts.map((alert) => <button className="overview-simple-row overview-alert" key={alert.kind} onClick={() => openView("products")}><span><strong>{alert.label}</strong><small>{alert.count} registro(s) identificado(s)</small></span><Icon name="arrow" /></button>)}</div> : <p className="overview-muted">Nenhum alerta identificado pelos dados atuais.</p>}</section></div><section className="overview-panel overview-activity"><div className="section-heading"><div><p className="eyebrow">Timestamps persistidos</p><h2>Atividade recente</h2></div></div>{recent_activity.length ? <div className="overview-list">{recent_activity.map((item, index) => <div className="overview-simple-row" key={`${item.kind}-${item.product_id}-${item.occurred_at}-${index}`}><span><strong>{item.label}</strong><small>{item.product_name || "Catalogo"} · {date(item.occurred_at)}</small></span></div>)}</div> : <p className="overview-muted">Ainda nao ha timestamps suficientes para atividade.</p>}</section></div>;
}

function money(value: number | null) {
  return value === null ? "—" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
}

function CatalogView({ kind, openView, products, stores, loading, onEditStore = () => undefined }: { kind: "products" | "stores"; openView?: (view: View) => void; products: CatalogProduct[]; stores: CatalogStore[]; loading: boolean; onEditStore?: (store: CatalogStore) => void }) {
  const isProducts = kind === "products";
  const rows = isProducts ? products : stores;
  return <div className="catalog-page"><div className="page-intro"><div><p className="eyebrow">Catálogo</p><h1>{isProducts ? "Produtos cadastrados." : "Lojas cadastradas."}</h1><p className="intro-copy">{isProducts ? "Identifique rapidamente cada produto pela imagem e pelo nome." : "Consulte as lojas e sites confirmados como fontes oficiais."}</p></div>{isProducts && openView && <button className="primary-button" onClick={() => openView("product")}><Icon name="plus" /> Adicionar produto</button>}</div>{loading ? <section className="empty-state"><h2>Carregando registros...</h2></section> : rows.length === 0 ? <section className="empty-state"><span className="empty-icon"><Icon name={isProducts ? "box" : "store"} /></span><h2>Nenhum registro disponível</h2><p>{isProducts ? "Os produtos aparecerão aqui depois que forem cadastrados e aprovados." : "As lojas pré-cadastradas aparecerão aqui quando estiverem disponíveis."}</p>{isProducts && openView && <button className="outline-button" onClick={() => openView("product")}><Icon name="plus" /> Cadastrar produto</button>}</section> : isProducts ? <section className="catalog-grid" aria-label="Produtos cadastrados">{products.map((product) => { const firstOffer = product.variants.flatMap((variant) => variant.offers)[0]; const image = product.primary_image_url ?? product.images.find((item) => item.is_main)?.display_url ?? product.images[0]?.display_url ?? null; const noImageMessage = imageAvailabilityView(product.primary_image_status ?? "missing").message; const offerUrl = firstOffer?.canonical_url ?? null; return <article className="catalog-card" key={product.id}><Link className="catalog-card-manage" href={`/admin/produtos/${product.id}`} aria-label={`Gerenciar ${product.canonical_name}`}><div className="catalog-card-media">{image ? <MediaImage key={image} src={image} alt="" className="catalog-media-image" /> : <span className="catalog-card-media-empty" role="status"><span className="media-empty-badge"><Icon name="image" /></span><span className="media-empty-label">{noImageMessage}</span></span>}</div><div className="catalog-card-body"><h2>{product.canonical_name}</h2><span className="catalog-card-link">Gerenciar produto <Icon name="arrow" /></span></div></Link>{offerUrl ? <a className="catalog-card-source" href={offerUrl} target="_blank" rel="noreferrer">Abrir origem <Icon name="external" /></a> : <span className="catalog-card-source">Oferta sem URL disponível</span>}</article>; })}</section> : <section className="catalog-list">{stores.map((store) => <article className="catalog-row" key={store.id}><div className="catalog-row-icon"><StoreLogo store={store} /></div><div className="catalog-row-main"><strong>{store.name}</strong><span>{store.hostname}</span>{store.match_enabled === false ? <small className="store-match-disabled">Comparação temporariamente indisponível{store.match_disabled_reason ? ` (${store.match_disabled_reason})` : ""}</small> : null}</div><div className="catalog-row-meta"><b>{store.productCount} {store.productCount === 1 ? "produto" : "produtos"}</b><span>{store.country} · {store.currency}</span></div><button className="catalog-row-edit" type="button" onClick={() => onEditStore(store)} aria-label={`Editar ${store.name}`}><Icon name="pencil" /></button><a className="catalog-row-link" href={store.url} target="_blank" rel="noreferrer" aria-label={`Abrir ${store.name}`}><Icon name="external" /></a></article>)}</section>}</div>;
}

function FormView({ notice, onSubmit, productUrl, onProductUrlChange, scrapeStatus, scrapedProduct, scrapeError, existingProductId, duplicateProduct, approvedPreview, onApprove, progressMessage, previewVisible, onRevealPreview, previewRef, importing, importSteps, importError, importPanelRef, importSuccessRef, matchedStore, includeImages, onIncludeImagesChange }: { notice: string; onSubmit: (event: FormEvent<HTMLFormElement>) => void; productUrl: string; onProductUrlChange: (value: string) => void; scrapeStatus: ScrapeStatus; scrapedProduct: ProductPreview | null; scrapeError: string; existingProductId: string | null; duplicateProduct: ExistingCatalogProduct | null; approvedPreview: boolean; onApprove: () => void; progressMessage: string; previewVisible: boolean; onRevealPreview: () => void; previewRef: RefObject<HTMLElement | null>; importing: boolean; importSteps: ImportStep[]; importError: string; importPanelRef: RefObject<HTMLDivElement | null>; importSuccessRef: RefObject<HTMLDivElement | null>; matchedStore: StoreCapability | null; includeImages: boolean; onIncludeImagesChange: (value: boolean) => void }) {
  const detectedUrl = useMemo(() => parseHttpUrl(productUrl), [productUrl]);
  const showImageOption = Boolean(detectedUrl && matchedStore?.supports_images);
  const productFields = <>
    <label htmlFor="product-url">URL do produto<span className="required">Obrigatório</span></label>
    <input id="product-url" type="url" value={productUrl} onChange={(e) => onProductUrlChange(e.target.value)} placeholder="Cole o link oficial do produto" required />
    <p className="helper">A API identifica a loja e coleta os dados a partir do domínio informado.</p>
    {detectedUrl && <div className={`detection-box ${matchedStore ? "supported" : "unsupported"}`}><strong>{matchedStore ? "Loja reconhecida" : "Endereço válido"}</strong><span>{matchedStore ? matchedStore.key : detectedUrl.hostname}</span><small>{matchedStore ? "A fonte está no registry do crawler." : "A compatibilidade da fonte será validada pela API."}</small></div>}
    {showImageOption && (
      <label className="image-fetch-option" htmlFor="include-images">
        <input
          id="include-images"
          type="checkbox"
          checked={includeImages}
          disabled={scrapeStatus === "loading"}
          onChange={(event) => onIncludeImagesChange(event.target.checked)}
        />
        <span>
          <strong>Buscar imagens do produto</strong>
          <small>
            {matchedStore?.image_fetch_cost === "high"
              ? "Opcional — nesta loja a coleta de galeria pode ser mais lenta."
              : "Extrai apenas as URLs da galeria (sem download binário no preview)."}
          </small>
        </span>
      </label>
    )}
  </>;
  return <div className="form-page">
    <div className="breadcrumb"><span>Visão geral</span><span>/</span><span>Adicionar produto</span></div>
    <div className="form-layout">
      <div className="form-intro"><p className="eyebrow">Novo registro</p><h1>Cole o link. Revise o produto.</h1><p>A API consulta a página oficial e prepara uma prévia para sua aprovação.</p><div className="form-rail"><span className="rail-line" /><span>01</span><span>Coletar e revisar</span></div></div>
      <form className="data-form" onSubmit={onSubmit}>
        <div className="form-card"><div className="form-card-heading"><span className="form-number">01</span><div><h2>Link do produto</h2><p>O nome vem da página, nunca de um texto digitado manualmente.</p></div></div>{productFields}</div>
        {scrapeStatus === "loading" && <div className="scrape-progress" role="status" aria-live="polite"><span className="progress-pulse" /><ShinyText text={progressMessage} speed={2} delay={0} color="#728589" shineColor="#ffffff" spread={120} direction="left" yoyo={false} pauseOnHover={false} disabled={false} /></div>}
        <div className="form-footer"><span className="secure-note">A prévia não altera o catálogo canônico antes da sua aprovação.</span><button className="primary-button" type={scrapeStatus === "ready" ? "button" : "submit"} onClick={scrapeStatus === "ready" ? onRevealPreview : undefined} disabled={scrapeStatus === "loading"}><Icon name={scrapeStatus === "loading" ? "refresh" : scrapeStatus === "ready" && previewVisible ? "chevron-down" : "arrow"} /> {scrapeStatus === "loading" ? "Buscando produto" : scrapeStatus === "ready" && previewVisible ? "Prévia exibida abaixo" : scrapeStatus === "ready" ? "Ver prévia" : scrapeStatus === "error" ? "Tentar novamente" : "Buscar produto"}</button></div>
        {scrapeError && <p className="error-notice" role="alert">{scrapeError}{existingProductId && !duplicateProduct && <> <Link href={`/admin/produtos/${existingProductId}`}>Abrir produto cadastrado.</Link></>}</p>}
        {duplicateProduct && !scrapedProduct && <DuplicateProductNotice product={duplicateProduct} />}
        {notice && <p className="success-notice" role="status"><Icon name={approvedPreview ? "check" : "refresh"} /> {notice}</p>}
      </form>
    </div>
    {scrapedProduct && <><SpecialStatusSummary product={scrapedProduct} />{(importing || approvedPreview || importError) && <ImportProgress steps={importSteps} error={importError} blocked={Boolean(duplicateProduct)} panelRef={importPanelRef} successRef={importSuccessRef} />}{duplicateProduct && <DuplicateProductNotice product={duplicateProduct} />}<ReviewPreview product={scrapedProduct} approved={approvedPreview} onApprove={onApprove} previewRef={previewRef} importing={importing} blocked={Boolean(duplicateProduct)} /></>}
  </div>;
}

function DuplicateProductNotice({ product }: { product: ExistingCatalogProduct }) {
  const createdAtLabel = formatCatalogDate(product.created_at);
  const imageUrl = product.main_image_url || product.primary_image_url || null;

  return <section className="duplicate-product-notice" role="alert" aria-label="Produto já cadastrado">
    <div className="duplicate-product-media">
      {imageUrl ? <ImageWithState src={imageUrl} alt={product.name || "Produto já cadastrado"} /> : <span>Sem miniatura</span>}
    </div>
    <div className="duplicate-product-content">
      <p className="eyebrow">Produto já cadastrado</p>
      <h2>{product.name || "Produto sem nome"}</h2>
      <p>Cadastrado em {createdAtLabel}.</p>
      <Link className="outline-button duplicate-product-link" href={`/admin/produtos/${product.id}`}>Abrir produto no catálogo</Link>
    </div>
  </section>;
}

function ImportProgress({ steps, error, blocked, panelRef, successRef }: { steps: ImportStep[]; error: string; blocked: boolean; panelRef: RefObject<HTMLDivElement | null>; successRef: RefObject<HTMLDivElement | null> }) {
  const failedStep = steps.find((step) => step.status === "error");
  const activeStep = steps.find((step) => step.status === "active");
  const completed = steps.every((step) => step.status === "completed");
  return <section className={`import-progress-panel ${error ? "has-error" : completed ? "is-success" : ""}`} ref={panelRef} aria-live="polite"><div className="import-progress-heading"><div><p className="eyebrow">Importação do produto</p><h2>{error ? "A importação foi interrompida" : completed ? "Produto importado com sucesso" : "Importando produto..."}</h2><p>{error ? `Falha em: ${failedStep?.label || "etapa não identificada"}` : activeStep ? "A API está processando esta etapa." : "Todas as etapas foram confirmadas pela API."}</p></div><span className="import-progress-counter">{steps.filter((step) => step.status === "completed").length}/{steps.length}</span></div><div className="import-steps">{steps.map((step) => <div className={`import-step ${step.status}`} key={step.id}><span className="import-step-mark">{step.status === "completed" ? <Icon name="check" /> : step.status === "error" ? <Icon name="close" /> : step.status === "active" ? <span className="import-step-pulse" /> : <span />}</span><span className="import-step-label">{step.status === "active" ? <ShinyText text={step.label} speed={2} delay={0} color="#728589" shineColor="#ffffff" spread={120} direction="left" yoyo={false} pauseOnHover={false} disabled={false} /> : step.label.replace("...", "")}</span></div>)}</div>{completed && <div className="import-success" ref={successRef} role="status"><Icon name="check" /><strong>Produto importado com sucesso</strong></div>}{error && <p className="error-notice import-error" role="alert">{error} {blocked ? "A prévia foi preservada, mas a importação permanece bloqueada." : "Os dados da prévia foram preservados; corrija a configuração e tente novamente."}</p>}</section>;
}

function SpecialStatusSummary({ product }: { product: ProductPreview }) {
  if (!product.special_status && !product.shipping_from) return null;
  return <section className="special-status-summary" aria-label="Condição especial do produto"><div className="special-status-heading"><span>Condição especial</span><strong>{product.special_status || "Informação de envio"}</strong></div>{product.shipping_from && <div className="special-status-date"><span>Envio a partir de</span><strong>{product.shipping_from}</strong></div>}</section>;
}

function DiscardDialog({ product, onKeep, onDiscard }: { product: ProductPreview; onKeep: () => void; onDiscard: () => void }) {
  return <div className="discard-backdrop" role="presentation"><section className="discard-dialog" role="dialog" aria-modal="true" aria-labelledby="discard-title"><span className="discard-mark"><Icon name="refresh" /></span><p className="eyebrow">Prévia em revisão</p><h2 id="discard-title">Descartar o produto anterior?</h2><p>Você começou a informar outro link. A prévia de <strong>{product.name || "produto atual"}</strong> será removida desta sessão se continuar.</p><div className="discard-actions"><button className="outline-button" onClick={onKeep}>Manter produto</button><button className="primary-button" onClick={onDiscard}>Descartar prévia</button></div></section></div>;
}

function formatStoreName(source?: string | null, urlStr?: string): string {
  return getStoreDisplayName(source, urlStr);
}

function canonicalGalleryImageKey(value: string): string {
  try {
    const url = new URL(value.trim());
    url.search = "";
    url.hash = "";
    url.pathname = url.pathname
      .replace(/\/produto\/(?:p|m|g|gg)\//i, "/produto/g/")
      .replace(/\/(?:small|medium|large|xlarge)\//i, "/")
      .replace(/(?:[-_](?:\d+x\d+|\d+w|\d+px))(?=\.[^.\/]+$)/i, "");
    return `${url.hostname.toLowerCase()}${url.pathname.toLowerCase()}`;
  } catch {
    return value.trim().toLowerCase().split("?")[0].split("#")[0];
  }
}

function ReviewPreview({ product, approved, onApprove, previewRef, importing, blocked }: { product: ProductPreview; approved: boolean; onApprove: () => void; previewRef: RefObject<HTMLElement | null>; importing: boolean; blocked: boolean }) {
  const money = (value: number | null | undefined) =>
    value === null || value === undefined
      ? "—"
      : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);

  const galleryImages = useMemo(() => {
    const list: Array<{ url: string; alt: string | null }> = [];
    const seen = new Set<string>();

    const addImg = (imgUrl?: string | null, altText?: string | null) => {
      if (!imgUrl || typeof imgUrl !== "string") return;
      const cleanUrl = imgUrl.trim();
      if (!cleanUrl) return;
      if (product.source.toLowerCase().includes("terabyte")) {
        try {
          const url = new URL(cleanUrl);
          if (url.hostname.toLowerCase() !== "img.terabyteshop.com.br" || !/^\/produto\/g\//i.test(url.pathname)) return;
        } catch {
          return;
        }
      }
      const key = canonicalGalleryImageKey(cleanUrl);
      if (!key || seen.has(key)) return;
      seen.add(key);
      list.push({ url: cleanUrl, alt: altText || product.name || "Imagem do produto" });
    };

    if (Array.isArray(product.images)) {
      for (const img of product.images) {
        if (img && img.url) {
          addImg(img.url, img.alt);
        }
      }
    }
    if (list.length === 0 && product.main_image) {
      addImg(product.main_image, product.name);
    }
    return list;
  }, [product.images, product.main_image, product.name, product.source]);

  const [selectedImageIndex, setSelectedImageIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const selectedImage = galleryImages[selectedImageIndex] ?? galleryImages[0] ?? null;

  function moveImage(direction: -1 | 1) {
    if (galleryImages.length < 2) return;
    setSelectedImageIndex((current) => (current + direction + galleryImages.length) % galleryImages.length);
  }

  const storeLabel = formatStoreName(product.source, product.url);

  return (
    <section className="review-preview" ref={previewRef} aria-live="polite">
      <div className="review-heading">
        <div>
          <p className="eyebrow">Prévia coletada</p>
          <h2>{product.name || "Produto sem nome identificado"}</h2>
          {Array.isArray(product.taxonomy) && product.taxonomy.length > 0 && (
            <div className="review-taxonomy" aria-label="Categorias do produto">
              {product.taxonomy.map((item, idx) => (
                <span key={item.slug || item.name || idx} className="review-taxonomy-item">
                  {item.name}
                </span>
              ))}
            </div>
          )}
          <p>Confira os dados extraídos da página antes de aprovar.</p>
          {(product.image_status === "error" || product.image_status === "omitted" || (product.image_status === "empty" && (product.images?.length ?? 0) === 0)) && (
            <p className="review-image-warning" role="status">
              Produto encontrado, mas não foi possível carregar a galeria
              {product.image_error ? `: ${product.image_error}` : "."}
            </p>
          )}
        </div>
        <a className="source-link" href={product.url} target="_blank" rel="noreferrer">
          Abrir página <Icon name="external" />
        </a>
      </div>

      <div className="review-grid">
        <div className="review-main">
          <div className="review-brand">
            <div className="review-brand-header">
              <div>
                <span>Marca</span>
                <strong className="review-brand-identity">{product.brand || "Não identificada"}</strong>
              </div>
            </div>

            <div className="review-identity-tags">
              <span className="review-tag">
                <b>Loja</b> {storeLabel}
              </span>
            </div>
          </div>

          <div className="review-fields">
            <div>
              <span>Preço original</span>
              <strong>{money(product.originalPrice)}</strong>
              <small>Valor de referência</small>
            </div>
            <div>
              <span>Preço no Pix</span>
              <strong className="review-accent">{money(product.pixPrice)}</strong>
              <small>
                {product.pixDiscountPercent === null || product.pixDiscountPercent === undefined
                  ? "—"
                  : `${product.pixDiscountPercent}% de desconto`}
              </small>
            </div>
            <div>
              <span>Preço no cartão</span>
              <strong>{money(product.cardPrice)}</strong>
              <small>
                {product.installments?.quantity && product.installments?.amount
                  ? `${product.installments.quantity}x de ${money(product.installments.amount)}`
                  : "—"}
              </small>
            </div>
            <div>
              <span>Economia no Pix</span>
              <strong>{money(product.priceDifferencePixToCard)}</strong>
              <small>Comparado ao cartão</small>
            </div>
          </div>

          <div className="review-meta">
            <span>
              <b>Vendido por</b>
              {product.seller || "Não identificado"}
            </span>
            <span>
              <b>Entregue por</b>
              {product.deliveryResponsible || product.seller || "Não identificado"}
            </span>
            {product.special_status && (
              <span>
                <b>Condição especial</b>
                {product.special_status}
              </span>
            )}
            {product.shipping_from && (
              <span>
                <b>Envio a partir de</b>
                {product.shipping_from}
              </span>
            )}
          </div>
        </div>

        <div className="review-media">
          <div className="gallery-stage">
            {selectedImage ? (
              <button
                className="gallery-main-button"
                type="button"
                onClick={() => setLightboxOpen(true)}
                aria-label={`Ampliar imagem ${selectedImageIndex + 1} de ${galleryImages.length}`}
              >
                <ImageWithState
                  src={selectedImage.url}
                  alt={selectedImage.alt || product.name || "Imagem do produto"}
                />
              </button>
            ) : (
              <div className="media-empty">Imagem não encontrada</div>
            )}
            {galleryImages.length > 1 && (
              <>
                <button
                  className="gallery-nav previous"
                  type="button"
                  onClick={() => moveImage(-1)}
                  aria-label="Imagem anterior"
                >
                  <Icon name="chevron-left" />
                </button>
                <button
                  className="gallery-nav next"
                  type="button"
                  onClick={() => moveImage(1)}
                  aria-label="Próxima imagem"
                >
                  <Icon name="chevron-right" />
                </button>
              </>
            )}
          </div>
          {galleryImages.length > 0 && (
            <div className="gallery-thumbnails" aria-label="Imagens do produto">
              {galleryImages.map((image, index) => (
                <button
                  className={`gallery-thumbnail ${index === selectedImageIndex ? "selected" : ""}`}
                  key={image.url}
                  type="button"
                  onClick={() => setSelectedImageIndex(index)}
                  aria-label={`Ver imagem ${index + 1}`}
                  aria-current={index === selectedImageIndex}
                >
                  <ImageWithState src={image.url} alt="" />
                </button>
              ))}
            </div>
          )}
          <span className="gallery-summary">
            {galleryImages.length === 1
              ? "1 imagem do produto encontrada"
              : `${galleryImages.length} imagens do produto encontradas`}
          </span>
        </div>
      </div>

      <div className="review-actions">
        <span className="persistence-note">
          {approved
            ? "Importação concluída; o registro canônico e as relações foram confirmados."
            : "A loja será vinculada pelo domínio e criada junto do produto quando o catálogo estiver configurado."}
        </span>
        <button
          className="primary-button"
          onClick={onApprove}
          disabled={approved || importing || blocked}
        >
          <Icon name={importing ? "refresh" : "check"} />{" "}
          {importing ? "Importando produto..." : approved ? "Prévia aprovada" : "Aprovar prévia"}
        </button>
      </div>

      <ImageViewer
        key={`${lightboxOpen ? "open" : "closed"}-${selectedImageIndex}`}
        images={galleryImages.map((image) => ({
          id: image.url,
          src: image.url,
          alt: image.alt || product.name || "Imagem do produto",
        }))}
        initialIndex={selectedImageIndex}
        title={product.name || "Produto sem nome identificado"}
        open={lightboxOpen}
        onClose={() => setLightboxOpen(false)}
      />
    </section>
  );
}
