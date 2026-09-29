# Investigação: embeddings no Product Match

- **Status:** análise inicial (2026-09-24), atualizada com runtime local (2026-09-26); ativa só em shadow local
- **Data da pesquisa:** 2026-09-24
- **Escopo:** análise original e comparação; atualização posterior descreve a integração experimental abaixo. `active` continua desligado.

## Resumo executivo

O Product Match separa descoberta de candidatos e decisão de identidade. Consultas progressivas e adapters SERP descobrem produtos em lojas ao vivo; MatchingEngine aplica identificadores, gates de variante/condição e score lexical. Embeddings são mais adequados, inicialmente, como evidência complementar em casos inconclusivos entre PDPs já extraídos, depois dos bloqueios determinísticos. Candidate generation vetorial exige corpus global que o fluxo atual não possui.

Recomendação para a prova: (1) medir o matcher atual num conjunto rotulado e congelado; (2) comparar embeddings multilíngues em shadow mode apenas para candidatos já raspados que passam os gates e ficam na zona de incerteza; (3) manter GTIN, MPN, códigos de modelo, variantes críticas, condição, bundle/acessório e regras de categoria como regras determinísticas não compensáveis; (4) não adicionar vector store; (5) usar Redis apenas como cache L2 bounded com TTL e namespace, conforme ADR 0046. Similaridade semântica sozinha nunca promove auto_match.

Esta é hipótese para experimento, não evidência de melhoria. A decisão final depende de pares reais do ScoutApi rotulados.

### Implementação iniciada

A primeira fatia está em `matching/embedding_text.py`: gera entradas `raw`, `normalized`, `structured` e `hybrid` a partir de `ProductIdentity`, sem rede, persistência ou integração com `MatchingEngine`. Campos dedicados a GTIN/MPN/código não são adicionados à representação estruturada; valores de `model`, atributos e títulos podem ainda conter códigos, e não há filtragem automática. O runner offline `scripts/bench_matching_baseline.py` consome JSONL versionado, valida identidades/rótulos, exige grupos sem vazamento entre splits, mede o baseline determinístico por split e emite métricas sem expor títulos. O dataset real continua ausente até haver revisão dos pares. Nenhum provider ou efeito no fluxo de Match foi ativado.

## Arquitetura atual

### Fluxo reconstruído

Produto de referência (CanonicalProduct ou URL) → identity_reference_item/ProductIdentity → build_search_queries → StoreSearchService/adapters → SearchCandidate[] (URL, título/hint, ID, metadados) → ProductMatchService raspa PDP via ProductScrapeService → identity_from_price_item → MatchingEngine.score → reject/review/auto_match e MatchReason → melhor resultado por loja → persistência PostgreSQL.

| Responsabilidade | Código atual | Ponto relevante |
|---|---|---|
| Identidade e consultas | src/scout_api/modules/matching/identity.py: ProductIdentity, identity_from_price_item, build_search_queries e normalizadores | Preservar códigos e atributos; embedding não reescreve identidade. |
| Candidate generation | matching/store_search_service.py, search_adapters/registry.py, adapters por loja, search_candidate.py | SERP gera candidatos transitórios ao vivo; não há corpus completo de produtos externos. |
| Orquestração | matching/product_match_service.py: match, match_from_item, busca progressiva, scrape e chamada do matcher | Integração potencial após extrair candidato e antes da decisão/persistência. |
| Scoring e regras | matching/engine.py: MatchingEngine.score | AUTO_THRESHOLD=0.92, REVIEW_THRESHOLD=0.75, TITLE_ONLY_CAP=0.74; gates retornam antes do score. |
| Persistência | matching/models.py, repository.py e migrations Alembic | CanonicalProduct, ProductIdentifier, StoreListing, OfferSnapshot e logs de runs; nenhum vetor armazenado. |
| Job durável e endpoint síncrono | match_run_service.py, match_run_claim.py, match_run_worker.py e matching/router.py | ProductMatchRun usa fila PostgreSQL; o endpoint POST /match ainda chama ProductMatchService.match diretamente. Não depender de Redis para fila. |
| Observabilidade | core/performance.py, MatchStoreOutcome, logs de decisão, MatchCandidateLog | Tempos de scrape e resultado por loja existem; embedding requer tempo e resultado próprios. |
| Testes existentes | tests/unit/test_matching_engine.py, test_matching_regression.py, test_match_runs.py, test_redis_cache.py | Reutilizar regressões e adicionar dataset/benchmark offline separado. |

MatchingEngine.score não é um blend simples. Retorna cedo em conflito de códigos de monitor, variante, identidade crítica, acessório, bundle, formato/condição, entre outros gates. Depois avalia sinais exatos, marca/modelo, título e preço. Embedding deve ser uma etapa adicional restrita a casos elegíveis, sem alterar um reject determinístico.

