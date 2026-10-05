# PENDING-024 — Validar embeddings no Product Match com corpus rotulado e Run real

- Status: RESOLVED
- Tipo: TESTING
- Prioridade: P1
- Área: matching/embeddings
- Origem: 2026-09-24 — implementação após investigação arquitetural
- Atualizado: 2026-09-26
- Resolvida: 2026-09-26 por delimitação de escopo ao ambiente local.

## Contexto e mudança de bloqueio

A ausência de API key paga deixou de bloquear o experimento: foi integrado TEI
local com `intfloat/multilingual-e5-small`, sem credencial, em container
separado. O `.env` local está configurado com `MATCH_EMBEDDINGS_MODE=shadow` e
o perfil `embeddings`; o `.env` não é versionado. O modo padrão do repositório
continua `off`. A decisão arquitetural e a comparação de provedores estão na
[ADR 0045](../adr/0045-self-hosted-product-match-embeddings-provider.md).

O modo shadow não altera scores nem ofertas. `active` continua desabilitado:
amostras preliminares mostram sobreposição alta entre positivos e hard
negatives. A execução persistida e a evidência shadow foram confirmadas no
ambiente de desenvolvimento por `AUTH_REQUIRED=false` e principal fixo de
desenvolvimento; ownership permaneceu ativo. O usuário delimitou o uso a este
ambiente, sem produção. Assim, este trabalho se encerra como experimento local;
os dados não aprovam qualidade generalizável nem habilitação de `active`.

## Verificado

- `docker compose` valida a configuração; `embedding-service` sobe saudável,
  baixa os pesos uma vez para volume Docker persistente e responde no endpoint
  OpenAI-compatible `/v1/embeddings`. Serviço sem porta publicada no host e sem
  API key; worker usa `http://embedding-service:80/v1/embeddings`.
- CPU do host: 32 núcleos; memória do host: 15,57 GiB; sem GPU disponível para
  Compose. Serviço separado limitado a 2 CPUs/2 GiB. TEI ficou saudável em
  cerca de 49 s no primeiro start (42 s baixando ONNX); RAM em repouso observada
  entre 850 e 900 MiB; CPU em repouso observado entre 0,3% e 1,3%. Após
  stop/start com volume preenchido, voltou a `healthy` em 14 s e carregou pesos
  locais em ~267 ms.
  São medidas deste host.
- Vetor real conferido com 384 dimensões e sem `Authorization`. Batch de entrada
  do TEI foi limitado pelo backend a 8 itens; o adapter fragmenta batches até
  100 em chamadas de no máximo 8, mantendo ordem e uso de tokens.
- Mediana após warm-up (3 medições): batch 1: 8,33 ms; 10: 54,69 ms; 50:
  251,69 ms; 100: 501,29 ms. São tempos end-to-end do match-runner ao sidecar,
  não incluem o primeiro download.
- Pares de smoke sintéticos (similaridade cosseno; apenas diagnóstico):
  iPhone PT↔EN 0,947; CPU reordered 0,945; GPU reordered 0,953; hard negative
  Pro↔Pro Max 0,992; 5800X↔5800X3D 0,969; 256↔512 GB 0,980. A distribuição
  mostra que esses embeddings, sozinhos, não distinguem variantes; não escolher
  threshold nem habilitar `active` com estes dados.
- Sinais multilíngues adicionais (smoke sintético): acabamento Laranja
  Cósmico↔Cosmic Orange 0,889; placa de vídeo↔graphics card 0,859;
  desbloqueado↔unlocked 0,922; Laranja Cósmico↔Naranja Cósmico 0,988. São
  exemplos úteis de sinal cross-lingual, mas não validam classificação de SKU.
- Cache L1 local + Redis L2 real: primeiro runtime (miss) chamou TEI em 39 ms;
  com TEI parado, um segundo runtime isolado retornou `ok` do Redis em 6 ms com
  dois hits. As duas chaves tinham TTL de 21.518 s e vetores 384D; as chaves de
  smoke foram removidas depois da checagem. Redis guarda apenas hashes e vetores
  com TTL; títulos não são salvos. Erro Redis mantém fallback L1 (teste unitário).
- Indisponibilidade real validada parando o sidecar: provider retornou
  `transport_error`; evaluator produziu erro de evidência e `shadow` preservou
  decisão `review`. Serviço foi reiniciado e recuperou `healthy`.
- Concorrência 1/2/4 requests: 1/1, 2/2, 4/4 sucesso; p50 22,71/13,35/25,49
  ms. Sob estresse 8 simultâneas, 5/8 sucesso e 3 `429` (máximo 37,82 ms).
  Fluxo atual do worker é serial, logo esse estresse não representa sua
  concorrência operacional; 429 continua fail-open.
