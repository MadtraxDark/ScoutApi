# Baseline de aceitação de embeddings no Product Match

## Corpus real: execução determinística

- **Data:** 2026-09-26
- **Dataset:** `tests/fixtures/matching/embedding_live_corpus.jsonl`
- **SHA-256:** `B0AD88CF8096EF377349F16AEED9AFC6DC18FADEDD614CB439B7C3AEF669DBCE`
- **Fontes:** títulos anonimizados de MatchRuns existentes; monitor ASUS/Gigabyte também documentado em PENDING-020; listagens públicas RX 7600.
- **Privacidade:** sem IDs de registro, URLs, preços, credenciais ou dados do proprietário.
- **Representações/provider:** não comparados. Este resultado mede apenas o `MatchingEngine` atual.

| Split | Pares | Rótulos | F1 same product | Falso positivo |
|---|---:|---|---:|---:|
| development | 1 | 1 uncertain (XFX RX 7600 com conflito de GTIN) | n/a | n/a |
| train | 1 | 1 same | 1,00* | 0 |
| calibration | 2 | 1 same, 1 different (iPhone 15) | 1,00 | 0/1 |
| validation (antes do fix) | 4 | 2 same, 2 different (CPU e motherboard) | 0,80 | 1/2 |
| holdout | 2 | 1 same, 1 different (ASUS VG259) | 1,00 | 0/1 |

`*` O split de treino tem só um positivo; suas métricas não têm valor
estatístico. Holdout tem uma única família e não demonstra generalização.
Calibration, validation e holdout tampouco têm tamanho para aprovar thresholds.
O par iPhone 15 Rosa/Midnight ficou em `review` pelo matcher e foi rotulado
`different_product` pela diferença explícita de variante, sem usar a decisão do
matcher como rótulo.

### Falha revelada pela validação

Na família Gigabyte A520M K V2, o matcher deu `auto_match` ao hard negative
A520M DS3H V2. A saída real persistida tinha razão `brand_model_exact` com
assinatura ampla `a520m~a520m`, apesar dos códigos completos de placa diferentes.
O caso está rotulado `different_product` por identidade de modelo e permanece no conjunto de validação. O fix em `_motherboard_board_signature` diferencia o sufixo `K` de `DS3H`; a reexecução deu TP=2, TN=2, FP=0 e FN=0 (precision/recall/F1 = 1,00 neste conjunto de quatro pares). É um diagnóstico pequeno, sem valor estatístico para generalização.

O par de GPU XFX usa o MPN oficial RX-76PSWFTFY, mas a listagem Carrefour tem
GTIN divergente do produto Pichau. Por isso está marcado `uncertain` em
`development`, fora de métricas binárias, até a discrepância ser resolvida.

## Smoke legado

`tests/fixtures/matching/embedding_acceptance.jsonl` é um conjunto pequeno de
regressões e exemplos construídos: 13 pares em 9 grupos, todos no split
`holdout`; seu SHA-256 anterior é
`78ac86d59cbec52a76910432c364d8d337d698fb331debe9aa6da9a31d383c17`. O resultado
anterior de 12/12 decisões binárias corretas não é evidência estatística nem
deve ser usado para treino ou calibração. Ver instruções dos fixtures em
[`tests/fixtures/matching/README.md`](../../tests/fixtures/matching/README.md).

## Limites

O corpus real tem 10 pares em cinco categorias, somente PT/EN e poucos grupos.
Não há exemplos espanhóis reais no catálogo/runtime consultado. Recall@K/MRR não
se aplicam, pois o dataset avalia identidade de pares, não uma lista ranqueada
de candidatos. Precision/recall/F1 acima são diagnóstico do matcher
determinístico; não são métricas de embeddings.

Este baseline é exploratório e não qualifica `active`: não houve calibração,
nem comparação de todas as representações/provider, e o holdout não foi usado
para calibração. O experimento vigente é apenas local e shadow. Se surgir
escopo para ativação ou produção, será necessário ampliar o corpus com rótulos
independentes, revisar grupos/categorias/idiomas e executar benchmark em split
separado, sem reutilizar o holdout para calibração.

## Geração de consultas do Product Match (2026-09-26)

Para a referência Pichau XFX Radeon RX 7600 Speedster SWFT210, MPN
`RX-76PSWFTFY`, a geração anterior compactava o modelo (`radeonrx7600`), não
extraía o MPN e ignorava o locale. As cinco primeiras consultas agora mantêm
GTIN, marca/modelo legível (`xfx rx 7600`), MPN exato, atributos principais e
um único termo de categoria localizado. Exemplos: `placa de video` em pt-BR,
`graphics card` em en-US e `tarjeta grafica` em es-PY. `rx 7600` também é
reconhecido como GPU e variantes incompatíveis continuam rejeitadas pelo gate
determinístico.

Smoke Product Match real pós-correção via `POST /match`, `persist=false`, lojas
Kabum e Terabyte (ML e Shopee excluídas): 19,6 s, sem erros, zero matches e
ambas as lojas sem correspondência. Antes do fix, o mesmo produto também teve
zero correspondências; portanto a melhoria verificada é na qualidade das
consultas e na prevenção do falso positivo, não em recall live. A listagem XFX
correspondente não foi confirmada nessas SERPs.
