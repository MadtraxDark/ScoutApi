# Amazon (BR / US)

## Markets / country

- Public `store=amazon` for both markets
- `amazon.com.br` → `country=BR`, `currency=BRL`
- `amazon.com` → `country=US`, `currency=USD`
- Catalog keys `amazon_br` / `amazon_us` with `StoreConfig.key="amazon"`
- Architecture: [ADR 0015](../../adr/0015-amazon-multi-marketplace.md),
  [ADR 0016](../../adr/0016-amazon-http-first-fetch.md)

Offers are **independent**. Same ASIN/model ≠ same price/seller/availability.
No FX conversion in spiders.

## Identifiers

- `product_id` = `sku` = **selected child ASIN**
- Parent ASIN (when different) → `metadata.parent_asin`
- GTIN/ISBN from detail tables / JSON-LD when present

## Live search (matching)

- Amazon BR and US: Search via `matching.search_adapters` (PDP spiders sem Search)
- SERP BR: `https://www.amazon.com.br/s?k={query}`
- SERP US: `https://www.amazon.com/s?k={query}`
- Parser: shared `parse_amazon_search_results` — multi-fonte estável:
  cards `s-search-result` / `data-asin`, depois anchors `/dp/{ASIN}`
  (dedup por ASIN + URL canônica). Não depende de uma única classe CSS.
- Classificação explícita (`classify_amazon_serp_response`):
  - SERP válida com produtos → candidates
  - zero results real (`nenhum resultado` / `no results for`) → lista vazia
    (`genuine_empty` → possível NO_MATCH só depois)
  - Robot Check / CAPTCHA → `UPSTREAM_WAF_BLOCKED`
  - Soft-error Amazon (`Algo deu errado` / Dogs of Amazon / `/error/500-title`)
    → `UPSTREAM_BLOCKED` (proxy FALLBACK), **nunca** NO_MATCH
  - shell incompleto sem ASIN → `SEARCH_INCOMPLETE_RESPONSE`
  - markers presentes sem parse → `SEARCH_PARSE_ERROR`
- `0 candidates` sozinho **não** prova zero resultados: primeiro validar a SERP
- Used by `POST /match` and durable Match Runs (ADR 0019 / ADR 0024 / ADR 0036)
- **Color finish vs hue:** bare SERP/PDP labels like ``Titânio`` / ``Titanium``
  are treated as *missing hue specificity*, not as a conflict against
  ``Titânio Preto`` / Titanium Black (MISSING ≠ CONFLICT). Real conflicts remain
  (ex.: Titânio Preto vs Titânio Branco).
- **Query forms matter more than anti-bot on BR:** compact tokens like
  `mzv9s1t0bam` / `990evoplus` often return sibling SKUs (990 PRO, 870 EVO).
  O título natural normalizado lidera, com categoria explícita e cor localizadas
  na mesma posição. Identificadores exatos, séries legíveis e aliases continuam
  como fallbacks; ver [contrato canônico](../../matching/README.md). Cor preserva
  acabamento (`Titânio Preto` → `black titanium` em en-US), sem traduzir modelo/MPN.
  Cards continuam sendo reordenados por overlap antes de raspar candidatos.
- HTTP-first still applies to SERP URLs (ADR 0016 + ADR 0032): clean SERP HTML
  with `s-search-result` cards is accepted **without** Camoufox; challenge/robot
  pages escalate. Empty SERP after a clean 200 is usually a **query
  quality** issue, not an anti-bot miss.
- PDP sem Buy Box e sem OOS explícito: retry HTTP uma vez; se continuar
  incompleta ou o retry falhar, browser direto. Proxy só após bloqueio
  classificado. OOS explícito não dispara browser nem fabrica preço.

## Offer source

- **Buy Box only** (`#ppd` / apex price selectors / JSON-LD offer fallback)
- Never use related-product carousels as `price`

## Details source

- Title, byline/brand, detail tables, feature bullets
- Attributes via `resolve_product_identity` after structured specs

## Images source

- Official variant gallery: `colorImages` `hiRes` (then landing image)
- Exclude review/customer/recommendation imagery
- Only when `include_images=true`

## Variant model

- Twister: `dimensions` order + `dimensionValuesDisplayData` for selected ASIN
- Selected variant ASIN must align with price/seller/images

## Pricing semantics

### BR

- `price` = Buy Box total in BRL
- `original_price` = list/basis when greater
- `pix_price` / installments only if explicitly shown (“no Pix”, “Nx de”,
  “à vista no Pix / NuPay”); when the Buy Box total is labeled as Pix/NuPay
  without a separate discounted amount, `pix_price` = Buy Box `price`
- Coupons / Prime → `metadata.pricing` flags; never invent Pix

### US

- `price` = Buy Box total in USD
- List/basis → `original_price`
- Never use `/mo`, coupon amount, trade-in, or Subscribe & Save as `price`
- Prime / coupon / Subscribe markers → `metadata.pricing` only
- `pix_price` not applicable

## Availability semantics

- BR: selected offer stock on Amazon BR
- US: selected offer stock on Amazon US (ADR 0013-style)
- `shipping_to_brazil: false` on US metadata — BR delivery does not define US stock
- No Buy Box price + clear OOS → `MissingPriceError` (fail-closed; schema needs price)

## Seller / marketplace

- Buy Box merchant + fulfiller (`metadata.fulfilled_by`)
- Do not assume seller is Amazon

## Fetch strategy

Progressive (ADR 0016):

1. **HTTP** (`UrllibHtmlFetcher`) with locale `Accept-Language`, cookie jar,
   canonical `/dp/{ASIN}` URL — accepted whenever the body looks like a PDP
2. **Browser** (Camoufox direct) when HTTP errors, returns challenge, or is
   not a recognizable PDP
