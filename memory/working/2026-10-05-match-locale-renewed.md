# Working log — Product Match locale + Renewed

Date: 2026-10-05
HEAD: 93e75fd

## SEARCH_LOCALE

### Before (query_locale via country fallback)
- shoppingchina/visaovip/nissei: country=PY → es-PY
- amazon_us/bestbuy: country=US → en-US
- BR stores: country=BR → pt-BR
- No store had explicit search_locale

### Reproduction (iPhone 17 256GB Preto 5G)
- es-PY first query: `apple iphone 17 256gb negro 5g`
- en-US first query: `apple iphone 17 256gb black 5g`

### Shopping China live quick_search
- `apple iphone 17 256gb black` → CELULAR APPLE IPHONE 17 256GB BLACK (+ E/JP)
- `apple iphone 17 256gb negro` → CELULAR APPLE IPHONE 17E 256GB WHITE JP (wrong)

### Visão VIP
- HTTP SERP 403 without browser; UI locale pt-BR; catalog titles EN
- User evidence: English search terms work

### Nissei
- Live SERP Cloudflare-blocked in this session
- Fixtures/swatches use English color labels (Black)
- Search path `/br/` (PT UI) but catalog attributes EN

## RENEWED_CONDITION

### Before
- condition_conflict: any used/refurbished/renewed vs new → reject
- prefilter `_serp_title_reject_reason` uses same gate
- ADR 0024/0040 document condition reject

### Target
- Renewed/Refurbished allowed with explicit condition label
- Used/Open-box keep reject-vs-new
- Identity blockers (storage/Pro) unchanged

## IPHONE17_LIVE_REGRESSION

Ground truth (evidence only, not hardcoded):
- Shopping China title: CELULAR APPLE IPHONE 17 256GB BLACK
- Best Buy Verizon URL provided by user
- Amazon US Renewed URL provided by user
