# NISSEI_COLOR_IDENTITY

Date: 2026-10-06

## Baseline

Title format (Nissei Magento):

`Apple iPhone 17 MG6C4VC/A A3519 256GB / eSIM - Sage`

Observed decision: **Requer revisão** (`review`) / reject paths depending on
how color leaked into `model`.

## Root cause

1. Commercial colors `sage` / `burgundy` / `glacier` were **absent** from
   `COLOR_CANONICAL`. Differing unknown colors triggered
   `variant_semantic_uncertain` → engine forced `review` (not a real
   structured conflict gate).
2. Structured `model` values like `iPhone 17 Sage` normalized to
   `iphone17sage`, breaking `brand_model_exact` against clean `iphone17`.
3. Color at the end of the title was treated as residual model text instead of
   a first-class variant attribute.

## Research

Compared approaches:

| Approach | Verdict |
|---|---|
| A. Loose title regex | Brittle; position-sensitive |
| B. Color gazetteer + longest-match | **Chosen** — deterministic, multilingual aliases, explainable |
| C. Structured attribute extraction | Already present via Magento specs; keep as primary when available |
| D. NER / LLM | Rejected — heavy, non-deterministic for this catalog problem |

Sources consulted conceptually: Putthividhya/Hu & KDD workshop gazetteer AVE,
ECNLP 2024 explicit attribute extraction + gazetteer normalization,
`@acjlabs/apparel-attribute-utils` (conservative synonym → controlled vocab),
HuggingFace colors-normalized (base palette — we keep commercial names, do not
collapse Sage→Green).

## Approach chosen

```
raw title
→ strip MPN / A#### / connectivity / market / color from model tokens
→ COLOR gazetteer (longest whole-word match)
→ COLOR canonicalization (commercial name preserved)
→ COLOR_FAMILY hint (observability only)
→ ProductIdentity.model clean
→ variant_attrs.color + connectivity + market_variant
→ MatchingEngine
```

## Tests

`tests/unit/test_color_identity_extraction.py` — position matrix, Sage/Burgundy,
multi-word, Samsung/Motorola, storage/Pro conflicts, americano/eSIM.

Broader suite: 160 matching unit tests passed.

## Live result

Synthetic (exact Nissei title format, polluted model `iPhone 17 Sage`):

- model → `iphone17`
- color → `sage` (canonical `sage`, family `green`)
- mpn → `mg6c4vca`, model_numbers → `a3519`
- connectivity → `esim`
- decision → **`auto_match` 0.92**

Live Nissei catalog (2026-10-06): Sage SKU not listed in SERP; live candidates
were Pro Max / Preto / Burgundy siblings. Engine correctly **rejected**
`sage!=preto` as real color conflict (no false review). Frontend maps
`auto_match` → Correspondência automática; `review` → Requer revisão — no FE
change required.

## Before / after

| Case | Before | After |
|---|---|---|
| `iPhone 17 Sage` model | `iphone17sage` → fail/reject | `iphone17` + color=sage → auto_match |
| Sage vs Burgundy | `variant_semantic_uncertain` → review | `variant_mismatch` → reject |
| Sage vs Sage (any title position) | fragile | auto_match |
| `(americano)` / eSIM | could pollute model | soft attrs; model clean |
