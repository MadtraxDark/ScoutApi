/**
 * Formatação monetária e valor comparável em BRL para ofertas multi-moeda.
 *
 * A conversão cambial vem do ScoutApiV2 (`converted_price_brl`).
 * O frontend não calcula taxa nem consulta provedores externos.
 */

export type ExchangeRateStatus = "fresh" | "stale" | "unavailable" | string;

export type OfferMoneyFields = {
  currency: string;
  pix_price?: number | null;
  card_price?: number | null;
  original_price?: number | null;
  converted_price_brl?: number | null;
  exchange_rate_status?: ExchangeRateStatus | null;
  exchange_rate_updated_at?: string | null;
};

const FRACTION_DIGITS: Record<string, number> = {
  BRL: 2,
  USD: 2,
  EUR: 2,
  PYG: 0,
};

export function normalizeCurrency(currency: string | null | undefined): string {
  const code = (currency || "BRL").trim().toUpperCase();
  return code || "BRL";
}

export function isBrlCurrency(currency: string | null | undefined): boolean {
  return normalizeCurrency(currency) === "BRL";
}

/**
 * Preço comercial “principal” na moeda da loja (Pix > cartão/price > original).
 */
export function rawOfferPrice(offer: OfferMoneyFields): number | null {
  for (const value of [offer.pix_price, offer.card_price, offer.original_price]) {
    if (value != null && Number.isFinite(value) && value > 0) return value;
  }
  return null;
}

/**
 * Valor usado para MENOR PREÇO e sorting entre moedas.
 * BRL: preço local. Estrangeiro: só `converted_price_brl` (nunca o raw estrangeiro).
 */
export function comparablePriceBrl(offer: OfferMoneyFields): number | null {
  if (isBrlCurrency(offer.currency)) {
    return rawOfferPrice(offer);
  }
  const converted = offer.converted_price_brl;
  if (converted != null && Number.isFinite(converted) && converted > 0) {
    return converted;
  }
  return null;
}

export function sortOffersByComparableBrl(
  left: OfferMoneyFields,
  right: OfferMoneyFields,
): number {
  const a = comparablePriceBrl(left);
  const b = comparablePriceBrl(right);
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a - b;
}

export function hasValidBrlConversion(offer: OfferMoneyFields): boolean {
  if (isBrlCurrency(offer.currency)) return true;
  return comparablePriceBrl(offer) != null;
}

/**
 * Formata valor monetário para exibição em pt-BR via Intl.NumberFormat.
 * Símbolos estáveis: BRL → R$, USD → US$, PYG → ₲.
 */
export function formatMoney(
  value: number | null | undefined,
  currency: string | null | undefined = "BRL",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const code = normalizeCurrency(currency);
  const digits = FRACTION_DIGITS[code] ?? 2;
  const numeric = new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);

  if (code === "BRL") return `R$\u00a0${numeric}`;
  if (code === "USD") return `US$\u00a0${numeric}`;
  if (code === "PYG") return `₲ ${numeric}`;

  try {
    return new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency: code,
      currencyDisplay: "symbol",
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  } catch {
    return `${code} ${numeric}`;
  }
}

export function formatRelativePast(
  iso: string | null | undefined,
  nowMs: number = Date.now(),
): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return null;
  const deltaSec = Math.max(0, Math.floor((nowMs - then) / 1000));
  if (deltaSec < 60) return "há poucos segundos";
  const minutes = Math.floor(deltaSec / 60);
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `há ${hours} h`;
  const days = Math.floor(hours / 24);
  return `há ${days} d`;
}

export function conversionHint(
  offer: OfferMoneyFields,
  nowMs?: number,
): string | null {
  if (isBrlCurrency(offer.currency)) return null;
  if (!hasValidBrlConversion(offer)) return "Conversão indisponível";
  const status = (offer.exchange_rate_status || "").toLowerCase();
  if (status === "stale") {
    const relative =
      nowMs != null
        ? formatRelativePast(offer.exchange_rate_updated_at, nowMs)
        : null;
    return relative
      ? `Conversão aproximada · cotação ${relative}`
      : "Conversão aproximada";
  }
  // fresh / unknown: keep the card clean — original currency line is enough
  return null;
}
