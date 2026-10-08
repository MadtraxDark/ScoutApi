# Best Buy

## Markets / country

- `store=bestbuy`, `country=US`, `currency=USD`
- Domain: `bestbuy.com`
- Used as **US list-price reference** for BR comparison

## Identifiers

- Product id / SKU from product JSON / page state
- GTIN/UPC when present

## Live search (matching)

- Search: `matching.search_adapters` (PDP spider sem Search)
- SERP: `https://www.bestbuy.com/site/searchpage.jsp?st={query}&intl=nosplash`.
  Sem `intl=nosplash`, o egress internacional pode receber a seleção de país
  com HTTP 200 e zero produtos; essa página é `SEARCH_INCOMPLETE_RESPONSE`,
  não uma busca vazia. Em teste real, o parâmetro retornou seis links de
  produto para `apple iphone 18 pro 512gb burgundy`.
- Parser: modern `/product/{slug}/{bsin}` and legacy `/site/…/{sku}.p?skuId=`
- Links modernos com e sem `/sku/{sku}` são deduplicados pelo BSIN da PDP;
  ambos podem aparecer na mesma SERP e a raspagem duplicada é custosa.
- Used by `POST /match` (ADR 0019)

## Offer source

- Product JSON + semantic markup / JSON-LD
- Modern `/product/{slug}/{bsin}` PDPs: Apollo SSR (`ApolloSSRDataTransport`)
  may embed `price.customerPrice` / `skuId` / `bsin` in non-JSON script pushes —
  spider extracts those fields when bare JSON product state is absent

## Details source

- Same product state; specs and model fields

## Images source

- Gallery when `include_images=true`

## Variant model

- Dimension map → `key: value; ...`
- **US carriers** (AT&T, Verizon, T-Mobile, …) are commercial conditions, not
  product identity — exclude from normalized `variant`; keep in metadata
  (`carrier`, `carrier_locked`)

## Pricing semantics

- `price` / `original_price` / discount from offer state
- Carrier-locked financing is **not** product installment — omit installment
  when `carrier_locked`

## Availability semantics

Canonical: [ADR 0013](../../adr/0013-bestbuy-availability-semantics.md).

- Price-first for active US offers
- Clear sold-out / discontinued → `out_of_stock`
- Missing shipping to Brazil / ZIP / store-pickup-only does **not** mean OOS
- `location_dependent` may be flagged in metadata without flipping availability

## Seller / marketplace

- Merchant from offer state; default display `"Best Buy"` when absent

## Fetch strategy

- `prepare_fetch_url` appends `intl=nosplash` to skip international splash
  (fetch-only; stripped from `canonical_url`)
- Accepts modern `/product/{slug}/{bsin}` and legacy
  `/site/{slug}/{sku}.p?skuId={sku}` (Best Buy 301-redirects legacy → product)
- Bare `/site/{sku}.p` gets `skuId` injected for the fetch
- If a legacy `skuId` lands on a different SKU after redirect → `ParseError`
  (fail-closed redirect leak)
- Camoufox + Proxy Cost Mode

## Known blocking

- Cloudflare/WAF / Akamai via fetcher classification
- Direct Camoufox from non-US egress often hits `NS_ERROR_NET_RESET` on PDP;
  classified as `UPSTREAM_BLOCKED` → Proxy Cost Mode FALLBACK
- Proxied Camoufox pins DataImpulse-style geo to **`__cr.us`** for
  `bestbuy.com` (BR residential IPs also reset against Akamai). Locale
  `en-US` when proxy is active; `geoip=True` on Best Buy proxied launches so
  timezone/WebRTC align with the US exit IP
- Sticky DataImpulse ``sessid.scoutbb`` keeps ``_abck`` / sensor cookies on one
  residential exit (~30 min)
- **Homepage warmup** before PDP (mint Akamai ``bmak`` / ``_abck`` / ``bm_sz``)
  with longer settle + light mouse telemetry; Referer from origin on PDP
- On PDP ``NS_ERROR_NET_RESET``, re-warm and retry once in-session; StoreAware
  also retries the proxied leg once after classified block
- Camoufox ``disableInstantAnimations`` (community: Akamai detection vector —
  daijro/camoufox#450/#555; Docker uses Camoufox Firefox 152+)
- Plain HTTP without Akamai cookies is unreliable; prefer browser + FALLBACK
- Never fabricate `price=null` / `available=false` from a TCP reset — classify
  as `UPSTREAM_BLOCKED` until resolution succeeds

## Important invariants

- US stock ≠ BR shipping eligibility
- No FX conversion in spider
- `canonical_url` must not keep `intl=nosplash`

## Known limitations

- Location/ZIP can change page copy without changing stock semantics
- Deactivated legacy SKUs may 404 (`page not found`) or redirect elsewhere —
  mismatch is rejected; true 404 stays `ParseError`
- SERP titles often come from the `/product/{slug}/` path when card text is empty
- Carrier-locked SKUs (Verizon/AT&T/…) appear in US SERP; matching treats carrier
  as commercial condition, not identity — unlocked / BR-ref matches still prefer
  unlocked when available
- Candidate search can succeed while PDP scrape still needs US residential
  proxy after direct `NET_RESET`
- Cold PDP without homepage warmup historically caused intermittent TCP RST;
  mitigated by origin warmup + sticky sessid (see Known blocking)

## Tests / fixtures

- `tests/fixtures/bestbuy/` (incl. `product_apollo_ssr.html`),
  `tests/unit/test_bestbuy.py`
