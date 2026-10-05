# PENDING-029 — Rebuild Docker falha nas permissões do modelo fpgen

- Status: OPEN
- Tipo: TECH_DEBT
- Prioridade: P1
- Área: docker/camoufox
- Origem: 2026-10-05 — aplicação local da correção de limites (ADR 0047)
- Atualizado: 2026-10-05

## Contexto

`docker compose build api` com o Dockerfile existente resolveu Camoufox 0.5.7
em lugar do 0.5.6 da imagem local validada. O estágio `python -m camoufox fetch`
baixou um browser de 1,3 GB e terminou com `FpgenModelError`: o usuário `app`
não pode ler/escrever `/usr/local/lib/python3.12/site-packages/fpgen/data`.
O Dockerfile e requisitos não foram alterados pela correção de limites.

## Feito

- Identificado o erro no histórico BuildKit, não um bloqueio de rede ou CAPTCHA.
- Consultado o código oficial: a CLI de fetch chama `ensure_fpgen_model`.
- Preservada a imagem anterior com a tag local `scoutapiv2-api-rate-limit-base`.
- Construída atualização somente de código a partir dessa imagem existente:
  cópia de `src/scout_api` em `/app/src/scout_api` e no pacote instalado em
  `/usr/local/lib/python3.12/site-packages/scout_api`, sem reinstalar dependências.
- Selecionada a atualização como `scoutapiv2-api`; recriado somente o serviço
  `api` com `docker compose up -d --no-deps --no-build api`.
- API saudável; Camoufox 0.5.6 e FastAPI 0.142.0 preservados. Headers dos escopos
  confirmados no navegador em requests reais com resposta 200.

## Falta

Corrigir permissões e reprodução do build oficial numa tarefa específica,
preservando versões/browser validados ou comprovando a atualização conforme
as regras de imutabilidade e benchmark do projeto. A atualização local de
código depende da imagem existente e não substitui um rebuild limpo oficial.

## Por que não terminou

A tarefa autoriza mudança no rate limiting, não atualização de fingerprint,
browser ou lifecycle Camoufox. O contorno local aplica a correção sem isso.

## Investigação

- Causa: pacote/modelo instalado sob root; etapa de fetch executada sob `USER app`.
- Testado: build oficial falhou; atualização sobre imagem validada passou em
  cerca de 1,5 segundo e a API reiniciada ficou saudável.
- Tempo observado: instalação de dependências 36,8 segundos; etapa Camoufox
  cerca de 89 segundos, incluindo download de 1,3 GB, antes da falha de permissão.
- Alternativas: conceder ownership somente ao diretório de dados necessário
  no build ou preparar o modelo antes de `USER app`; avaliar com versões fixadas.
- Não adotado: atualizar browser/dependências sem validação ou elevar usuário
  do crawler em produção como solução de permissão.
- Fonte oficial: [CLI Camoufox](https://github.com/daijro/camoufox/blob/main/pythonlib/camoufox/__main__.py).

## Impacto

A correção de limites está ativa localmente, mas um rebuild limpo pode falhar
até reparar esta lacuna. API não exige serviço pago ou alteração de banco.

## Relacionado

- `Dockerfile`
- `pyproject.toml`
- `docs/adr/0039-bounded-browser-scheduler-capacity-c1.md`
- `docs/security/api-auth.md`

## Pronto quando

Build oficial limpo passa sob usuário correto, API inicia saudável e o runtime
Camoufox permanece validado sem aumento de capacidade ou mudança não testada.

## Atualização — investigação da importação (2026-10-05)

Dockerfile concede ownership somente a fpgen/data ao usuário app. Build oficial
api + image-optimizer passou; ensure_fpgen_model foi validado sob app. O bloqueio
de permissão está corrigido. Permanece aberta somente a validação/pin do runtime
Camoufox novo; execução local conserva 0.5.6 da imagem validada. Não implantar
browser novo sem evidência exigida pelo projeto. Ver relatório de desempenho
[importação](../performance/import-images-2026-10-05.md).
