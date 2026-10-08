# Regras do projeto ScoutApiV2

- Modular monolith com `src` layout e organização por feature em `src/scout_api/modules/`.
- Direção de dependências: HTTP/router -> service -> repository -> database.
- Regras de negócio não dependem de FastAPI; routers traduzem erros de domínio para HTTP.
- Use `core/` apenas para infraestrutura transversal.
- Testes ficam fora de `src`, em `tests/unit`, `tests/integration` ou `tests/e2e`.
- Decisões relevantes ficam em `docs/adr/`, com numeração sequencial e template.
- Docker é a forma oficial de execução; mantenha `Dockerfile` e `compose.yaml` funcionais.
- Configurações usam variáveis de ambiente centralizadas em `core/config.py` com `pydantic-settings`.
- Nunca versione `.env` ou secrets; mantenha `.env.example`, Compose, README e configuração sincronizados.
- **Segurança da API (crítico):** endpoints privados por padrão (DENY BY DEFAULT);
  allowlist pública explícita; secrets e credenciais privilegiadas nunca
  atravessam a API; dados pessoais com minimização (`PublicUser`); auth
  (Supabase JWT) separada de autorização/ownership; `AUTH_REQUIRED=true` por
  padrão (`false` só em dev/teste — rejeitado no startup em production);
  rate limiting nos endpoints (crawler mais restrito); logs/erros sanitizados.
  Ver `.cursor/rules/security.mdc`, `docs/security/api-auth.md` e ADR 0023.
- **Persistência (PostgreSQL / Supabase):** a API é o único componente que
  acessa o banco. Conexão direta via `DATABASE_URL` + SQLAlchemy/`psycopg`
  (nunca Supabase Data API no backend; nunca acesso do frontend ao Postgres).
  Schema só via Alembic. Redis, se usado, é cache/coordenação — não SoT.
  Ver ADR 0021, `docs/persistence/supabase-postgres.md`,
  `.cursor/rules/persistence-supabase.mdc` e skill `supabase-postgres`.
- O crawler coleta preços apesar de WAF/anti-bot usando fetch/browser/proxy **já
  previstos** (Camoufox, Proxy Cost Mode), **resolução de challenge/CAPTCHA** e
  **auth bypass** (login/session wall). Challenge/CAPTCHA/robot-check **e**
  paredes de login/soft-auth que bloqueiam oferta pública **devem ser
  resolvidos**; nunca trate HTML de bloqueio/login como produto
  (`available=false` / preço fabricado). `UPSTREAM_BLOCKED` só após esgotar
  tentativas de resolução. Ver `.cursor/rules/captcha-challenge-resolution.mdc`,
  `.cursor/rules/auth-wall-resolution.mdc`, ADR 0017 e ADR 0018.
- **Concorrência de browser limitada (crítico):** `CAMOUFOX_BROWSER_CAPACITY=1` é o único valor
  validado em produção. Não aumente a capacidade sem benchmark com harness de produção (`BrowserScheduler`)
  e evidência de `success_rate ≥ C1` **e** `P95 não piora ≥15%` em loja real anti-bot. A infraestrutura
  `BrowserScheduler` + `ProfileLock` + `claim_trial` protege slots isolados; fila saturada emite
  `BROWSER_QUEUE_SATURATED` em vez de hang. ADR 0032 (pool N browsers rejeitado) **permanece Accepted**;
  ADR 0039 documenta a decisão C1 e as condições para reabertura de C2+.
- **Proxy Cost Mode (crítico):** proxy pago deve ser evitado sempre que possível. Todas as lojas tentam acesso direto primeiro; proxy só como fallback após bloqueio classificado (`UPSTREAM_BLOCKED`). Com proxy ativo, tráfego mínimo obrigatório (sem imagens/vídeos/fontes/PDFs; ignorar `include_images=true`; só texto/JSON essencial). Ver `.cursor/rules/proxy-cost-mode.mdc` e ADR 0014.
- **Scraper/Camoufox imutáveis por padrão:** sem pedido explícito, não altere fetch, navegação, waits, fingerprint, proxies, retries, lifecycle do browser nem o fluxo operacional do Camoufox. Só é livre alterar extração, parsing, normalização, validação e shape dos dados coletados. Exceções: Proxy Cost Mode; **resolução obrigatória de challenge/CAPTCHA** (ADR 0017); **auth bypass / login wall** (ADR 0018).
- **Auth bypass (crítico):** login wall / soft-auth / session-gate que impede
  preço público **deve ser contornado ou resolvido** (sessão própria,
  credenciais do operador via env — nunca no Git). Ver
  `.cursor/rules/auth-wall-resolution.mdc` e ADR 0018.
- **Pesquisa antes de bloquear (crítico):** antes de declarar problema sem
  solução ou adotar dependência/serviço pago, investigue causa raiz,
  documentação oficial, soluções nativas, open source/GitHub, Stack Overflow e
  experiências da comunidade; teste alternativas viáveis. Prefira:
  capacidades do projeto → nativo → OSS mantido → gratuito → pago (pago só com
  justificativa e autorização explícita). Não trate “não está no código atual”
  como inexistência de solução. Ver
  `.cursor/rules/research-and-problem-solving.mdc`.
