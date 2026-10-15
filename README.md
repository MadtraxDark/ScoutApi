# ScoutApiV2

Base arquitetural de uma API FastAPI organizada como modular monolith e preparada para testes, CI/CD e execução via Docker.

## Requisitos

Python 3.12+; Docker e Docker Compose.

## Estrutura

`src/scout_api/api` contém a composição HTTP; `core` contém configuração e infraestrutura transversal; `modules` contém features; `tests` contém testes separados do código; `docs/adr` contém decisões arquiteturais.

## Configuração

Copie `.env.example` para `.env` e ajuste os valores conforme o ambiente. O `.env` é ignorado pelo Git e não deve conter valores versionados.

## Docker

Runtime reproduzível do crawler: Camoufox 0.5.7 e browser oficial
156.0.1-beta.34 fixados. Modelo fpgen é preparado com permissão restrita para
`app`; capacidade C1 permanece 1. Workers não herdam o healthcheck HTTP da API.

Execute `docker compose up --build`. A API ficará disponível em `http://localhost:8000`; `GET /health` verifica a disponibilidade (`status` + `database`). O entrypoint aplica `alembic upgrade head` automaticamente no boot (`AUTO_MIGRATE=true`).

Para apontar ao Postgres do Supabase em vez do serviço Compose, defina
`DATABASE_URL` no `.env` (SSL/`sslmode=require`). Migrations sobem com o
container; no host use `make migrate` se necessário.
Detalhes: [`docs/persistence/supabase-postgres.md`](docs/persistence/supabase-postgres.md).

