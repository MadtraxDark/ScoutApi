# Visão VIP

## Markets / country

- `store=visaovip`, `country=PY`, `currency=USD`
- The registry declares `PY + USD` as this integration's supported market pair;
  `GET /stores` exposes the pair for the admin UI. It is not inferred from
  Paraguay's official currency. Other integrations default to their registered
  `country` + `currency` pair unless they declare additional supported pairs.
- Domains: `visaovip.com`, `www.visaovip.com`
- Ciudad del Este storefront; prices advertised as `U$` (USD)
- UI locales: `pt-BR` (default) and `es`
- Storefront also shows companion `G$` (PYG, IVA) and `R$` (BRL) next to
  the primary U$ — these are **display conversions of the store**, not
  ScoutApiV2 FX and not Pix/card payment methods

## Identifiers

- `product_id` = `productCode` from the RSC flight payload (UI label **Código**)
- URL shape: `/prod/{category}/{slug}/{productCode}/`
- `sku` = specification **REFERÊNCIA** (manufacturer part / MPN) when present;
  otherwise falls back to `productCode`
- GTIN/EAN only when present in specs or product fields (often absent)

## Offer source

- Next.js App Router RSC flight: `self.__next_f.push([1, "..."])` embeds the
  product object (`productCode`, prices, balance, gallery, specs)
- Open Graph support: `product:price:amount` / `product:price:currency=USD`,
  `og:availability`
- No `__NEXT_DATA__`, no JSON-LD Product, no public product API used
- Companion G$/R$ when present on the PDP HTML → `metadata.display_prices`
  (`PYG` / `BRL` as decimal strings). Never overwrite `price` / `currency`

## Details source

- Same flight product object
- Specs from `productSpecifications[]`
  (`specificationName` / `specificationValue`)
- Brand from `manufactureName` (+ specs `MARCA`)
- **Base model** from the GPU/phone/CPU parser on the title when specs `MODELO`
  is a cooler line or opaque code (ADR 0026). Example: specs `Shadow 3X OC` +
  title `GeForce RTX5070` → `model=GeForce RTX 5070`, `variant=Shadow 3X OC`.
  Structured chips (`GeForce RTX 5070 Ti`) still win over a weaker title.
- Description from `productComents` / `productDescription`
- Attribute gaps filled via global `resolve_product_identity` (title fallback)

## Images source

- When `include_images=true`: `productFrontImage` first, then
  `productGalleryImages` (CDN `cdn.visaovip.com/img/prod/...`)
- Absolute URLs, de-duplicated; excludes brand logos (`/img/marca/`)

## Pricing semantics

- Primary offer is always the storefront **U$** amount:
  - No promotion (`productPromotionPrice` is `0` / falsy):
    - `price` = `productPrice`
    - `original_price` = null
  - Promotion (`isProductPromotion` and `productPromotionPrice` > 0):
    - `price` = `productPromotionPrice` (highlighted card price)
    - `original_price` = `productPrice` (strikethrough) when greater than promo
- `currency`: prefer Open Graph `product:price:currency` when present;
  otherwise `USD` (store default)
- `pix_price` / installments: **not exposed** by Visão VIP → always null
  (do **not** invent Pix from card/USD/BRL companions)
- Companion `G$` / `R$` on the PDP → `metadata.display_prices` only
  (source `pdp-html-companion`). These are **not** Pix and must not be mapped
  to Brazilian “PREÇO NO PIX / PREÇO NO CARTÃO” UI slots as if they were
  payment-method prices
- Discount percentage derived only when both current and original exist
- **No FX conversion** inside the spider (USD→BRL stays in the exchange
  subsystem via `converted_price_brl`)

## Availability semantics

- Prefer `isProductWithBalance` (`true` → available, `false` → out_of_stock)
- Fallback: `og:availability` (`instock` / `outofstock`)
- Missing signal → `unavailable` (never invent OOS from HTTP 200)
- Soft-404 shell (title `Produto - Visãovip` without product payload) →
  `ParseError`, not `available=false`
- `shipping_to_brazil: false` in offer metadata — PY stock ≠ BR shipping

## Seller / marketplace

- Direct storefront (not marketplace)
- `seller` from `og:site_name` (typically `Visaovip`), else `Visãovip`
- Display label for UI: store config `display_name="Visão VIP"` (not the spider)

## Fetch strategy

- Default Camoufox + Proxy Cost Mode (`FALLBACK`) for general store fetch
- **PDP:** initial HTML already carries the RSC product payload; plain HTTP
  with a normal UA returns the flight data (HTTP-first is sufficient for offer)
- **SERP Strategy B (current default):** `prefer_browser=True`; Camoufox
  settles the Next.js RSC shell and yields `/prod/` card links.
  - Plain HTTP returns a ~5.8 KB Cloudflare shell (no card links) — do not use.
  - Hydration settle_ms (default) is sufficient for most queries (B650M, GPU,
    CPU, SSD). **Samsung Galaxy S25 Ultra category (smartphones) exhibits
    `incomplete_hydrate`** — shell (~114 KB) loads but RSC never populates
    `/prod/` cards within settle_ms (see Known Limitations).
