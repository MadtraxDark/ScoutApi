# Product Identity & Attribute Normalization

Camada canônica de identidade de produto do ScoutApiV2 (ADR 0026 + ADR 0027).

## Princípios

| Conceito | Significado |
|---|---|
| `brand` | Fabricante / board partner |
| `model` | Identidade base pesquisável (chip, linha, SKU de CPU, …) |
| `variant` | Refinamento comercial opcional (cooler, cor+storage, Digital/Disc) |
| `attributes` | Características técnicas **presentes nas fontes** (nunca inventadas) |
| `product_line` | Linha interna opcional (TUF, ROG) — pode entrar na variant pública |

**Prioridade de fonte:** specifications → structured → selected model/variant →
title fallback → `null`.

**missing ≠ conflict:** atributo ausente de um lado não rejeita match.
Wi-Fi / wireless como variant comercial **não** vira gate de `color`.
Sufixos de board (`B650M-E`) e MPNs hifenizados são preservados na
normalização usada por Search/Match.

**Não over-inferir:** `Ryzen 7 7800X3D` não ganha `cores=8` sem fonte.

### Identidade de processadores

O Product Match extrai a assinatura completa do SKU em contexto de CPU para AMD
Ryzen (incluindo `R7` abreviado), Intel Core i-series e Intel Core Ultra. A
assinatura inclui todos os dígitos e sufixos (`5800X3D`, `14900K`, `14900KF`,
`5600G`, `285K`); dois SKUs explicitamente diferentes são conflito crítico e
não dependem da similaridade global do título. Assinaturas iguais sustentam
match mesmo quando o título tem idioma, ordem e detalhes complementares
diferentes.

Socket (`AM4`, `AM5`, `LGA1700`, etc.) só gera conflito quando ambos os lados
informam valores diferentes. Socket ausente permanece desconhecido. OPNs AMD
de CPU (`100-100000651POF` boxed e `100-000000651` tray) são preservados como
MPNs: igualdade é identificador forte, enquanto OPNs diferentes não anulam uma
assinatura de processador igual, pois podem indicar só a embalagem. Clock,
cache, cores, threads, gráficos integrados e TDP permanecem evidências
complementares; sem parsers/valores confiáveis em ambos os lados não são
inventados nem usados como conflito.

Em Search, processadores geram cedo queries legíveis com marca/família/tier/SKU
espaçados, preservando o sufixo discriminante, além das queries genéricas de
MPN e título. Isso corrige modelos estruturados concatenados como
`ryzen75800x3d` sem baixar thresholds gerais.

### Identidade de monitores

O código de modelo de monitor é um sinal exato quando existe; a ausência não é
conflito. Em Search, monitores sem código também geram cedo uma consulta com a
marca e a família legível extraída do título, seguida por uma variante com
tamanho e taxa de atualização quando presentes. Isso preserva frases como
`Samsung Odyssey G30` quando o campo `model` normalizado está concatenado
(`odysseyg30`), sem alterar os gates de decisão do matcher. Para categorias de
monitor, tamanho da tela, resolução com dimensões explícitas e taxa de
atualização também são normalizados entre grafias equivalentes (por exemplo,
`24"`/`24 pol.`, `FHD (1920x1080)`/`1920 x 1080 pixels` e `144Hz`/`144 Hz`).
Dimensões ou frequências diferentes continuam sendo conflitos de variante.

## Arquitetura

```text
resolve_product_identity
  → detect_product_category (CategoryProfile registry)
  → specs / structured fill
  → CategoryProfile.parse_title (fallback brand/model)
  → CategoryProfile.extract_attributes (title → attrs críticos)
  → format_identity_variant
```

Código:

- `utils/category_profiles/` — `CategoryProfile`, registry, normalizers, parsers
- `utils/category_profiles/attribute_extractors.py` — extractors Phase 4–7
- `utils/product_identity.py` — grammars GPU/phone/CPU/RAM/SSD + chaves canônicas
- `utils/product_attributes.py` — prioridade de fonte + bundle

## Adicionar uma categoria

