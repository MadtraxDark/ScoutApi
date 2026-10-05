# Dataset offline de avaliação do Product Match

`embedding_acceptance.jsonl` é um conjunto legado de regressões e exemplos
construídos. Não representa a distribuição de produção e não deve ser usado
como evidência de qualidade nem para selecionar modelo/threshold.

`embedding_live_corpus.jsonl` contém pares de títulos de MatchRuns existentes e
listagens públicas revisadas. IDs, URLs, preços e dados de proprietário foram
omitidos. Cada linha registra `provenance` e `label_basis`; os grupos não cruzam
splits. O corpus ainda é pequeno e cobre PT/EN, não ES, portanto suas métricas
são diagnósticas e não bastam para calibrar ou aprovar `active`. O caso XFX RX
7600 com discrepância de GTIN está em `development` com rótulo `uncertain`, fora
das métricas binárias.

## Formato JSONL v1

Um objeto JSON UTF-8 por linha. Campos de topo:

- `schema_version`: `1`;
- `sample_id`: identificador anônimo e estável, sem URL ou ID real;
- `group_id`: identificador anônimo de família/produto, igual para pares
  relacionados e exclusivo de um split;
- `provenance` (corpus real): `live_match_run` ou `live_public_listings`;
- `label_basis` (corpus real): justificativa independente do resultado do
  matcher para o rótulo escolhido;
- `split`: `train`, `validation` ou `holdout`, agrupado por família/produto
  para evitar vazamento entre variantes;
- `label`: `same_product`, `different_product` ou `uncertain`;
- `category`, `reference_language`, `candidate_language`;
- `reference` e `candidate`: campos de `ProductIdentity` usados pelo matcher.

Cada identidade exige `title` e `title_normalized`. Campos opcionais aceitos:
`gtin`, `brand`, `model`, `variant_attrs` (objeto texto→texto), `store`,
`product_id`, `price` (decimal como string recomendado), `currency`, `mpn`,
`mpn_display`, `mpn_aliases` e `model_numbers` (listas de texto),
`monitor_model_code` e `category`. O loader rejeita campos desconhecidos,
rótulos inválidos, IDs duplicados e dataset vazio. Não use pickle nem inclua
credenciais, cookies ou segredos.

Execute o baseline sem rede com:

```powershell
uv run python scripts/bench_matching_baseline.py --dataset <caminho-jsonl> --split holdout
```

Os splits aceitos são `development`, `train`, `calibration`, `validation` e
`holdout`. Use o corpus real separadamente por split; não ajuste representação
ou limiar com `holdout`.

O JSON de saída inclui SHA-256 do dataset, matriz de confusão de três classes,
métricas binárias para `same_product` (rótulos gold `uncertain` excluídos),
contagem de `review`/abstenção e cortes por categoria/idioma. O loader rejeita
qualquer `group_id` que apareça em mais de um split. `review` conta
como não-auto-match no recall e na taxa de falso positivo, e aparece como
abstenção na matriz de confusão. Holdout é somente para avaliação; não calibre
thresholds nele.
