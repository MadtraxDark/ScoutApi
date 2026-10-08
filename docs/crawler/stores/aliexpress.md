# AliExpress (pt / BR market)

## Markets / country

- `store=aliexpress`, `country=BR`, `currency=BRL` (catalog defaults)
- Domains: `*.aliexpress.com` (locale hosts: `pt.`, `www.`, `es.`, …)
- `country` / `currency` on the offer describe the **storefront market**
  (ship-to / display), **not** the seller’s physical origin
- Seller origin (when present) goes in metadata (`seller_origin`,
  `ship_from` / `ship_from_code`) — e.g. seller in Brasil on `pt.aliexpress.com`
- `pt.aliexpress.com` ≠ Portugal; BRL display ≠ seller country

## Identifiers

| Field | Meaning |
|---|---|
| `product_id` | AliExpress **item / product id** from `/item/{id}.html` |
| `sku` | **Selected `skuId`** (buyable variant), not the item id |

URL query notes:

- `pdp_npi` / SERP params may embed a sku id and stale price snapshots —
  **never** trust query prices; they are tracking/SERP hints only
- `prepare_fetch_url` strips tracking and may promote a sku hint to
  `?sku_id=`
- Canonical form:
  `https://{host}/item/{product_id}.html?sku_id={sku}` when sku is known

## Variant / SKU model

- Product = listing (`productId`)
- SKU = one row in `SKU.skuPaths` / price maps
- Selection order:
  1. explicit `sku_id` / `skuId` query (or sku extracted from `pdp_npi`)
  2. MTop `selectedSkuId` / `PRICE.selectedSkuId`
  3. **first sellable** path in stable order — **never** cheapest wins
- Price, availability, and SKU images must stay on the **same** selected sku

## Seller / marketplace

- Seller = `SHOP_CARD_PC.storeName` (real store), **not** `"AliExpress"`
- Metadata may include `store_num`, `seller_id`, `seller_origin`

## Pricing semantics

| Field | Source |
|---|---|
| `price` | Selected SKU `targetSkuPriceInfo` / sku price map (`salePrice*`) |
| `original_price` | `originalPrice` when higher than sale |
| `discount_percentage` | From discount text or computed |
| `pix_price` | Always `null` (no explicit Pix modality on AliExpress PDP) |
| `installment_*` | From `INSTALLMENT.text` when present |

Conditional / campaign tags (`m03_new_user`, coupon panel, coins, Choice,
app-only) stay under `metadata.promotions` — they do **not** replace `price`.

Currency comes from the price payload (`originalPrice.currency` /
`salePriceString`), never from the hostname alone.

## Shipping / taxes

- `shipping_price` only when MTop shipping bizData exposes a reliable fee
  (`shippingFee: "free"` → `0.00`; otherwise parse amount or leave `null`)
- No invented CEP / freight
- Import tax / Remessa Conforme / ICMS are **not** added into `price`;
  if components appear later, keep them in metadata only

## Availability

- From selected SKU `salable` / `skuStock` / `QUANTITY_PC.totalAvailableInventory`
- HTTP 200 / CSR shell / challenge ≠ `available=false`
- Anti-bot → `UPSTREAM_BLOCKED` (or `AUTH_REQUIRED` on passport login)

## Offer / Details / Images sources

| Surface | Source |
|---|---|
| Offer | Intercepted MTop `mtop.aliexpress.pdp.pc.query` (`PRICE`, `SKU`, `SHOP_CARD_PC`, …) |
| Details | Same payload: `PRODUCT_TITLE`, `PRODUCT_PROP_PC`, selected SKU attrs + identity normalization |
| Images | `HEADER_IMAGE_PC` — selected `skuImagesMap` / `currentSkuImages` first, then gallery; skip logos/banners |

`include_images=false` → gallery not extracted.
`include_images=true` → gallery only when egress is **not** paid proxy
(Proxy Cost Mode).