- Worker Compose reconstruído e configuração efetiva conferida: shadow,
  provider `openai_compatible`, dimensão 384, batch 8, API key vazia.
- A chave de cache inclui fingerprint do endpoint e `MATCH_EMBEDDINGS_CACHE_REVISION`;
  mudar de provedor ou atualizar pesos com o mesmo ID exige novo escopo. A URL
  não é registrada nem armazenada no cache.
- `MatchEmbeddingRuntime` instanciado pelas `Settings` efetivas do container
  gerou evidência `ok` pelo endpoint local: Ryzen positivo 0,968645, 43 ms,
  sem API key.
- MatchRun real via endpoint local em `development` (sem adicionar bypass novo):
  Ryzen 7 5800X3D, run `f55d824b-d919-4390-8ada-42707873c748`, concluída em
  260,4 s; 11 lojas registradas, 7 candidatos, 4 sem resultado e 0 erros.
  Dez lojas foram contabilizadas como concluídas, embora todas as 11 linhas de
  loja estivessem em estado terminal no detalhe. Essa divergência histórica foi
  corrigida no serviço de finalização e coberta por teste regressivo; o registro
  dessa Run permanece inalterado. ML e Shopee não aparecem na execução e seguem
  com `match_enabled=false`.
- MatchRun real elegível para embeddings: iPhone 15 Rosa, run
  `83b18121-1fa8-4460-b207-6c0dd285f3ea`, concluída em 326,3 s. A evidência
  foi persistida em cinco candidatos `review` da Nissei com motivo
  `variant_semantic_uncertain:color:rosa!=midnight`; todos registraram
  `status=ok`, modelo `intfloat/multilingual-e5-small`, similaridade `0.92875`
  e representação `hybrid`. A primeira inferência durou 346 ms; as quatro
  repetições foram hits de cache Redis (2 hits, 0 ms). A decisão permaneceu
  `review` nos cinco casos, confirmando que shadow não promove nem altera a
  decisão/oferta. Execução: 11 lojas, 3 candidatos encontrados, 8 sem
  resultado, 0 erros; ML e Shopee ausentes.
- A divergência do contador `stores_completed` foi reproduzida com teste
  regressivo e corrigida em `MatchRunService.finalize_completed`: a finalização
  agora considera também o número de lojas filhas em estado terminal. O teste
  passa e cobre a recuperação quando o worker envia a agregação defasada.
- Testes direcionados de provider/cache/fail-open/configuração, MatchRun e
  regressão de matching: **140 passed** em 1,23 s; dois avisos de depreciação
  em dependências. Ruff check/format, mypy dos quatro módulos e
  `git diff --check` passaram.
- Após a correção do escopo de cache por endpoint/revisão: **21 testes
  direcionados passed** em 0,44 s; Ruff check e format passaram.
- Suíte rápida completa (`pytest -m "not live and not slow"`): **1.019
  passed, 7 skipped** em 16,51 s; 4 avisos de depreciação de dependências.
  A primeira tentativa falhou ao ler o diretório temporário compartilhado do
  Windows; a repetição usou um `--basetemp` novo dentro do workspace e passou.

## Resolução e limites aceitos para o escopo local

- Execuções locais confirmaram MatchRun, persistência de evidência shadow,
  fallback e cache, sem mudança de decisões/ofertas. A regressão do contador de
  lojas foi corrigida e coberta por teste.
- A suíte rápida completa passou com **1.019 aprovados e 7 ignorados**; testes
  direcionados, Ruff, formatação, mypy e `git diff --check` também passaram.
- O corpus permanece pequeno e exploratório; não houve comparação de todas as
  representações, validação estatística, calibração nem medição de CPU/RAM de
  pico. Esses resultados não são uma aprovação para `active` ou para produção.
- Como o uso solicitado é somente local, não se exige ampliar o corpus nem
  executar benchmark operacional de produção para encerrar este experimento.
  Manter embeddings em `shadow`/`off`, `active` desabilitado e ML/Shopee
  desativadas. Se o escopo mudar para ativação ou produção, abrir nova validação
  com corpus rotulado independente, holdout preservado e benchmark adequado.

## Fontes e arquivos relacionados

- [Investigation e opções de providers](../matching/embeddings-architecture-investigation.md)
- [ADR 0043](../adr/0043-product-match-semantic-embedding-evidence.md)
- [ADR 0045](../adr/0045-self-hosted-product-match-embeddings-provider.md)
- [Corpus piloto](../../tests/fixtures/matching/embedding_live_corpus.jsonl)
- `src/scout_api/modules/matching/embedding_evidence.py`
- `src/scout_api/modules/matching/embedding_provider.py`
