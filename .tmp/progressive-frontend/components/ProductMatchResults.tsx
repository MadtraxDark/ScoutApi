"use client";

import styles from "./ProductMatchResults.module.css";
import { memo } from "react";
import type { CatalogOffer, MatchRunStatusView, MatchStoreLiveView } from "@/utils/api/contracts";
import { finitePositive } from "@/utils/match-run-offers";
import { formatMoney } from "@/utils/money";
import { getStoreDisplayName } from "@/utils/store-display";
import { matchStoreStatusLabel } from "@/utils/display";

function publicUrl(value: string | null): string | null {
  try {
    const url = new URL(value ?? "");
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch { return null; }
}

export function ProductMatchStoreProgress({ stores, recovering }: { stores: MatchStoreLiveView[]; recovering: boolean }) {
  if (!stores.length) return null;
  return <section className={styles.progress} aria-label="Progresso da busca por loja">
    <h3>Progresso da busca</h3>
    {recovering && <p role="status">Aguardando recuperação da busca. Os resultados encontrados foram preservados.</p>}
    <ul>{stores.map((store) => <li key={store.id}>
      <strong>{getStoreDisplayName(store.store, store.matched_url, store.store_display_name)}</strong>
      <span>{store.matched_decision === "review" ? "Requer revisão" : matchStoreStatusLabel(store.status)}</span>
    </li>)}</ul>
  </section>;
}

export const ProgressiveOfferRow = memo(function ProgressiveOfferRow({ result, run }: { result: MatchStoreLiveView; run: MatchRunStatusView | null }) {
  const price = finitePositive(result.matched_price);
  const converted = finitePositive(result.converted_price_brl);
  const url = publicUrl(result.matched_canonical_url ?? result.matched_url);
  const interrupted = run?.status === "failed" || run?.status === "cancelled";
  return <article className={`persisted-offer-row ${styles.row}`}>
    <div className="persisted-offer-main">
      <strong className={styles.price}>{price == null ? "Preço não informado" : formatMoney(price, result.matched_currency)}</strong>
      {converted != null && <span className={styles.conversion}>Referência em BRL: {formatMoney(converted, "BRL")}{result.exchange_rate_status === "stale" ? " · Conversão aproximada" : ""}</span>}
      <p>{result.matched_title || "Produto encontrado"}</p>
      <span className={styles.label}>{interrupted ? "Busca interrompida — resultado não confirmado no catálogo" : run?.status === "completed" ? "Resultado ainda não reconciliado com o catálogo" : "Resultado parcial — encontrado nesta busca"}</span>
    </div>
    <div className="persisted-offer-store">
      <strong>{getStoreDisplayName(result.store, result.matched_url, result.store_display_name)}</strong>
      {url && <a className="store-offer-link" href={url} target="_blank" rel="noopener noreferrer">Ir à loja</a>}
    </div>
  </article>;
});

export function ReviewOffers({ offers }: { offers: CatalogOffer[] }) {
  if (!offers.length) return null;
  return <aside className={styles.progress} aria-label="Ofertas em revisão">
    <h3>Ofertas que exigem revisão</h3>
    <ul>{offers.map((offer) => <li key={offer.id}>
      <strong>{getStoreDisplayName(offer.source, offer.canonical_url, offer.store?.name, offer.country)}</strong>
      <span>Correspondência em revisão</span>
      {publicUrl(offer.canonical_url) && <a href={publicUrl(offer.canonical_url)!} target="_blank" rel="noopener noreferrer">Consultar oferta</a>}
    </li>)}</ul>
  </aside>;
}