- **SERP Strategy A (`VISAOVIP_SEARCH_ACTION_ENABLED`, default true):**
  HTTP POST to the `searchProducts` Next.js Server Action endpoint.
  Returns JSON directly (no RSC hydration wait) → preferred path when the
  action ID is available; especially useful when Strategy B hits
  `incomplete_hydrate` (e.g. some smartphone SERPs).
  - **Constraint:** the `Next-Action` header ID is **deploy-coupled** — it
    changes on every Next.js build/deploy of Visão VIP. Do not hardcode
    permanently.
  - **Discovery (production):** `StoreSearchService` resolves ID via
    optional `VISAOVIP_SEARCH_ACTION_ID` bootstrap → process cache →
    Camoufox-hydrated SERP HTML chunk scan
    (`discover_action_id_from_serp_html`). On `404`/`UNAVAILABLE` the cache
    is invalidated and the next call rediscovers. Discovery SERP navigation
    is reused as Strategy B when A fails.
  - Implementation: `search_adapters/paraguay/visaovip_action_strategy.py`
    + `VisaoVipSearchAdapter.try_strategy_a()`.
  - **POST path:** bare `httpx` and Playwright `APIRequestContext` are
    Cloudflare-403'd (TLS fingerprint). Production uses
    `CamoufoxHtmlFetcher.browser_post` → in-page `fetch()` on a warm Camoufox
    page (cookies + browser TLS) after discovery SERP hydration. Without a
    hydrated session, A returns `BLOCKED` and falls back to Strategy B.
  - Kill switch: `VISAOVIP_SEARCH_ACTION_ENABLED=false`.

## Search vs PDP (do not conflate)

| Layer | Responsibility |
|---|---|
| Search | `matching.search_adapters` (`build_search_request` + `parse_candidates`) |
| PDP | `extract_offer` / `extract_details` / `extract_images` |
| Identity / Match | shared `ProductIdentity` + `MatchingEngine` (not store-specific) |

Fixing PDP pricing must not change Search query generation or matcher thresholds.

## Live search (matching)

- Search: `matching.search_adapters` (PDP spider sem Search)
- SERP: `https://www.visaovip.com/busca/termo/{slug}/`
  - Storefront search form converts whitespace to hyphens in the path
    (`ASUS TUF Gaming B650M-E WIFI` → `ASUS-TUF-Gaming-B650M-E-WIFI`)
  - Naive `/busca/?q=` is a soft 404 — do not use
- **Search locale:** `search_locale=en-US` (ADR 0050). UI default is pt-BR, but
  catalog/search terms for electronics are English-first; country=PY must not
  force Spanish color tokens.
- Parser: `a[href*="/prod/"]` whose path matches `/prod/.../{productCode}/`
  (stable productCode = same ID as PDP). CDN gallery URLs under
  `cdn.visaovip.com/img/prod/...` are ignored
- SERP HTML is RSC/postponed: plain HTTP shell often lacks cards; Camoufox
  settle (default store fetch) hydrates product links. Prefer HTTP only if a
  future JSON/RSC payload proves complete without browser
- Progressive ProductIdentity queries (MPN / brand+family+board / board) apply
  via shared `build_search_queries` — no store-specific SKU rules

## Known blocking

- Some non-PDP routes (e.g. naive `/busca`) may 403/404 without the term slug
- Standard WAF/challenge classification via fetcher when it occurs

## Important invariants

- `country=PY` + primary `currency=USD` (do not convert FX in the spider)
- Do not treat soft-404 / incomplete flight as out of stock
- Do not invent Pix or installment values
- Companion G$/R$ stay in `metadata.display_prices` only

## Known limitations

- Wrong/missing category slug in the URL can soft-404 even with a valid code
- GTIN often absent
- Broad term queries (`B650M-E WIFI`) can return Wi-Fi adapters / sibling
  boards — progressive identity queries + matcher precision handle this
- Frontend BR price cards (Pix / cartão) may show empty Pix when `pix_price`
  is correctly null; primary USD may appear under a “cartão” label depending
  on UI mapping — that is presentation, not a store Pix field
- **Samsung Galaxy S25 Ultra SERP `incomplete_hydrate` (probe 2026-09-23):**
  the `/busca/termo/samsung-galaxy-s25-ultra/` SERP loads the Next.js shell
  (~114 KB DOM) but the RSC client-side hydration never populates `/prod/` cards
  within the configured settle_ms. Root cause not fully confirmed; leading
  hypotheses: (1) smartphone category uses a slower RSC variant; (2) the S25
  Ultra is not in Visao VIP’s catalogue (genuine empty without “nenhum resultado”
  marker); (3) Cloudflare asymmetric throttling for high-commerciality queries.
  **Strategy B (browser SERP) returns 0 candidates for this query.**
  **Strategy A (Server Action)** is the preferred path: ID is auto-discovered
  from Camoufox-hydrated SERP chunks (process cache; kill switch
  `VISAOVIP_SEARCH_ACTION_ENABLED=false`). Circuit protection prevents retries
  beyond budget when A and B both fail.

## Tests / fixtures

- `tests/fixtures/visaovip/` — PDP + SERP (`search_termo_board.html`,
  `product_motherboard.html`, `product_display_prices.html`)
- `tests/unit/test_visaovip.py` — offer/details/search URL + SERP parser +
  display_prices + Search↔PDP contract
