import type { CatalogOffer, MatchStoreLiveView } from "./api/contracts";
import { sortOffersByComparableBrl } from "./money";

export type MatchOfferRow = {
  key: string;
  offer: CatalogOffer | null;
  partial: MatchStoreLiveView | null;
};

export function finitePositive(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const amount = Number(value);
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

function identity(store: string | null | undefined, country: string | null | undefined, product: string | null | undefined): string | null {
  return store && country && product ? JSON.stringify([store, country, product]) : null;
}

function partialIdentity(result: MatchStoreLiveView): string | null {
  return identity(result.matched_store, result.matched_country, result.matched_product_id);
}

export function isEquivalentOffer(offer: CatalogOffer, result: MatchStoreLiveView): boolean {
  if (result.matched_listing_id && result.matched_listing_id === offer.id) return true;
  const commercial = partialIdentity(result);
  if (commercial && commercial === identity(offer.source, offer.country, offer.external_product_id)) return true;
  return Boolean(result.matched_canonical_url && offer.canonical_url === result.matched_canonical_url
    && offer.source === result.matched_store && offer.country === result.matched_country);
}

export function mergeMatchOffers(offers: CatalogOffer[], results: MatchStoreLiveView[], terminal: boolean): MatchOfferRow[] {
  const confirmed = results.filter((s) => s.status === "match" && s.matched_decision === "auto_match");
  const remaining = new Set(confirmed);
  const rows: MatchOfferRow[] = offers.map((offer) => {
    const result = confirmed.find((s) => isEquivalentOffer(offer, s));
    if (result) remaining.delete(result);
    const key = (result && partialIdentity(result)) ?? identity(offer.source, offer.country, offer.external_product_id) ?? `listing:${offer.id}`;
    return { key, offer, partial: terminal ? null : result ?? null };
  });
  for (const result of remaining) {
    rows.push({ key: partialIdentity(result) ?? `result:${result.id}`, offer: null, partial: result });
  }
  return rows.sort((a, b) => sortOffersByComparableBrl(
    a.offer ?? { currency: a.partial?.matched_currency ?? "", original_price: finitePositive(a.partial?.matched_price), converted_price_brl: finitePositive(a.partial?.converted_price_brl) },
    b.offer ?? { currency: b.partial?.matched_currency ?? "", original_price: finitePositive(b.partial?.matched_price), converted_price_brl: finitePositive(b.partial?.converted_price_brl) },
  ));
}
