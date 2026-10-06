# Shopping China

## Markets / country

- Registry markets: `PY + PYG` (`.com.py`) and `PY + BRL` (`.com.br`); primary
  currency follows the requested host locale.

- `store=shoppingchina`, `country=PY` (always Paraguay storefront identity)
- Domains: `shoppingchina.com.py`, `shoppingchina.com.br`
- Dual TLD, **one** spider / one country code

## Identifiers

- Product id from URL path (`…-(\d+)/`) and/or structured data
- Optional internal id in metadata when distinct

## Live search (matching)

- Search: `matching.search_adapters` (PDP spider sem Search)
- SERP JSON: `https://www.shoppingchina.com.py/quick_search?search={query}`
  (legacy Magento `/catalogsearch/result` returns 404)
- Fallback HTML parser for `/site/search?query=` pages when JSON is absent
- **Search locale:** `search_locale=en-US` (ADR 0050). Catalog titles index
  English color tokens (`BLACK`); country=PY must not force `es-PY`/`negro`.
  Live evidence (2026-10-05): `… black` recovers the SKU; `… negro` returned a
  wrong sibling.
- Product URLs: `/produto/` and `/producto/`
- On **`.com.py`**, `quick_search` may still emit `/produto/` slugs that soft-404;
  spider rewrites them to `/producto/` in search candidates and
  `prepare_fetch_url` (`.com.br` keeps `/produto/`)
- Used by `POST /match` (ADR 0019)

## Offer source

- Prefer **visible primary price** on the requested page; JSON-LD as support

## Details source

- Specs lines / structured data; GTIN when present (e.g. flix EAN hooks)

## Images source

- Official product images when `include_images=true`

## Pricing semantics

- Primary `price` / `currency` follow the **requested host locale**:
  - `.com.py` → typically PYG
  - `.com.br` → typically BRL as advertised
- USD tax-free (when shown) → `metadata.display_prices` only
- **No FX conversion** in the spider
- Never mix an amount from one currency with another currency code

## Availability semantics

- Availability = stock at the **Ciudad del Este** storefront
- Always record `shipping_to_brazil: false` — BR shipping does **not** define stock
- Price-first may mark available when BR locale hides cart but price is present

## Seller / marketplace

- Fixed seller `"Shopping China"` (direct storefront)

## Fetch strategy

- `prepare_fetch_url` keeps the requested host (no silent rewrite to `.py`)
- On `.com.py` only: normalize legacy `/produto/` → `/producto/` before fetch
- Camoufox + Proxy Cost Mode

## Known blocking

- Standard challenge classification via fetcher

## Important invariants

- `country=PY` even on `.com.br` URLs
- Shipping-to-Brazil never flips availability
- Alternate currencies stay in metadata

## Known limitations

- Locale/host can change which price is primary; callers must not assume PYG
  on `.com.br`

## Tests / fixtures

- `tests/fixtures/shoppingchina/`, `tests/unit/test_shoppingchina.py`