3. **Proxy** only after classified `UPSTREAM_BLOCKED` (Proxy Cost Mode FALLBACK)

Missing Buy Box on an otherwise valid PDP → `MissingPriceError` (no browser
escalation solely for empty widgets; AOD / “outras ofertas” never becomes
`price`).

Shared parser + `AMAZON_BR` / `AMAZON_US` configs; thin regional spiders.
Locale hints: `pt-BR` / `en-US`.
No hardcoded ZIP / silent address spoofing.

### Evidence matrix (public page sources)

| Campo | HTTP | JSON-LD | Embedded state | Browser | API oficial* |
|---|---|---|---|---|---|
| ASIN | sim | às vezes | twister / input | sim | sim |
| title | sim | sim | — | sim | sim |
| price (Buy Box) | sim (quando widget presente) | às vezes | raro no HTML estático | sim (hidratação) | sim |
| original_price | sim (basis/list) | raro | — | sim | parcial |
| seller | sim (merchant feature) | raro | — | sim | offers |
| availability | sim (#availability) | offers | — | sim | sim |
| variant | twister JSON embutido | — | `dimensionValuesDisplayData` | sim | sim |
| images | `colorImages` / landing | image | `colorImages` | só se HTTP incompleto | sim |
| specifications | tabelas detalhe | parcial | — | sim | parcial |

\*Creators API (ex-PA-API): Associates + elegibilidade; **não integrada** sem aprovação.

#### amazon.com (US)

HTTP direto costuma bastar para Offer (ASIN, Buy Box, seller, availability,
variante). Proxy **não** é requisito estrutural.

#### amazon.com.br (BR)

HTTP-first is the production path. Live Buy Box captures (2026-09-12):
`B0GY5SB1P3`, `B0GN4S2ZSK`, `B0GXLWFMQJ` (marketplace sellers). Some PDPs
intermittently omit Buy Box widgets on the first HTML hit — fetcher retries
HTTP once (~1.25s) before fail-closed `MissingPriceError`. Parser also
covers `#buybox` / `#qualifiedBuybox` price widgets observed on live BR PDPs.
AOD / “outras ofertas” never becomes `price`. HTTP 500 → browser fallback;
proxy only after classified `UPSTREAM_BLOCKED`.

## Known blocking behavior

- Robot check / `validateCaptcha` / “not a robot” → **must be resolved** in
  the fetch layer (ADR 0017) via `ChallengeResolver` + offline
  `amazoncaptcha` for classic Amazon image captchas (no API key)
- Soft-error page (`Algo deu errado` / Dogs of Amazon / `/error/500-title`):
  often follows HTTP **503** from datacenter IPs. Detected by
  `is_amazon_soft_error_page` → `UPSTREAM_BLOCKED` so Proxy Cost Mode
  FALLBACK may retry. **Not** CAPTCHA; never parse as SERP/PDP; never
  map to NO_MATCH / `available=false`
- Login / `ap/signin` → **must be resolved** in the fetch layer (ADR 0018):
  seeded Camoufox profile and/or `AMAZON_AUTH_EMAIL` /
  `AMAZON_AUTH_PASSWORD` (operator env only); then resume PDP URL
- Cloudflare JS challenges: soft wait + Turnstile click attempt
- Hard-block (IP ban) is not image-solvable → `UPSTREAM_BLOCKED` (proxy fallback)
- Spiders still refuse to parse challenge/login HTML as a product
- `UPSTREAM_BLOCKED` only after resolution attempts are exhausted
- Empty Buy Box without clear OOS → `MissingPriceError` (fail-closed), not `available=false`

## Important invariants

- Buy Box offer is the commercial truth for the scrape
- BR and US results must remain separate objects
- Conditional pricing stays conditional in metadata
- Canonical fetch URL is `/dp/{ASIN}` (tracking stripped)

## Known limitations

- HTTP-first covers `/crawl/offer` when the public PDP includes Buy Box
- Challenge + auth-wall resolution are **required by policy** (ADR 0017/0018);
  Amazon image captchas use free `amazoncaptcha`; login needs session seed
  and/or operator credentials via env
- BR HTML may omit Buy Box widgets intermittently (soft HTTP retry mitigates)
- Some ASINs return HTTP 500 for non-existent/blocked SKUs
- Markup drift on price/seller widgets — fixtures + selector fallbacks
- Official catalog API not used (eligibility / credentials)
- Amazon US SERP for current-gen phones often ranks **Renewed / Renewed
  Premium** above new unlocked SKUs. Matching rejects used/renewed when the
  reference is new (`condition_reject`) — unmatched is correct when no new
  listing appears in the candidate window.
## Live validation references

- US: `B09V9Z1WLN` — HTTP-first offer (~3s, no browser/proxy)
- BR: `B0GY5SB1P3`, `B0GN4S2ZSK` — fixtures `br_live_buybox_*.html` from live HTML;
  também validados via `OfferScrapeService` em `B0GN515WT6`, `B0H2CXMFLX`
- BR regression without Buy Box: `B0GVTB7BGQ` → `MissingPriceError` (AOD-only)

## Tests / fixtures

- `tests/fixtures/amazon/` (incl. `br_live_buybox_*.html`,
  `br_serp_valid.html`, `br_serp_zero_results.html`,
  `br_serp_robot_check.html`, `br_serp_soft_error.html`,
  `br_serp_incomplete_shell.html`, `br_serp_layout_variant_dp_asin.html`)
- `tests/unit/test_amazon_common.py`, `test_amazon_br.py`, `test_amazon_us.py`
- `tests/unit/test_amazon_http_first.py` (progressive fetch / fallbacks)
- `tests/unit/test_amazon_br_serp.py` (SERP classification / ASIN parse /
  soft-error ≠ NO_RESULTS)