1. Registrar `CategoryProfile` em `category_profiles/definitions.py`
2. Needles de detecção + prioridade
3. `supported_attributes`, `critical_conflict_keys`, `search_filters`
4. `parse_title` (ou deixar `None` se só metadata)
5. `extract_attributes` em `attribute_extractors.py` (auto-wire por `id`) **ou**
   declarar `specs_only_critical` quando o atributo crítico só vem de specs
   (ex.: `mpn` em printer/scanner)
6. Testes em `tests/unit/test_category_profiles.py`
7. Atualizar esta doc se o contrato público mudar

Não editar o core do resolver com `if category == …` novos.

## Normalização comum

`128GB`→`128 GB`, `6000mhz`→`6000 MHz`, `DDR 5`→`DDR5`, `2 X 16 GB`→`2x16 GB`.
Não inventar kit a partir só do total (`32GB` ≠ `2x16`).

Aliases de marca só com evidência (`ASUSTeK`→`ASUS`).

## Search

`GET /products/search` — filtros opcionais e combináveis:

- `brand`, `model`, `variant`, `category`
- attrs: `vram`, `memory_type`, `capacity`, `frequency`, `chipset`, `socket`,
  `storage`, `refresh_rate`, `wattage`, `panel`

`variant` omitido = todas as implementações do modelo base.

## Match

Evidência forte: GTIN/MPN/model_number. Title similarity só complementar.
Valores de atributos explícitos usam aliases semânticos por chave para suportar
idiomas e ordens diferentes sem traduzir o título inteiro. Ausência permanece
desconhecida. Matiz de cor, qualificador, capacidade, modelo e condição continuam
separados; qualificadores ausentes só são compatíveis quando o matiz explícito
coincide. `network_lock` compara `unlocked`/`desbloqueado` e `locked`/`bloqueado`
quando ambos os lados declaram o atributo. Condição `renewed`, `refurbished`,
`used` ou `open box` não é descartada na normalização e segue o gate de oferta
do ADR 0024. Cor explícita fora do vocabulário fica em `review` quando os valores
divergem; não rejeita por diferença textual nem recebe `auto_match` só por
marca/modelo. Ver [ADR 0040](../adr/0040-matching-multilingual-attributes.md).

Conflitos críticos por profile (ex.: `rtx5070`≠`rtx5070ti`, Digital≠Disc).
Para monitores, o parser de título identifica códigos de fabricante alfanuméricos
com contexto da categoria (incluindo sufixos hifenizados), normaliza apenas
capitalização e espaços, e compara o identificador completo. Códigos iguais
confirmam o match; códigos distintos identificados nos dois lados rejeitam.
Se qualquer lado não tiver código confiável, o matcher segue a cascata normal;
a ausência nunca é tratada como conflito. Quando o código falta e ao menos três
atributos explícitos entre tamanho da tela, resolução, taxa de atualização e
painel coincidem, esses dados sustentam `review`; não promovem sozinhos um
match automático. As decisões são registradas nos logs estruturados do
`MatchingEngine`.

## Persistência

- Tipado: `brand`, `model`, `variant_key`
- JSON `attributes`: `category` + attrs esparsos validados pelo profile
- Identifiers: tabela `product_identifiers` (GTIN)

Promover atributo quente a coluna/índice só com evidência de filtro frequente.
**Hoje:** filtro `category` + attrs roda em memória sobre JSON após prefilter de
`brand` — volume atual **não** justifica coluna `category` indexada nem GIN em
attrs (reavaliar quando search multi-attr for caminho quente em produção).

## Profiles registrados

Ver `category_profiles/definitions.py` (GPU, RAM, MB, CPU, phone, tablet, SSD,
notebook, PSU, cooler, fan, monitor, TV, console, peripherals, network,
printer/scanner/webcam, audio, camera/lens, NAS/UPS/chargers/docks/cables,
furniture, accessory).

Maturidade:

- Phase 1–3: grammars de título fortes (GPU/RAM/MB/CPU/phone/SSD/console/…).
- Phase 4–7: `extract_attributes` cobre `critical_conflict_keys` extraíveis do
  título (pack_count, port_count, vesa, bay_count, connectors, …).
- Printer/scanner: `mpn` é **specs-only** (`specs_only_critical`) — título não
  inventa identificador.