- **Observabilidade de tempo (crítico):** processos significativamente longos
  — incluindo comandos, testes e ferramentas executados por agentes — **não
  podem ser invisíveis**. Meça duração/etapa, não aguarde silenciosamente
  processos aparentemente travados, investigue a causa, prefira feedback
  rápido (teste direcionado antes de suíte cara) e reporte regressões. Retries,
  browser e Product Match devem expor timings por etapa. Pendências
  recorrentes: tipo `PERFORMANCE`. Ver `.cursor/rules/performance.mdc`,
  `docs/performance.md` e ADR 0028.
- **Product Match persistente (crítico):** a busca em outras lojas é um job
  de backend (`ProductMatchRun`), não uma conexão SSE nem lifecycle da página
  React. Uma Run ativa por produto; polling leve; toast efêmero ≠ central de
  notificações persistente; logs observáveis (sem chain-of-thought). Ver
  ADR 0036 e `docs/integration/pricescout.md`.
- **PDP ≠ Store Search (crítico):** scraping de página de produto e candidate
  discovery do Match são capabilities independentes (ADR 0038). Não implemente
  lógica de SERP em spiders PDP; não implemente `extract_offer`/`extract_details`
  em `StoreSearchAdapter`. Ver `docs/matching/README.md`.
- **Imagens de produto (crítico):** candidata do crawl ≠ imagem de catálogo;
  upload no Drive **só após aprovação** humana; original nunca é apagado ao
  gerar AVIF; credenciais Drive nunca vão ao frontend; download de URL
  externa exige validação SSRF. Ver `docs/persistence/product-images.md` e
  ADR 0029.

## Execução de ferramentas

- Execute comandos diretamente pelo terminal do agente (PowerShell no Windows),
  sem depender do Cursor, de seu terminal integrado ou de automações do editor.
- Se o sandbox falhar antes de criar o processo com
  `helper_unknown_error: setup refresh had errors`, teste um comando somente de
  leitura pelo terminal direto com `require_escalated`, sujeito à aprovação da
  ferramenta. Não aplique escalonamento indiscriminado a outros comandos.
  Em 2026-10-07, `Get-Location` falhou no sandbox e funcionou fora dele no
  mesmo workspace; o teste não demonstrou falha no Cursor.

## Documentation

Decisões arquiteturais e comportamentais importantes devem ficar no repositório.

Antes de concluir mudanças significativas, faça um **documentation impact review**
(ver `.cursor/rules/documentation-governance.mdc`).

Use:

- `AGENTS.md` — invariantes globais do agente (este arquivo; manter curto);
- `docs/adr/` — decisões arquiteturais com trade-offs;
- `docs/crawler/` — contratos do crawler e playbooks por loja;
- `docs/pending/` — pendências e trabalho incompleto;
- `.cursor/rules/` — fluxos operacionais do agente;
- `docs/skills/README.md` — inventário de skills.

Não duplique a mesma regra em várias fontes; aponte para o documento canônico.
Não documente secrets, cookies, tokens ou credentials.

## Language

Todo output textual do agente destinado a humanos deve ser em **português do
Brasil (pt-BR)**. Detalhes: `.cursor/rules/language-pt-BR.mdc`.

Não traduza identificadores técnicos (classes, funções, paths, endpoints,
campos JSON, comandos, bibliotecas, contratos existentes). Código e nomes
seguem a convenção atual do repositório. Comments/docstrings: não migrar em
massa; seguir a convenção do módulo.

## Pending work

Se uma tarefa terminar com algo relevante ainda incompleto, registre em
[`docs/pending/`](docs/pending/) (ver `.cursor/rules/pending-work.mdc` e
`.cursor/rules/task-completion.mdc`).

Antes de marcar `BLOCKED` / desistir de uma lacuna técnica, cumpra a
investigação em `.cursor/rules/research-and-problem-solving.mdc` e preserve
na pendência o que já foi pesquisado e testado.

Pendências resolvidas **não** permanecem na lista de trabalho aberto: remova
do índice ativo e limpe TODOs/referências relacionadas na mesma alteração.

Não declare a tarefa completa em silêncio.

Índice: [`docs/README.md`](docs/README.md).

Comandos oficiais: `make test` (ciclo rápido; ver `docs/testing.md`),
`make test-performance` (durations + slow tests), `python -m pytest`,
`ruff check .`, `ruff format --check .`, `mypy src`,
`uvicorn scout_api.main:app --reload --app-dir src` e `docker compose up --build`.

## Skills

Skills complementam as regras do repositório; não as substituem.

**Política oficial:** skills orientam o agente; nunca decidem arquitetura. Acione só a skill mínima para a tarefa. Precedência: tarefa explícita → `AGENTS.md` → ADRs aceitos → `.cursor/rules/` → padrões do código → skills → genéricos. Soluções externas encontradas na pesquisa seguem a mesma precedência (ver `research-and-problem-solving.mdc`).

Skills de terceiros: pin → auditoria (`skill-scanner`) → registro em `docs/skills/README.md` (e `skills-lock.json` se vendored). Recomendações de framework devem ser adaptadas ao ScoutApiV2; não podem introduzir dependências, alterar contratos ou forçar nova arquitetura sem necessidade comprovada.

Inventário, matriz de acionamento e restrições: `docs/skills/README.md`. Governança agent: `.cursor/rules/skills-governance.mdc`.
