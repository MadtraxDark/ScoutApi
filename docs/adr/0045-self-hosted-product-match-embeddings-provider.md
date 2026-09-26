# ADR-0045: Provider self-hosted para embeddings do Product Match

- Status: Accepted
- Data: 2026-09-26

## Contexto

O experimento de evidência semântica depende de chamadas OpenAI e exigia uma
chave, embora o produto priorize custo marginal de API zero e os títulos de
produtos sejam dados de terceiros. O worker também executa Product Match com
Camoufox, então importar pesos no processo do worker aumentaria sua memória e
competiria pelo mesmo ciclo de vida.

## Decisão

- Generalizar o cliente para endpoint completo `MATCH_EMBEDDINGS_API` com o
  contrato `POST /v1/embeddings` compatível com OpenAI. A chave é opcional para
  `openai_compatible`; sem chave, nenhum header `Authorization` é enviado.
- Oferecer Hugging Face Text Embeddings Inference (TEI) em serviço Docker
  separado e opt-in no perfil `embeddings`, com `intfloat/multilingual-e5-small`
  (384 dimensões) e volume persistente de modelo. Limitar o serviço a 2 CPUs e
  2 GiB; manter `MATCH_EMBEDDINGS_MODE=off` como default do projeto.
- Configuração do serviço local: `MATCH_EMBEDDINGS_PROVIDER=openai_compatible`,
  `MATCH_EMBEDDINGS_API=http://embedding-service:80/v1/embeddings`, modelo
  `intfloat/multilingual-e5-small`, prefixo de entrada `query: `, sem API key.
- Erro, timeout ou serviço indisponível permanece fail-open. `shadow` observa,
  mas não altera o resultado determinístico; `active` continua exigindo limiar
  calibrado e as travas da ADR 0043.
- Cache LRU/Redis é definido pela ADR 0046; não se adiciona vector store ou
  armazenamento durável.

## Alternativas

- APIs hospedadas gratuitas: rejeitadas para uso contínuo porque free tiers,
  créditos, cotas e retenção mudam; não atendem à garantia de custo recorrente
  zero nem à exigência de não depender de terceiro.
- Ollama com modelo de embeddings: candidato self-hosted válido, mas a
  compatibilidade OpenAI pode pedir uma chave fictícia no cliente padrão, em
  conflito com a regra do projeto de não inventar credencial; TEI expõe o
  endpoint diretamente e suporta batching.
- FastEmbed: leve e adequado em CPU, mas é biblioteca, não servidor HTTP
  OpenAI-compatible; exigiria construir e operar uma API adicional.
- Modelo carregado dentro da API/worker: rejeitado para preservar isolamento de
  memória e reduzir competição com Camoufox.

## Consequências

- Custo de API recorrente: R$ 0; continuam existindo consumo de CPU/RAM e
  download inicial dos pesos. O volume evita baixar os pesos a cada reinício.
- O modelo E5 é multilíngue (português, inglês e espanhol incluídos) e requer o
  prefixo `query: ` em similaridade simétrica. A adequação ao corpus ScoutApi
  ainda precisa ser validada; benchmark de modelo não substitui rótulos reais.
- Serviço e cache são locais; texto de produto não é enviado a provider
  hospedado pela configuração self-hosted.
- O modo segue desligado por padrão. A habilitação de `shadow` no `.env` local
  serve apenas ao smoke/bench deste ambiente e não habilita `active`.
- O experimento local confirmou cache-hit/miss e evidência em MatchRun shadow.
  O corpus é exploratório e não qualifica `active` nem produção; qualquer
  ampliação de escopo exige benchmark rotulado separado antes da habilitação.

## Fontes

- [TEI: Quick Tour](https://huggingface.co/docs/text-embeddings-inference/quick_tour)
- [TEI: modelos suportados](https://huggingface.co/docs/text-embeddings-inference/supported_models)
- [Modelo multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)
- [Código do TEI](https://github.com/huggingface/text-embeddings-inference)
