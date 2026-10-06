import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { CatalogOffer, MatchStoreLiveView } from "./api/contracts";
import { isEquivalentOffer, mergeMatchOffers } from "./match-run-offers.ts";

export function partial(overrides: Partial<MatchStoreLiveView> = {}): MatchStoreLiveView {
  return {
    id: "store-amazon", store: "amazon_br", store_display_name: "Amazon Brasil",
    status: "match", started_at: null, finished_at: null,
    matched_decision: "auto_match", matched_listing_id: null,
    matched_store: "amazon", matched_country: "BR", matched_product_id: "ABC",
    matched_canonical_url: "https://amazon.com.br/dp/ABC",
    matched_url: "https://amazon.com.br/dp/ABC?tag=teste", matched_title: "Produto",
    matched_price: "100", matched_currency: "BRL", matched_confidence: "0.99",
    converted_price_brl: null, exchange_rate_status: null,
    exchange_rate_updated_at: null, error_code: null, ...overrides,
  };
}

function offer(overrides: Partial<CatalogOffer> = {}): CatalogOffer {
  return {
    id: "listing-amazon", catalog_product_variant_id: "variant", store_id: "amazon",
    source: "amazon", country: "BR", external_product_id: "ABC",
    canonical_url: "https://amazon.com.br/dp/ABC", seller: null,
    delivery_responsible: null, original_price: 120, pix_price: null, card_price: null,
    discount_percent: null, installments: null, installment_value: null,
    interest_free: null, image_url: null, currency: "BRL", market: "BR",
    history: [], ...overrides,
  };
}

describe("mergeMatchOffers", () => {
  it("não cria oferta para review, no_match, error ou legado sem decisão", () => {
    assert.deepEqual(mergeMatchOffers([], [
      partial({ matched_decision: "review" }), partial({ status: "no_match" }),
      partial({ status: "error" }), partial({ matched_decision: null }),
    ], false), []);
  });

  it("une alias operacional amazon_br à identidade amazon/BR por produto externo", () => {
    const result = partial({ matched_canonical_url: "https://nova-url" });
    assert.equal(isEquivalentOffer(offer(), result), true);
    assert.equal(isEquivalentOffer(offer({ country: "US" }), result), false);
  });

  it("reconcilia canonical URL quando não há ID externo", () => {
    assert.equal(isEquivalentOffer(offer({ external_product_id: null }), partial({ matched_product_id: null })), true);
    assert.equal(isEquivalentOffer(offer({ source: "outra" }), partial({ matched_product_id: null })), false);
  });

  it("reconcilia listing_id mesmo quando identidade foi normalizada", () => {
    assert.equal(isEquivalentOffer(offer(), partial({ matched_listing_id: "listing-amazon", matched_product_id: "outra", matched_canonical_url: null })), true);
  });

  it("preserva key no handoff por listing_id após normalização de identidade", () => {
    const result = partial({ matched_listing_id: "listing-amazon", matched_product_id: "id-anterior" });
    const before = mergeMatchOffers([], [result], false);
    const after = mergeMatchOffers([offer()], [result], true);
    assert.equal(after.length, 1);
    assert.equal(after[0].key, before[0].key);
  });

  it("mostra duas lojas em snapshots separados e mantém keys no handoff final", () => {
    const first = partial();
    const second = partial({ id: "store-kabum", store: "kabum", matched_store: "kabum", matched_product_id: "123", matched_canonical_url: "https://kabum.com.br/123", matched_price: "200" });
    const initial = mergeMatchOffers([], [first], false);
    const progressing = mergeMatchOffers([], [first, second], false);
    assert.equal(initial.length, 1);
    assert.equal(progressing.length, 2);
    assert.equal(initial[0].key, progressing[0].key);
    const final = mergeMatchOffers([offer(), offer({ id: "listing-kabum", source: "kabum", external_product_id: "123", canonical_url: "https://kabum.com.br/123", original_price: 200 })], [first, second], true);
    assert.equal(final.length, 2);
    assert.deepEqual(final.map(row => row.key), progressing.map(row => row.key));
    assert.ok(final.every(row => row.offer && row.partial === null));
  });

  it("preserva preço canônico e marca observação parcial enquanto ativa", () => {
    const existing = offer();
    const result = partial();
    const rows = mergeMatchOffers([existing], [result], false);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].offer, existing);
    assert.equal(rows[0].offer?.original_price, 120);
    assert.equal(rows[0].partial, result);
  });

  it("ordena por BRL comparável e deixa moeda sem cotação ao fim", () => {
    const usd = partial({ id: "usd", matched_store: "amazon", matched_country: "US", matched_product_id: "USD", matched_currency: "USD", matched_price: "10", converted_price_brl: "80" });
    const unknown = partial({ id: "unknown", matched_store: "bestbuy", matched_country: "US", matched_product_id: "unknown", matched_currency: "USD", matched_price: "1" });
    const rows = mergeMatchOffers([], [unknown, partial(), usd], false);
    assert.deepEqual(rows.map(row => row.partial?.id), ["usd", "store-amazon", "unknown"]);
  });
});
