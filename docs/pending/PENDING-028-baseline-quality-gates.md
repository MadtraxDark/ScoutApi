# PENDING-028 — Falhas preexistentes nos checks globais

- Status: OPEN
- Tipo: TESTING
- Prioridade: P2
- Área: matching/quality
- Origem: 2026-10-05 — validação da correção de rate limiting (ADR 0047)
- Atualizado: 2026-10-05

## Contexto

A suíte rápida global não está completamente verde. As mudanças de limites
passaram nos checks direcionados; comparar uma cópia do `src` com os arquivos
alterados restaurados de `HEAD` reproduziu três falhas Amazon e os mesmos
41 erros de mypy em 11 arquivos. Não é regressão introduzida pelo ADR 0047.

## Feito

- Suíte rápida: 1.032 passed, 8 skipped, 3 failed em 18,32 segundos.
- Baseline: `test_amazon_match_display.py` apresenta as mesmas três falhas,
  com 6 passed, em 0,49 segundo.
- Mypy baseline e atual: 41 errors in 11 files, checked 202 source files.
- Ruff em `src tests`: 102 violações fora da alteração de limites;
  `ruff format --check src tests`: 48 arquivos anteriores precisam de formato.
- Ruff e formato dos arquivos da alteração passaram.
- Erros iniciais de `tmp_path` foram eliminados usando `--basetemp` no workspace.

## Falta

- Reconciliar expectativas de identidade/locale em `test_amazon_match_display.py`
  com a geração de consultas do matching, sem afetar spiders PDP.
- Corrigir erros de tipagem e lint preexistentes numa alteração própria.

## Por que não terminou

São comportamentos e arquivos fora da correção autorizada de limites. Não
alterar descoberta de produtos ou lifecycle de browser para tornar esta suíte verde.

## Impacto

Checks globais impedem afirmar que todo o projeto está verde. Os testes da
nova política, contrato HTTP, segurança e frontend passaram separadamente.

## Relacionado

- `tests/unit/test_amazon_match_display.py`
- `src/scout_api/modules/matching/`
- `docs/adr/0047-api-token-bucket-scoped-cooldown.md`

## Pronto quando

Suíte rápida, `ruff check .`, `ruff format --check .` e `mypy src` passam,
sem reduzir cobertura ou suprimir falhas reais.

## Atualização — importação de imagens (2026-10-05)

As três falhas Amazon foram novamente reproduzidas no baseline HEAD.
Mypy após a correção: 37 erros em 9 arquivos (baseline 41/11); imagens sem
novos erros. Ruff e formato dos arquivos da importação passaram. TypeScript
do PriceScout e 10 testes direcionados passaram; lint global ainda tem
set-state-in-effect preexistente no admin. Detalhes e números da suíte em
[relatório de importação](../performance/import-images-2026-10-05.md).