## Live search (matching)

- Search: `matching.search_adapters` (PDP spider sem Search)
- SERP: `https://pt.aliexpress.com/w/wholesale-{query}.html`
- HTTP-first (`curl_cffi`) when the HTML already embeds `itemList` with
  `productId` and `displayTitle`. Browser is the fallback for a TMD/RGV587
  shell, not the first search transport.
- Prefer that SSR list (titles included) → captured search JSON
  (`data-aliexpress-search`) → `/item/{id}.html` links → embedded item ids
- Title-less numeric item URLs are not a brand hint. With a title, the normal
  SERP identity reject runs before any PDP scrape.
- Candidate retrieval is separate from Product Match scoring
- PDP remains browser MTop interception (CSR). One fresh profile per PDP, then
  proxy only after a classified block.

## Fetch strategy

Priority observed in baseline (2026-09-20, reference GPU PDP):

| Strategy | Result |
|---|---|
| Plain HTTP / curl_cffi search | SSR `itemList` with titles (accepted; no browser) |
| Plain HTTP / curl_cffi PDP | CSR shell, empty title, no PRICE/SKU (`RGV587` / gated) |
| HTTP session (homepage→PDP) | Same shell |
| Camoufox direct | MTop often `FAIL_SYS_TOKEN_EMPTY` / incomplete |
| Camoufox + residential proxy (`FALLBACK`) | MTop `SUCCESS` + full components (~50KB+) |

Chosen path (aligned with Shopee Mode A):

1. Camoufox loads PDP (**fresh temp profile** — reused contexts get RGV587)
2. Intercept browser’s own MTop `pdp.pc.query` response (JSONP OK; detect
   SUCCESS payload even when `POPUP` precedes `PRODUCT_TITLE` in the blob)
3. Wrap JSON for the spider (`data-aliexpress-pdp`)
4. `ProxyPolicy.FALLBACK`: direct first; proxy only after classified
   `UPSTREAM_BLOCKED` (no MTop SUCCESS within early-stop budget)
5. Skip origin warmup for AliExpress (does not mint PDP MTop; burns budget)
6. Proxied launches keep `geoip=True` with BR egress pin (`__cr.br`)

Do **not** forge MTop `_m_h5_tk` signatures over plain HTTP while AliExpress
gates them with `FAIL_SYS_USER_VALIDATE` / `RGV587` / `FAIL_SYS_TOKEN_EMPTY`
(community + local baseline). Prefer browser interception.

Research references (non-exhaustive):

- [SMCodesP/aliexpress-mcp](https://github.com/SMCodesP/aliexpress-mcp) /
  Glama write-up — MTop + browser intercept; HTTP MTop gated as of 2026-08
- [sudheer-ranga/aliexpress-product-scraper](https://github.com/sudheer-ranga/aliexpress-product-scraper)
  v4 — CSR API interception over SSR `runParams`
- [justinritchie/aliexpress-mcp-server](https://github.com/justinritchie/aliexpress-mcp-server)
  — signed MTop; notes empty `runParams` CSR shells
- Reddit r/webscraping — datacenter IPs / rate → CAPTCHA; residential helps
  but is not sufficient alone
- Camoufox stealth docs — fingerprint isolation; challenges may still need
  session / proxy

## Known limitations

- Description body is a separate media URL (`DESC.pcDescUrl`) — not inlined
- Multi-currency secondary display (when present) is not converted
- Cold direct egress frequently lacks MTop SUCCESS; proxy fallback is common
- Search anti-bot (TMD punish) can empty SERP candidates without fabricating
  matches
- Concurrent AliExpress scrapes share domain throttle via `ScrapeGuard`

## Tests / fixtures

- Unit: `tests/unit/test_aliexpress.py`
- Fixtures: `tests/fixtures/aliexpress/` (sanitized MTop SUCCESS, multi-SKU,
  OOS, RGV587 block, search)