ProductMatchService chama self._engine.score(local_identity, candidate_identity) após raspar cada PDP. Tem cache local por execução para SERP/PDP e StoreAttemptBudget de consultas, requests externos e navegação por loja. MatchCandidateLog e outcomes de store-run permitem guardar decisão e duração; shadow exige preservar também o resultado semântico ou emitir eventos estruturados consultáveis.

### Persistência, jobs e decisões aceitas

CanonicalProduct guarda título/marca/modelo/atributos JSON; product_identifiers mantém identificadores; store_listings e offer_snapshots guardam ofertas/histórico. ProductMatchRun é persistente, uma execução ativa por produto, processada por match-runner e consultada por polling (ADR 0036). Não há tabela vetorial, extensão pgvector declarada no modelo, provider ou worker de embeddings.

Há também caminho síncrono legado em matching/router.py, que executa ProductMatchService.match no request. Para preservar o wall time HTTP, a primeira instrumentação semântica deve rodar apenas em MatchRun durável (com feature flag/opção explícita), mantendo /match no baseline até medir o custo e aprovar sua inclusão. Não criar uma fila Redis adicional.

ADRs relevantes:

- ADR 0019 e 0024: matching precision-first, GTIN/MPN/modelo, bloqueadores, título auxiliar e thresholds; título sozinho nunca gera auto_match.
- ADR 0020: Redis é cache/coordenação fail-open, nunca fonte de verdade.
- ADR 0033: retrieval tolerante e matching rigoroso; não baixar threshold para corrigir falha de busca.
- ADR 0036: Product Match é job durável PostgreSQL, independente do lifecycle da UI.
- ADR 0038: candidate discovery Store Search e scraping PDP são capabilities separadas.
- ADR 0040: aliases tipados PT/EN/ES foram adotados em vez de embeddings como decisor; embeddings podem ser reavaliados se métricas de recall/precisão justificarem, sem sobrepor bloqueadores.

## Auditoria Redis

| Item | Evidência no repositório | Implicação |
|---|---|---|
| Imagem | compose.yaml declara redis:7-alpine (tag móvel, não digest ou patch fixo) | Arquivo não prova qual build está executando. |
| Memória/persistência | appendonly no, RDB `save 3600 1 300 100 60 10000`, maxmemory 256mb e allkeys-lru; sem volume Redis | Cache reconstruível; snapshots RDB ficam no container sem volume durável e qualquer chave pode ser removida por pressão. |
| Conexão | API, monitor e match-runner em redis://redis:6379/0 | Todos compartilham serviço, DB lógico e maxmemory. DB lógico não isola eviction. |
| Configuração | core/config.py centraliza URL; timeout conexão 0,3 s e socket 0,5 s; Compose TTL scrape 300 s | Timeouts são curtos para coordenação/cache. |
| Cliente | pyproject.toml declara redis>=5,<7 | uv.lock observado não contém pacote redis; versão resolvida não é comprovada. |
| Gateway | crawler/core/redis_client.py: conexão lazy, health_check_interval=30 e fail-open | Operações podem degradar para cache local. |
| Cache/chaves | crawler/core/cache.py: L1+L2, envelope JSON versionado; redis_keys.py namespace scout:v1 | Vetores disputariam memória com respostas de scraping. |
| Usos | cache.py, scrape_guard.py, distributed_single_flight.py, distributed_cooldown.py, profile_lock.py | Cache de scraping, single-flight, cooldown e lock de perfil. |
| Fila Product Match | PostgreSQL em match_run_worker.py com FOR UPDATE SKIP LOCKED | Redis não opera fila de matching. |
| Busca vetorial | `MODULE LIST` runtime vazio; nenhum RedisVL, FT.CREATE/FT.SEARCH, VSIM ou RediSearch carregado | RediSearch não está disponível nesta instância. |

Snapshot runtime em **2026-09-24** do Compose local (`redis` saudável): `redis:7-alpine`, Redis **7.4.11**, imagem `redis@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`, modo standalone. `MODULE LIST` vazio (sem módulo de busca); `maxmemory=268435456`, `allkeys-lru`, `appendonly=no`; RDB usa os limiares default `3600 1 300 100 60 10000`, com 73 saves e último BGSAVE `ok`. Não há volume no Compose para persistir os snapshots após recriação do container. No instante da consulta: `used_memory=1.39M`, pico `1.45M`, `evicted_keys=0`, `keyspace_hits=9123`, `keyspace_misses=640`, `total_commands_processed=36898`. Uma amostra de `redis-cli --latency -i 0.1` mediu 11 PINGs: min 0 ms, média 0.18 ms, max 1 ms. Contadores são desde o início do processo e latência é uma amostra curta, não p95 ou benchmark de carga.

