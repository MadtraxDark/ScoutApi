# ADR 0024: Cascata de evidências do Product Match (MPN + bloqueadores)

- Status: aceito
- Data: 2026-09-19
- Supersede: parcial de [ADR 0019](0019-product-matching.md) (refina scoring/retrieval; não altera persistência)

## Contexto

O match cross-store (ADR 0019) priorizava GTIN → brand+model → título, com gates
de variante. Em produtos reais (ex.: SSD Samsung 990 EVO Plus 1TB) o PDP publica
o **nome comercial** em `model` e o **MPN** só no título (`MZ-V9S1T0B/AM`),
enquanto outra loja faz o inverso. O motor rejeitava o mesmo SKU; a busca não
consultava o MPN; títulos com `128 GB` vs `128GB` perdiam overlap; e séries
irmãs (990 vs 870 EVO, RTX 5060 vs 5060 Ti) podiam ser confundidas se o modelo
estruturado fosse fraco.

Pesquisa (OSS `product-matcher`, matching-engine / factorypure, sku-matcher,
catalog-forge / record linkage): identificadores determinísticos primeiro;
atributos normalizados; bloqueadores críticos; título só auxiliar; precisão
antes de recall. Embeddings/ML descartados no momento (custo/opacidade).

## Decisão

1. **Ordem de evidências (scoring):**
   1. conflito de variante / `critical_identity_conflict` → `reject`
   2. acessório → `reject`
   3. kit/bundle (ex.: iPhone + Apple Watch) → `reject`
4. condição **USED/OPEN_BOX** vs referência nova → `reject`;
   Renewed/Refurbished **não** rejeitam identidade (ADR 0050) — geram
   `offer_condition` e persistem rotulados
5. mesmo `store`+`product_id` → `auto_match`
   6. GTIN validado → `auto_match` (brand conflict → `review`)
   7. **MPN** normalizado → `auto_match` (brand conflict → `review`)
   8. brand + model compatível (série comercial / MPN cruzado no título) → `auto_match`
   9. similaridade de título (cap `TITLE_ONLY_CAP`) → no máximo `review`
2. **Bloqueadores críticos** (não compensáveis por título): assinaturas de GPU
   (`ti`/`super`), telefone (`Pro`/`Max`/`16e`≠`16`), série SSD (`990 evo plus` ≠ `870 evo`),
   DDR4≠DDR5, capacidade de armazenamento ≥128GB divergente, **edição de console**
   (`digital` ≠ `disc` / Blu-ray), **quantidade explícita de controles** (1≠2).
   Atributo **ausente** (`unknown`) **não** é conflito — só divergência explícita.
3. **Bundles:** rejeitar merchandise extra (Fortnite, GT7, kit/watch) quando a
   referência é o SKU base. Pack-in / marketing (`Astro's Playroom`, Bluetooth,
   8K, DualSense singular) **não** contam como bundle.
4. **Normalização de título:** compactar `128 GB`→`128gb`; manter MPN como token
   único; mapear cores PT/EN/ES (incl. `Preto`↔`Black`, `Verde-acinzentado`↔`teal`).
5. **Chaves de variante localizadas:** aliases `cor`/`colour`→`color`,
   `armazenamento`/`tamanho`(≥128GB)→`storage` — sem isso o gate de cor era
   descartado (falso positivo Amazon BR iPhone 16 Preto vs Verde-acinzentado).
6. **Candidate retrieval:** queries `GTIN → display MPN (ex. ``MZ-V9S1T0B/AM``)
   → brand + série + storage + **edition** (digital/disc) + **sinônimos de cor**
   (`preto`/`black`) → drop progressivo → MPN compactado → título`. Compactar
   tokens é fraco na SERP Amazon BR. `StoreSearchService` reordena por título/path
   (ignora `keywords=`). No scrape de candidatos, `ProductMatchService` espera e
   retenta uma vez em `RATE_LIMITED` de domínio.
7. **Consoles / título pobre:** `resolve_model` enriquece rótulos genéricos
   (`PlayStation 5`) com Slim/Digital/Disc do título. Soft-model entre famílias
   PS5 **não** exige `SOFT_MODEL_TITLE_MIN` — título é evidência complementar;
   conflito explícito de edição/storage/controles continua rejeitando.
   SERP de console omite `slim` na frase primária (Shopping China esvazia em
   `brand+slim+storage`) e emite MPN CFI hifenizado (`CFI-2115B`).
8. Thresholds inalterados em espírito: `AUTO=0.92`, `REVIEW=0.75`; título sozinho
   nunca `auto_match`.
9. **GPUs (refino 2026-09-19):** modelo canônico = chip (`rtx5070` / `rtx5070ti`);
   cooler/edition (`Shadow 3X` ≠ `Gaming Trio`) e VRAM vão para `variant_attrs` /
   `critical_identity_conflict`. Queries progressivas
   `brand + GPU + edition + VRAM (+ memory_type)` **antes** de MPN/board codes;
   ruído SEO (DLSS, MHz, bus, Ray Tracing) é stopword de título. Soft-model GPU
   também dispensa `SOFT_MODEL_TITLE_MIN` quando chip+edition+VRAM concordam.
   Padrões genéricos de MPN (`###-V###-###`, `G####-…`) coletam aliases
   co-ocorrentes sem hardcode de SKU. KaBuM SERP lê
   `__NEXT_DATA__.catalogServer.data` (anchors `/produto/` ausentes no HTML
   inicial).

## Consequências

- Melhor recall em componentes com MPN no título sem hardcode de produto.
- Menos falsos positivos entre séries/sufixos críticos.
- SERP ainda depende de qualidade da loja; retrieval ruim ≠ baixar threshold.
- GPU: rediscovery por identidade comercial (não URL/ID hardcoded); variantes
  Ti / VRAM / edition continuam rejeitadas.

## Relacionado

- ADR 0019, `modules/matching/engine.py`, `modules/matching/identity.py`
- Testes: `tests/unit/test_matching_regression.py`
- KaBuM search: `spiders/brazil/kabum.py` (`_parse_search_next_data`)
