import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { MatchRunLiveView } from "./api/contracts";
import { reconcileLiveSnapshot } from "./match-run-live.ts";

function snapshot(): MatchRunLiveView {
  return {
    run: { id: "run", product_id: "product", status: "running", started_at: "2026-10-05T00:00:00Z", finished_at: null, last_activity_at: "2026-10-05T00:00:00Z", total_duration_ms: null, stores_total: 2, stores_completed: 0, matches_found: 0, no_matches: 0, errors: 0, failure_code: null, failure_message: null, already_active: false },
    is_effectively_active: true, auto_matches_found: 0,
    stores: ["amazon_br", "kabum"].map(store => ({ id: store, store, store_display_name: store, status: "pending", started_at: null, finished_at: null, matched_decision: null, matched_listing_id: null, matched_store: null, matched_country: null, matched_product_id: null, matched_canonical_url: null, matched_url: null, matched_title: null, matched_price: null, matched_currency: null, matched_confidence: null, converted_price_brl: null, exchange_rate_status: null, exchange_rate_updated_at: null, error_code: null })),
  };
}

describe("reconcileLiveSnapshot", () => {
  it("snapshot idêntico preserva snapshot, lista e objetos", () => {
    const previous = snapshot();
    const reconciled = reconcileLiveSnapshot(previous, structuredClone(previous));
    assert.equal(reconciled, previous);
    assert.equal(reconciled.stores, previous.stores);
    assert.equal(reconciled.stores[0], previous.stores[0]);
  });

  it("alteração de uma loja preserva objeto da outra", () => {
    const previous = snapshot();
    const next = structuredClone(previous);
    next.stores[0].status = "running";
    const reconciled = reconcileLiveSnapshot(previous, next);
    assert.notEqual(reconciled, previous);
    assert.equal(reconciled.stores[0], next.stores[0]);
    assert.equal(reconciled.stores[1], previous.stores[1]);
  });

  it("nova Run substitui resultados anteriores sem retenção cruzada", () => {
    const previous = snapshot();
    const next = snapshot();
    next.run.id = "nova-run";
    assert.equal(reconcileLiveSnapshot(previous, next), next);
    assert.equal(reconcileLiveSnapshot(null, next), next);
  });
});