Redis Search suporta KNN, filtros e pesquisa textual quando Query Engine está instalado; a documentação distingue Redis Stack da imagem Redis básica ([vector search](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/), [Redis Stack Docker](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-stack/docker/)). Redis 8 introduz Vector Sets beta, não disponível pela configuração atual ([Redis 8](https://redis.io/docs/latest/develop/whats-new/8-0/)).

| Função | Decisão para a primeira prova |
|---|---|
| Cache de embeddings | Redis L2 bounded com TTL e chaves hash, após verificar o orçamento de memória e medir reuso; LRU local é L1. A eviction só causa recomputação e não dispara fetch de PDP. |
| Cache de score de pares | Adiar: dot product é barato e candidatos variam. Se necessário, chavear ambos input hashes, representação e versão do modelo. |
| Locks/deduplicação | Já existem para scraping. Não criar novos até existir geração concorrente/backfill. |
| Jobs | Não usar Redis. Reusar MatchRun/PostgreSQL; backfill futuro pode ser job idempotente PostgreSQL. |
| Vector search/KNN/hybrid | Não está comprovado e é desnecessário para dezenas de candidatos transitórios. |
| Fonte persistente | Nunca Redis. Vetores duráveis, se necessários, devem estar em PostgreSQL/pgvector ou store durável dedicado. |

## Pesquisa técnica e alternativas

Pesquisa consultada em 2026-09-24. Documentação/código/papers formam a evidência principal; community posts são relatos e opiniões, não benchmarks controlados.

### Multilingualidade, modelos e texto de entrada

WDC Products mede entity matching em ofertas reais de milhares de lojas e destaca corner cases, entidades não vistas e tamanho de conjunto rotulado ([benchmark](https://webdatacommons.org/largescaleproductcorpus/wdc-products/)). O paper de transformers multilíngues em product matching relata resultados após fine-tuning em inglês/polonês, não garantia zero-shot em PT-BR↔EN para SKU ([paper](https://arxiv.org/abs/2205.15712)). MTEB é referência ampla, não substitui validação local de produtos ([MTEB](https://arxiv.org/abs/2210.07316)).

| Modelo | Evidência atual | Trade-off para ScoutApiV2 |
|---|---|---|
| OpenAI text-embedding-3-small (API) | Ativo; preço consultado US$0,02 por milhão de tokens ([modelo/preço](https://developers.openai.com/api/docs/models/text-embedding-3-small), [guia](https://platform.openai.com/docs/guides/embeddings)) | Baseline simples, sem peso local; rede, rate limits, disponibilidade, secret management e envio de títulos/atributos a terceiro. |
| intfloat/multilingual-e5-base (local) | Model card declara 94 idiomas, 768 dimensões, licença MIT e Sentence Transformers ([model card](https://huggingface.co/intfloat/multilingual-e5-base)) | Sem tarifa por token e dados locais; runtime, pesos, RAM, cold start, CPU e serving; seguir query/passage prefixes do modelo. |
| BAAI/bge-m3 (local) | Código declara 1024 dimensões, 8192 tokens, dense/sparse/multi-vector e peso ~2,27 GB ([spec](https://github.com/FlagOpen/FlagEmbedding/blob/master/docs/source/bge/bge_m3.rst), [repo](https://github.com/FlagOpen/FlagEmbedding)) | Recursos multilíngues amplos, mas footprint incompatível com imagem atual sem serviço/worker dimensionado; features sparse/multi-vector são excesso no início. |
| Cohere Embed (API) | Produto declara retrieval multilíngue; preço por tier ([Embed](https://cohere.com/embed), [pricing](https://cohere.com/pricing)) | Alternativa para comparação, medir custo/latência/PT-EN-ES nos mesmos pares. |

Recomendação para o primeiro PoC controlado: usar text-embedding-3-small como baseline inicial pela integração simples e preço unitário baixo, com chamadas em lote e gating; comparar multilingual-e5-base como principal alternativa local se privacidade/volume justificar serving próprio. A opção de menor custo marginal é o modelo local se já houver CPU/serviço ocioso suficiente, mas o custo operacional total precisa entrar no benchmark. Não declarar vencedor antes do dataset rotulado. BGE-M3 fica como opção se provar benefício que justifique footprint/operação. Provider externo só recebe dados minimizados após revisar se título/atributos contêm texto fornecido pelo usuário.

Testar: título bruto; título normalizado sem apagar discriminantes; texto estruturado marca/família/modelo/variante/storage/cor/condição; híbrido de título mais campos. Começar comparando normalizado e híbrido. Não manter title/model/attributes embeddings distintos sem ablação que justifique triplicar custo/memória. GTIN, MPN, OPN e sufixos como 5800X3D, VG259Q5A, A3256 e MG7L4LL/A permanecem sinais determinísticos.

Se cache for aprovado após medir: serializar vetor em formato binário validado (dimensão/dtype explícitos), nunca pickle; chave conceitual = provider + model_version + representation_version + normalizer_version + SHA-256 do texto canônico. TTL limitado e namespaces separados do scrape cache; mudança de qualquer versão/hash invalida naturalmente a entrada. Inicialmente usar cache L1 bounded por tamanho/TTL somente se demonstrar hit rate útil. Persistência futura deve identificar provider/model/version/dimensions/input_hash/representation_version/created_at/updated_at e recalcular quando texto canônico ou modelo mudar.

Bi-encoders geram vetor fixo e comparam eficientemente; cross-encoder pode reranquear top-K com custo maior. Sentence Transformers documenta o padrão em duas etapas ([uso](https://sbert.net/docs/sentence_transformer/usage/usage.html), [cross-encoders](https://sbert.net/examples/cross_encoder/applications/README.html)). Não adicionar cross-encoder até medir ganho: dezenas de candidatos já têm custo de scrape e cosine não requer modelo pareado.

### Papéis dos embeddings

| Abordagem | Avaliação | Parecer |
|---|---|---|
| Feature em todos candidatos | Gasta em matches óbvios/rejects claros; amplia latência/custo e pode contaminar score explicável. | Não inicialmente. |
| Fallback em inconclusivos | Reduz chamadas e pode recuperar diferença de idioma com marca/modelo/atributos compatíveis; inseguro se desfizer reject determinístico. | Candidata após shadow. |
| Candidate generation vetorial | Exige corpus global; lojas são consultadas ao vivo e listings históricos não incluem todo produto das lojas. | Adiar; exigir corpus e Recall@K melhor. |
| Reranking | Coerente para candidatos raspados, embeddings em batch e cosine. | Variante de fallback/gating, sem KNN. |
| Híbrida | Busca atual descobre; regras descartam conflitos; lexical/atributos decidem; semântica auxilia incerteza. | Recomendada para experimento. |

### Score e zona de incerteza

Sinais para ablação/calibração: match/conflito GTIN, MPN e código; marca, modelo/família/sufixo, storage, cor/variante/categoria/condição, lexical, presença de dados, score atual e embedding cosine. Não usar média fixa 50/50. Hard constraints precedem score estatístico. Similaridade não pode sozinha cruzar auto_match; promoção futura exige combinação de evidência determinística calibrada num conjunto separado.

Fluxo: identificadores exatos/conflitos → bloqueadores de variante/condição/categoria/bundle/acessório → atributos tipados e compatibilidade modelo/marca → score atual → só estado intermediário elegível recebe embedding → política calibrada ou abstenção.

Exact identifier, conflito estrutural, decisão atual clara ou vocabulário existente que resolve variação não chama modelo. Cor PT/EN já tratada por ADR 0040 dispensa embeddings. Atributo ausente segue unknown. Produto com pouca identidade tende a review/uncertain. Candidate generation atual permanece independente da validação, conforme ADRs 0033/0040.

### Vector stores

| Tecnologia | Capacidades/trade-offs | Adequação |
|---|---|---|
| Redis Search/RediSearch | KNN, metadados e híbrido se Query Engine presente; não declarado e RAM compartilhada. | Não: módulo/capacidade não confirmados e eviction conflita. |
| PostgreSQL + pgvector | Busca exata, HNSW/IVFFlat, SQL/filtros; HNSW consome memória e busca aproximada filtrada precisa tuning ([repo](https://github.com/pgvector/pgvector)). Supabase documenta extensão vector ([guia](https://supabase.com/docs/guides/ai/vector-columns)). | Melhor candidato futuro para vetores persistentes do catálogo. Confirmar extensão no serviço; compose usa postgres:16-alpine. |
| FAISS | Biblioteca CPU/GPU ANN, sem serviço ([repo](https://github.com/facebookresearch/faiss)). | Excelente para benchmark/índice imutável; update, backup e sincronização ficam com a aplicação. |
| Qdrant | Serviço, payload filters, HNSW e vetores em disco ([docs](https://qdrant.tech/documentation/guides/)). | Só se corpus/escala justifica nova operação. |
| Weaviate | Híbrido BM25+vetor, filtros e reranking ([docs](https://docs.weaviate.io/weaviate/search/hybrid)). | Útil se busca híbrida em catálogo for requisito; excessivo para pares da execução. |
| Milvus | ANN/HNSW e scalar filters; HNSW tem overhead de memória ([HNSW](https://milvus.io/docs/hnsw.md), [scalar index](https://milvus.io/docs/scalar_index.md)). | Orientado a carga/ingestão maiores; não justificado hoje. |

Comparar dezenas de candidatos pede cosine exato em memória, não ANN. ANN não reduz custo de descoberta/scrape. Se surgir corpus canônico útil, testar primeiro pgvector por compartilhar transação/backup, confirmando extensão. Vector DB dedicado só se volume/QPS/ingestão/latência medidos pedirem.

## Dataset, benchmark, métricas e critérios

### Provider sem custo recorrente

Para executar o experimento sem API hospedada, o contrato foi generalizado para
um endpoint OpenAI-compatible configurável. O Compose oferece TEI CPU como
serviço separado (perfil `embeddings`) usando `intfloat/multilingual-e5-small`
(384 dimensões, licença MIT) e volume persistente. Isso evita exigir credencial
ou compartilhar o processo/modelo com o match-runner; não elimina o custo de
CPU/RAM nem o download inicial dos pesos. O serviço está limitado a 2 CPUs e
2 GiB e continua opt-in; o modo embeddings permanece `off` por padrão.

| Opção pesquisada | Custo/limite e chave | CPU/RAM, API e licença | Resultado |
|---|---|---|---|
| HF Inference Providers | $0,10 de crédito mensal no nível Free, sujeito a mudança; além disso é cobrado. Requer token. | Compute hospedado; compatibilidade varia por provider/modelo. Serviço e modelos têm termos próprios. | Free tier, não contínuo ilimitado; não escolhida. |
| Jina Embeddings API | Chave free: página lista 100 RPM/100k TPM; 500 RPM/2M TPM é faixa paga. Cotas/política podem mudar. | Compute hospedado; `POST /v1/embeddings`; modelos multilíngues e termos do provider. | Útil para avaliação, mas terceiro, credencial e limite; não escolhida. |
| Google Gemini Embeddings | Atualmente lista free tier para Gemini Embedding 2; uso tem cotas e conteúdo Free pode ser usado para melhorar produtos. | Compute hospedado; REST e API key/projeto Google; termos do serviço. | Não é irrestrito e envia dados externos; não escolhida. |
| Cohere Trial | Trial grátis, mas limitado e não permitido para produção/comercial; docs consultadas divergem quanto ao rate-limit de Embed. | Compute hospedado; REST, modelos multilíngues; serviço comercial. | Serve para PoC, não Product Match contínuo. |
| Ollama + EmbeddingGemma | Self-hosted, sem custo de chamada; artefato listado ~622 MB; Gemma Terms, não MIT. | Modelo local ~300M; API nativa `/api/embed`, engine Ollama MIT; adapter necessário. | Alternativa local válida; TEI foi mais direto e o modelo E5 tem MIT. |
| FastEmbed | Self-hosted ONNX/biblioteca, sem custo de API; Apache-2.0; inclui multilingual-E5-small. | CPU local e batching em biblioteca; sem servidor HTTP próprio. Modelo escolhido é MIT. | Exigiria construir/operar API separada. |
| TEI + multilingual-E5-small | Self-hosted; custo de API R$ 0; Apache-2.0; modelo MIT, 384 dimensões, 94 idiomas. | Docker CPU; memória observada 853–900 MiB; `/v1/embeddings`, healthcheck, batching. | Escolhido; endpoint e batching integrados no projeto. |

Redis armazena vetores apenas como cache efêmero L2 sob TTL; o limite de entradas
continua na L1 local e o orçamento global Redis permanece limitado por
`allkeys-lru`. Não há textos, fonte de verdade nem índice vetorial. A escolha do
modelo é experimental e não altera qualquer decisão determinística. Evidência
do experimento local e seu limite de escopo estão em
[PENDING-024 resolvida](../pending/resolved/PENDING-024-product-match-embeddings-shadow.md).

### Comparação dos modelos

| Modelo | Tamanho/dimensão | Idiomas/licença | Leitura para este uso |
|---|---|---|---|
| `intfloat/multilingual-e5-small` | 117M parâmetros, 384D | 94 idiomas, MIT | Escolhido por menor peso/latência, serving CPU, licença permissiva; medido aqui. |
| `intfloat/multilingual-e5-base` | 278M parâmetros, 768D | Multilíngue, MIT | Mesmo recipe/família com custo de CPU/RAM maior; sem prova de ganho no corpus ScoutApi. |
| `google/embeddinggemma-300m` | 308M parâmetros; artefato Ollama ~622 MB | 100+ idiomas; Gemma Terms of Use | Boa alternativa on-device, mas modelo maior e licença com termos próprios. |
| `Qwen/Qwen3-Embedding-0.6B` | 0,6B parâmetros, até 1024D | 100+ idiomas, Apache-2.0 | Potencial de qualidade maior e custo operacional superior ao necessário para um rerank auxiliar. |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 118M parâmetros, 384D | 50 idiomas, Apache-2.0 | Alternativa leve; cobertura linguística menor que E5. |

Resultados MTEB publicados são benchmarks gerais/retrieval, não avaliação de
identidade de SKU. A página do E5 explicita prefixo `query: ` para similaridade
simétrica e observa que o score absoluto tende a concentrar entre 0,7 e 1,0;
por isso os cossenos do smoke e suas sobreposições não são threshold de decisão.

### Fontes e alcance da pesquisa

Foram consultados documentação de cobrança/modelos/limites, model cards e
papers, GitHub e issues, além de discussões de Reddit, Hacker News, Stack
Overflow, Hashnode e Medium. Relatos de comunidade foram usados como sinais de
operação e não como prova de cota/licença. Não apareceu API pública que reúna
uso ilimitado garantido, sem chave e sem custo; as opções hospedadas mantêm
algum limite, regra de dados ou dependência comercial.

- [HF Inference Providers: créditos e cobrança](https://huggingface.co/docs/inference-providers/en/pricing)
- [Jina Embeddings API: cotas atuais](https://jina.ai/embeddings/)
- [Google Gemini Embeddings: pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Cohere: rate limits](https://docs.cohere.com/docs/rate-limits)
- [Cohere: trial versus production](https://cohere.com/pricing)
- [TEI GitHub / endpoints](https://github.com/huggingface/text-embeddings-inference)
- [MDN: semântica de HTTP 429/Retry-After](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/429)
- [Elastic Labs: benchmark de embeddings para busca de produtos](https://www.elastic.co/search-labs/blog/multimodal-embeddings-ecommerce-product-search)
- [FastEmbed GitHub](https://github.com/qdrant/fastembed)
- [EmbeddingGemma no Ollama](https://ollama.com/library/embeddinggemma:300m)
- [Qwen3 Embedding 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [MiniLM multilíngue](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
- Comunidade: [Reddit / TEI local](https://www.reddit.com/r/LocalLLaMA/comments/1inm9z7/), [Hacker News / inference local](https://news.ycombinator.com/item?id=41756863), [Stack Overflow / TEI](https://stackoverflow.com/questions/79681283/vertex-ai-tei-deployment-fails-for-private-hugging-face-model-could-not-downl), [Hashnode / local models](https://premai.hashnode.dev/private-rag-deployment-building-zero-leakage-retrieval-pipelines-for-enterprise), [Medium / CPU multilíngue](https://medium.com/@janschachtschabel/multilingual-embedding-models-in-2026-what-actually-works-on-cpu-d4bf3fe3d335).

Ver [ADR 0045](../adr/0045-self-hosted-product-match-embeddings-provider.md)
para decisão, limites, alternativas e fontes. A adequação precisa ser medida
no corpus local antes de qualquer modo ativo.

### Dataset e protocolo

Criar conjunto versionado rotulado, separado da produção, derivado de pares de MatchRun revisados. Campos: ids internos anonimizados, títulos/atributos necessários, categoria/store/idioma, label same_product/different_product/uncertain, evidências e razão. Revisar privacidade/licença e redigir URLs/secrets. Splits por produto/família e loja impedem vazamento de variantes do mesmo SKU.

Cobrir iPhone, Samsung Galaxy, Ryzen, GPU, SSD, placa-mãe e monitor; positivos PT↔EN↔ES com ordem/unidade/spacing/título diferentes; negativos difíceis iPhone Pro/Pro Max, 5800X/5800X3D, 256/512GB, VG259Q5A/VG259QM, Ti/non-Ti, DDR4/DDR5, used/refurbished, acessórios/bundles; atributos ausentes e identidade incompleta.

Matcher atual e alternativas rodam nos mesmos splits reproduzíveis; holdout não calibra threshold. Testes unitários validam invariantes determinísticos; runner offline mede qualidade estatística.

### Métricas e go/no-go

- Match final: precision, recall, F1, false positive/negative rate, matriz de confusão e métricas por idioma/categoria; falso positivo tem custo primário por preço de SKU incorreto.
- Retrieval futuro: Recall@K, Precision@K, MRR contra candidatos relevantes.
- Runtime: p50/p95 etapa semântica e run, chamadas/tokens, timeout/429, cache hit futuro, custo, RSS, CPU/GPU e Redis evictions.
- Go/no-go: holdout preserva precisão do baseline; recomendação conservadora é nenhuma regressão observada de auto-match nos hard negatives e ganho de recall estatisticamente sustentado; intervalos 95% bootstrap. Dataset insuficiente implica shadow/review, sem ativação automática. Não inventar threshold numérico sem labels/custo de erro acordado.
- Comparar matcher atual e matcher+embedding no mesmo tráfego/candidatos antes de qualquer impacto em oferta persistida.

Benchmark de 1, 10, 50, 100, 1000 candidatos decompõe referência uma vez/run, batch dos candidatos elegíveis, cosine O(N·d), rede/inferência, cold-start local, custo e memória. Os budgets por loja limitam valores altos; 1000 é stress sintético, não carga assumida. Medir hardware real; capacidade browser segue C1/ADR 0039.

Para N candidatos elegíveis, a geração pede no máximo N+1 textos (uma referência reutilizada e N candidatos), divididos em um ou mais batches conforme limite do provider; a similaridade exata então faz N·d comparações de componentes. O custo externo cresce com tokens do texto, não com KNN. No modo shadow, fazer a geração dentro da Run durável e fora do request síncrono legado; se provider exceder deadline, registrar erro de etapa e deixar a decisão atual intacta. Não bloquear a Run esperando backfill de vetor persistente.

## Segurança, falhas e observabilidade

Títulos são públicos em geral, mas CanonicalProduct pode incluir texto enviado por usuário. Antes de API externa: minimizar campos, rever retenção/região e política; segredo em env/secret manager. Timeout do modelo menor que deadline da store/run. Provider opcional; timeout, 429/5xx, Redis/modelo/worker indisponível usam matcher atual. Redis nunca vira dependência.

Eventos: provider/model version, embedding_used, score/bucket, decision_before, decision_after_shadow, gate reason, cache hit, latency, batch size, erro e tokens/custo. Não logar vetores ou chain-of-thought. Cache miss/eviction recalcula ou faz fallback.

## Plano de implementação futuro

| Fase | Objetivo/superfícies | Testes e saída |
|---|---|---|
| 0. Dataset/baseline | Fixtures em tests/fixtures/matching/ e runner em scripts; MatchingEngine/regressões. | Labels revisados, holdout fixo e margem de precisão aprovada. |
| 1. Provider | Interface nova em modules/matching; API/local e config opcional em core/config.py; pins/dependências em pyproject.toml e uv.lock após escolha. | Fake provider, dimensão, vazio, timeout, rate limit e fallback; flag off idêntica. Aplicação inicial só em MatchRun durável, não no POST /match síncrono. |
| 2. Representação/batching | Builder baseado em identity.py, batching/reuso em product_match_service.py; sem persistência/Redis. | Hash/version/canonicalização e 4 formatos testados; calls/latência medidos. |
| 3. Shadow | Integração após gates de engine.py/product_match_service.py; decisão atual não muda; persistir resultado só se auditoria durável necessitar. | Nenhum provider em hard conflict; decisão atual invariável; comparação observável. |
| 4. Avaliação | Shadow controlado e calibração separada do holdout. | CI por categoria/idioma, precisão/recall, latência/custo e revisão de erros. |
| 5. Zona de incerteza | Flag gated só para estados inconclusivos elegíveis; política calibrada. | Hard negatives e falha de provider cobertos; guardrail e rollback. |
| 6. Persistência opcional | Só com reuso demonstrado: models.py, repository.py, migration Alembic, pgvector se extensão confirmada; backfill PostgreSQL idempotente. | Versionar provider/model/dimensions/representation/input hash/timestamps; backfill retomável e comparação v1/v2. |
| 7. KNN condicional | Corpus histórico útil e Recall@K melhor comprovados; FAISS offline/pgvector antes de serviço. | Recall, filtros, RAM/p95, update, backup e custo justificam serviço. |
| 8. Rollout | Shadow → comparação → zona intermediária por coorte/percentual → ampliar ou rollback. | Dashboard/guardrails precision, FP, health, custo, p95 e kill switch. |

Arquivos prováveis: matching/identity.py, engine.py, product_match_service.py, match_run_worker.py; novos módulos provider em matching; tests/unit/test_matching_engine.py e test_matching_regression.py; core/config.py, compose.yaml, .env.example, pyproject.toml, uv.lock. Persistência posterior: matching/models.py, repository.py e migration nova. Reconciliar uv.lock (não lista redis apesar de pyproject declará-lo). Atualizar docs/matching/README.md e docs/README.md caso vire orientação operacional. Compromisso arquitetural aprovado exige novo ADR sequencial; não reescrever ADR 0040.

## Riscos

| Risco | Mitigação |
|---|---|
| Similaridade confunde variantes | Hard gates, negativos difíceis, abstenção e precisão como guardrail. |
| Códigos/SKU perdem peso | Comparação determinística exata, sem remover sufixos. |
| Modelo não alinha idiomas | Holdout local PT/EN/ES por categoria. |
| API lenta/cara | Batch, timeout, fallback, flag e custo medido. |
| Redis expulsa scrape cache | Namespace, TTL e limite da L1; eviction global só reduz hit ratio. Medir `evicted_keys` durante uso representativo. |
| Modelo local pressiona browser worker | Inferência isolada e dimensionada; não carregar no processo Camoufox sem benchmark. |
| Modelo/texto muda | Versionar provider/model/dimensões/representação/normalizador/hash; backfill progressivo. |
| Leakage/labels ruins | Split por família/loja, revisão humana, holdout e intervalos de confiança. |

## Fontes externas

### Primárias, código e papers

- Redis: [vector search/KNN](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/), [Docker Redis Stack](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-stack/docker/), [Redis 8 Vector Sets beta](https://redis.io/docs/latest/develop/whats-new/8-0/).
- [pgvector](https://github.com/pgvector/pgvector); [Supabase vector columns](https://supabase.com/docs/guides/ai/vector-columns); [FAISS](https://github.com/facebookresearch/faiss); [Qdrant](https://qdrant.tech/documentation/guides/); [Weaviate hybrid](https://docs.weaviate.io/weaviate/search/hybrid); [Milvus HNSW](https://milvus.io/docs/hnsw.md).
- [OpenAI text-embedding-3-small e preço](https://developers.openai.com/api/docs/models/text-embedding-3-small); [guia de embeddings](https://platform.openai.com/docs/guides/embeddings).
- [multilingual-e5-base](https://huggingface.co/intfloat/multilingual-e5-base); [FlagOpen BGE-M3](https://github.com/FlagOpen/FlagEmbedding).
- Sentence Transformers [uso](https://sbert.net/docs/sentence_transformer/usage/usage.html) e [cross-encoder](https://sbert.net/examples/cross_encoder/applications/README.html).
- [WDC Products](https://webdatacommons.org/largescaleproductcorpus/wdc-products/); [transformers multilíngues para product matching](https://arxiv.org/abs/2205.15712); [MTEB](https://arxiv.org/abs/2210.07316).

### Comunidade (evidência secundária/anecdótica)

- GitHub pgvector [issue de filtragem/HNSW](https://github.com/pgvector/pgvector/issues/751): ilustra diferença de busca aproximada e filtros; issue histórica não representa necessariamente versão atual.
- Stack Overflow: [Redis KNN sem resultados](https://stackoverflow.com/questions/76830371/knn-vector-similarity-search-in-redis-is-not-returning-any-results) mostra possível confusão de schema/index; [timeout FT.SEARCH](https://stackoverflow.com/questions/79804992/stackexchange-redis-redistimeoutexception-on-ft-search-with-redis-stack-vector-i) é caso isolado .NET, não benchmark Python.
- Reddit: [escolha de vector DB](https://www.reddit.com/r/vectordatabase/comments/1v5ezl7/how_do_you_decide_which_vector_db_to_use/) e [pgvector/Redis](https://www.reddit.com/r/MachineLearning/comments/1e6rhjx) são opiniões/experiências, não testes controlados.
- Hacker News: [debate pgvector](https://news.ycombinator.com/item?id=45798479) expõe trade-offs, sem resolver escala deste catálogo.
- Medium: [entity resolution para product matching](https://medium.com/data-science/streamlining-e-commerce-leveraging-entity-resolution-for-product-matching-6a507fd5e925) e [similarity search em e-commerce](https://medium.com/datascience-semantics3/similarity-search-for-product-matching-conference-talk-a80b51091b5f) contextualizam usos, sem validar ganho local.
- Buscas não acharam conteúdo Hashnode ou blogs de engenharia de Cloudflare/Netflix/Discord/Uber/Stripe/Shopify/GitHub/Meta/Google que alterassem recomendação. MDN não é aplicável, pois não há mudança HTTP/frontend proposta.

## Estado da implementação (2026-09-26)

ADRs 0043/0045/0046 registram as decisões. O caminho entregue fica desligado por
padrão e limitado ao `match-runner`: provider self-hosted TEI via endpoint
OpenAI-compatible, comparação por cosine, cache Redis L2 + LRU local bounded,
metadados resumidos em MatchRun e fallback fail-open. O shadow só consulta `review` por
`variant_semantic_uncertain`, após `MatchingEngine`. O modo `active` exige
limiar explícito, marca/modelo exatos, mesma condição de variante e ausência de
preço extremo recalculado; ainda assim deve permanecer desabilitado até dataset
e threshold serem validados em holdout.

O baseline reproduzível atual tem dez exemplos de aceitação/regressão, com
resultado perfeito no matcher atual, mas não é amostra representativa nem
compara embeddings. Consulte [embedding-acceptance-baseline.md](embedding-acceptance-baseline.md)
e o registro do [experimento local PENDING-024](../pending/resolved/PENDING-024-product-match-embeddings-shadow.md).

Migration `0031_match_embedding_evidence` foi aplicada ao PostgreSQL do Compose;
`alembic_version` aponta para essa revisão e a coluna `embedding_evidence` foi
confirmada como `json`. A mesma revisão foi sincronizada nos quatro containers
atuais e cada um reporta 0031 como head Alembic. Os processos continuam usando
as imagens anteriores ao código de embeddings, portanto a feature não está
rodando no Compose. Não houve chamada real ao provider: falta chave/modelo local
e dataset de pares revisado. Permanecem pendentes a avaliação shadow real, a
decisão operacional sobre transferência/retenção de títulos e atributos e o
smoke do processo atualizado.

