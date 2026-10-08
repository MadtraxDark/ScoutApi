# Magazine Luiza

## Markets / country

- `store=magazineluiza`, `country=BR`, `currency=BRL`
- Domain: `magazineluiza.com.br`

## Identifiers

- Product identity from `__NEXT_DATA__` item / fallbacks

## Live search (matching)

- Search: `matching.search_adapters` (PDP spider sem Search)
- SERP: `https://www.magazineluiza.com.br/busca/{query}/`
- Parser: product card `/p/{id}/` links
- Used by `POST /match` (ADR 0019)

## Offer source

- `__NEXT_DATA__` item + `offers[]`
- If URL has `seller_id`, bind to that seller’s offer; otherwise first offer

## Details source

- Item catalog fields + specs; color often as Magalu raw label
- Brand/model/variant via `resolve_product_identity` (ADR 0026); console
  structured models are kept when no category parser applies

## Images source

- Primary: `__NEXT_DATA__` → `props.pageProps.data.item.media.images`
  (template CDN URLs with `{w}x{h}`)
- Materialize templates to a concrete public size (`1200x1200` verified on
  `a-static.mlcdn.com.br`) — no guessed `small→large` rewrite
- Prefer `a-static.mlcdn.com.br` over `m.magazineluiza.com.br/a-static/`
- Main image (`item.image`) first; then gallery order; dedupe by content-hash
  filename (not by resolution query)
- Keep only assets for the selected product id (exclude other color/storage
  thumbs from `attributes`)
- JSON-LD `image` is **main only** — never treat it as the full gallery
- When `include_images=true`: return official gallery URLs only (no binary
  download in preview). CDN is public; proxy is for PDP fetch only.
- `include_images=false` → `extract_images` not called

## Pricing semantics

- Pix from `bestPrice` when `paymentMethodId == pix`, else visible “no pix”, else JSON-LD
- Do not invent Pix

## Availability semantics

- Fail-closed: missing clear stock signal → `ParseError` (not soft unavailable)

## Seller / marketplace

- Prefer seller id / delivery identifiers over display-only names when selecting offers

## Fetch strategy

- HTTP-first (`curl_cffi`, one attempt) when the body is already a SERP
  (`/busca/` product cards) or a ready PDP (`__NEXT_DATA__` item + offers).
  A 403/reset does not retry on that same call. Akamai sec-cpt is not a
  document. When the body carries a crypto/adaptive payload, one same-session
  impersonation solves the proof-of-work if `chlg_duration` fits a 12 s cap,
  then refetches. A body that is still an interstitial continues to Camoufox.
  Behavioral sec-cpt has no HTTP sensor forge.
- Camoufox + Proxy Cost Mode after that miss. A sec-cpt stub skips
  `networkidle` and starts resolution on the first observation. The browser
  resolver, inside one ~8–22 s budget: pointer telemetry, one press-and-hold
  on a visible control in any frame, crypto/adaptive proof posted from that
  same page when the cookie and the duration fit, then a poll until the
  interstitial is gone. One resume navigation happens only after `sec_cpt`
  contains `~3~`. Structural markers (`id="sec-if-cpt-container"`,
  `class="behavioral-content"`) still count after the document grows past
  40 KB. The settle loop resolves once. `UPSTREAM_BLOCKED` is emitted only
  when that budget ends on a challenge, which is what starts proxy fallback.
  The proxy egress runs the same resolver. A `/busca/` document skips
  `networkidle` once about eight product links are present, or when the link
  count stays unchanged for ~2 s (poll capped at ~8 s). Stopping on the first
  card drops the rest of the grid. An empty SERP still waits for `networkidle`.
  Homepage warmup caps `DOMContentLoaded` at 20 s.
- After a classified direct block whose proxy fallback succeeds, the same host
  skips another doomed direct navigation for a few minutes (`proxy_sticky`).
  The first request of a process is still direct. Proxy is not started when
  the Match store deadline cannot fit another browser attempt.
- Budget validado em 2026-10-05: 15 s frio e 1 s cache, escolhido pelo usuário.
  Warmup mantém a pausa de cookies/challenge e dispensa analytics networkidle
  na homepage. PDP dispensa essa espera apenas com NEXT_DATA pronto (item,
  id, título e ofertas) e sem interstitial. Documento incompleto/challenge
  mantém espera e resolução. C1 permanece 1.
  [Amostras e limitações](../../performance/pending-recovery-2026-10-05.md).

## Known blocking

- Akamai Bot Manager **sec-cpt** interstitial
  (`sec-if-cpt-container`, ~2–3 KB stub) — challenge, never a product
  (ADR 0017)
- Resolution order: HTTP impersonation (crypto/adaptive proof on the same
  session, only when the server wait fits the budget) → persistent Camoufox
  profile and origin warmup → in-page proof and/or press-and-hold with
  pointer telemetry → one resume after `sec_cpt` `~3~` → `UPSTREAM_BLOCKED`
  → sticky proxy fallback, which repeats the browser resolver. Success is a
  SERP/PDP document, not HTTP 200 with the interstitial still present.
- Fetcher challenge / hard-block classification must precede spider parse
  (never map interstitial HTML to missing price)

## Important invariants

- Selected seller must match URL `seller_id` when present
- First true Offer/Details split in the project (ADR 0011)

## Known limitations

- NEXT_DATA schema drift breaks selection
- **Produto / URL inexistente ou aposentado** (não é falha da integração Magalu):
  IDs mortos ou URLs curtas inválidas (ex. histórico `/p/240590700/`) podem
  soft-redirect para home (sem `data.item`) ou render soft-404 `h1=Oops!` com
  título genérico. O spider classifica como `ParseError` (“soft-404”) e **não**
  monta produto com `title=Oops!` / preço fabricado. Em validação live, se
  aparecer Oops/soft-404, **teste outro PDP válido e disponível** no site antes
  de tratar como regressão do crawler (ex. referência viva:
  Galaxy Tab S10 Lite `/p/jjhd6g4f9d/…?seller_id=samsung`).
- Akamai behavioral scoring is adversarial; residential BR proxy may still be
  required after local resolution attempts

## Live validation references

- Soft-404 (URL inválida, **não** integração): `/p/240590700/` → `ParseError` soft-404
- PDP viva (2026-09-21): iPhone 15 128GB Preto `/p/238035600/…` →
  `include_images=true` retorna galeria completa (`media.images`, 10 URLs);
  browser direto, sem proxy; CDN `a-static.mlcdn.com.br` público
- PDP viva (2026-09-14): Galaxy Tab S10 Lite
  `…/p/jjhd6g4f9d/tb/sams/?seller_id=samsung` → oferta OK
  (`price`/`pix_price`/`seller=samsung`/`available`)
- Outras PDPs válidas já observadas: `/p/238803000/`, `/p/241268000/`

## Tests / fixtures

- `tests/fixtures/magazineluiza/` (incl. `product_oops_soft_404.html` para
  classificação de URL morta — não representa falha de loja;
  `product_gallery_media.html` para galeria `media.images`),
  coverage in `tests/unit/test_spider_parsing.py` e
  `tests/unit/test_magalu_images.py`
- Akamai sec-cpt detection/resolution: `tests/unit/test_html_fetcher.py`,
  `tests/unit/test_challenge_resolution.py`,
  `tests/unit/test_akamai_sec_cpt.py`,
  `tests/unit/test_magalu_navigation_readiness.py`