O perfil Camoufox é um bind mount em `./data/camoufox-profiles` (compartilhado com o seed local). Para aquecer a sessão Shopee/WAF com janela:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\python -m camoufox set official/stable/156.0.1-beta.34
.\.venv\Scripts\python -m camoufox fetch
.\scripts\seed-camoufox-profile.ps1
```

Na janela: resolva challenge/login, abra um produto, pressione Enter no terminal. Depois a API sobe de novo usando o mesmo perfil.

## Execução local

Execute `python -m venv .venv`, `python -m pip install -e ".[dev]"` e `uvicorn scout_api.main:app --reload --app-dir src`.

## Validação

```bash
make test              # ciclo diário (exclui live/slow)
make test-performance  # + --durations e resumo de testes lentos
make test-live         # lojas reais
make test-full         # tudo
ruff check .
ruff format --check .
mypy src
```

Detalhes de markers, budgets e regras: [`docs/testing.md`](docs/testing.md).
Observabilidade de tempo: [`docs/performance.md`](docs/performance.md).

Para desenvolvimento, atualize somente os spiders sem reconstruir a imagem:

```powershell
.\scripts\update-spiders.ps1
```

Esse comando monta `spiders` e `services` do crawler no container e ativa o
reload automático. Assim novos spiders (e o `store_resolver`) entram sem
rebuild. Use `.\scripts\update-spiders.ps1 logs` para acompanhar a API ou
`.\scripts\update-spiders.ps1 down` para pará-la. Alterações no Dockerfile,
dependências ou bibliotecas do sistema ainda exigem `docker compose up --build`.

## ADRs

As decisões ficam em [`docs/adr`](docs/adr/). Para uma nova decisão, copie [`docs/adr/template.md`](docs/adr/template.md), use o próximo número e registre contexto, alternativas, decisão, justificativa e consequências.

Índice de documentação do projeto (arquitetura, crawler, lojas, regras): [`docs/README.md`](docs/README.md).

## Crawler de preços

O crawler fica em `src/scout_api/modules/crawler`. Ele separa **oferta comercial**
(`ProductOffer`) de **detalhes do produto** (`ProductDetails`) e compõe o contrato
completo (`ProductPriceItem`) no fluxo full. Usa `Decimal` para dinheiro, TTL
adaptativo, fila de prioridade, fingerprint determinística, cache, backoff/jitter
e circuit breaker. Cache, single-flight e cooldown do `ScrapeGuard` usam Redis
quando `REDIS_URL` está definido (L1 memória + L2 Redis, fail-open; ADR 0020).
PostgreSQL permanece a fonte de verdade de matching e histórico.

**Autenticação:** rotas de negócio exigem `Authorization: Bearer` (Supabase
Auth / Google). Públicos: `GET /health` e `/auth/*` de sessão. Detalhes:
[`docs/security/api-auth.md`](docs/security/api-auth.md) e ADR 0023.

Rate limiting usa reposição contínua por escopo (ADR 0047). As variáveis
`RATE_LIMIT_*_PER_MINUTE` definem capacidade e reposição por minuto; o 429
informa o escopo e `Retry-After` para coordenar a pausa no cliente.

- `POST /crawl` — scraping completo (oferta + detalhes) via `ProductScrapeService`.
  Use `include_images=true` para incluir a galeria (`extract_images`); o padrão é
  `false` (sem parsing de imagens). Ver ADR 0012.
- `POST /crawl/offer` — consulta leve (preço/seller/disponibilidade) via
  `OfferScrapeService`, sem executar a extração de detalhes nem de imagens.
- `POST /match` — Product Matching: scrape da URL de referência, busca ao vivo
  nas lojas com Search adapter registrado (Amazon BR/US, Kabum, Magalu, Shopee,
  Best Buy, Nissei, Shopping China), score precision-first (GTIN → marca+modelo
  → título auxiliar) e persistência opcional no PostgreSQL (ADR 0019). Use
  `persist=false` sem `DATABASE_URL`.
- `POST /products` — cadastro/localização de produto canônico (get-or-create
  sem overwrite; dedup por GTIN ou store+canonical_url). `GET /products/{id}`
  consulta o registro e listings.
- `POST /offers/refresh` — reconsulta listings persistidos e registra eventos
  (`price_changed`, `seller_changed`, `offer_removed`, …) sem sobrescrever
  histórico (ADR 0019). Requer `DATABASE_URL`.

PostgreSQL: fonte de verdade (Compose local **ou** Supabase hospedado).
Configure `DATABASE_URL` com driver `postgresql+psycopg` (ver `.env.example` e
[`docs/persistence/supabase-postgres.md`](docs/persistence/supabase-postgres.md)).
Migrations: automáticas no boot do container; no host `alembic upgrade head`
/ `make migrate` (URI direct/session `:5432`, não transaction pooler `:6543`).
A API não usa a Supabase Data API; o frontend não acessa o banco.

Redis: serviço `redis` no Compose (cache/coordenação, não persistente). Opcional
fora do Compose — sem `REDIS_URL` a API usa só memória local.

Ambos buscam HTML com **Camoufox** (Firefox anti-detect) por padrão e delegam o
parsing aos spiders (`magazineluiza`, `nissei`, `shopee`, `amazon`, …). Spiders não fazem
I/O de rede. O proxy residencial (`CAMOUFOX_PROXY_URL`) é **store-aware** (ADR
0014 / Proxy Cost Mode): todas as lojas tentam **direto primeiro**; proxy só
após bloqueio classificado (`UPSTREAM_BLOCKED`). Com proxy ativo, tráfego mínimo
(sem image/media/font; `include_images` ignorado). Na Shopee o fetch para após
`get_pc`, omite galeria por política de custo e cacheia `ProductOffer` no
`ScrapeGuard`. Amazon BR (`amazon.com.br`) e Amazon US (`amazon.com`) usam
`store=amazon` com `country`/`currency` do marketplace (ADR 0015); ofertas são
independentes e sem conversão cambial no spider.
No Linux/Docker o browser usa display virtual (`Xvfb`) + `geoip` para passar
Cloudflare (Nissei). Desative com `CAMOUFOX_ENABLED=false` para fallback `urllib`.
Na primeira instalação local, rode `python -m camoufox fetch`. Ver ADR 0011–0014.

Spiders de referência: `kabum`, `bestbuy`, `magazineluiza` e `nissei`. Eles são
`Spider` customizados (não `CrawlSpider`) porque o parsing de produto e JSON-LD é
específico e conservador. Shopee, AliExpress e eBay devem preferir APIs oficiais ou
integrações autorizadas para ofertas/sellers; os demais adapters podem ser adicionados
sem duplicar a infraestrutura base.

```bash
python -m camoufox fetch
scrapy crawl kabum -a start_urls=https://www.kabum.com.br/produto
scrapy crawl magazineluiza -a start_urls=https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony-com-2-jogos/p/240590700/ga/gap5/?seller_id=magazineluiza
docker compose up --build -d
python -m pytest
```

O crawler visa resolver CAPTCHA/challenge, contornar bloqueios operacionais e
assegurar a coleta de dados públicos (ADR 0017). Camoufox cobre fingerprint/WAF;
Mitmproxy e proxies residenciais podem complementar IP. Proxy segue Proxy Cost
Mode (ADR 0014).
