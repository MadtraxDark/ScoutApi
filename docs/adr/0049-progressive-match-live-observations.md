# ADR 0049: Ofertas progressivas com observações duráveis de Match Runs

- Status: Accepted
- Data: 2026-10-05
- Complementa ADR 0036; mantém a Run independente do frontend.

## Contexto

O worker grava outcomes por loja antes de finalizar a busca, mas o PriceScout
esperava o catálogo final. Os outcomes anteriores não distinguiam `review`
de `auto_match` nem preservavam o hit para recuperação de outra attempt.

## Problema / decisão necessária

Mostrar ofertas assim que uma loja termina, preservando ownership,
recuperação do job e a autoridade do catálogo final.

## Alternativas consideradas

- Snapshot leve com polling e staging dos hits selecionados.
- Persistir `StoreListing` progressivamente em cada outcome.
- SSE/WebSocket como canal adicional de observação.

## Decisão

Usar `GET /match-runs/{run_id}/live` privado, com polling visível a cada
quatro segundos. Persistir decisão, hit sanitizado e referência em PostgreSQL.
Registrar roster antes da busca e estado `running` quando cada loja inicia.
Após reclaim, reidratar hits terminais e repetir somente lojas não terminais.

Fencing usa `worker_id` e número da attempt com lock curto. O lock final
precede a persistência canônica; catálogo, links `matched_listing_id` e estado
terminal pertencem à mesma transação. Falha na gravação de outcome interrompe
a execução, preservando os outcomes já commitados.

O read model projeta campos comerciais e identidade, sem carregar candidates,
payload de recuperação ou referência. FX reutiliza uma leitura de cotações;
preço e moeda originais continuam autoritativos. Somente `auto_match` gera
card parcial; review fica no progresso e, no catálogo, em seção de revisão.

## Justificativa

A latência percebida depende da primeira loja, sem antecipar decisões de
catálogo ou alterar scraper, scheduler de browser ou consenso GTIN. Polling
usa a infraestrutura de requests e o escopo de rate limiting existentes.

## Consequências positivas

- Resultados sobrevivem a reload, navegação e recuperação do worker.
- Handoff identifica a mesma oferta por listing, identidade comercial ou URL.
- Atualização terminal altera ofertas sem sobrescrever o formulário em edição.
- Histórico completo continua disponível por endpoint separado.

## Trade-offs / consequências negativas

- A oferta pode demorar um ciclo de polling mais rede/renderização para aparecer.
- Staging aumenta armazenamento; tem versão e limite de 64 KiB por observação.
- Observações anteriores à migration não têm decisão/contexto: não viram cards
  confirmados; reclaim de hit legado falha explicitamente como
  `MATCH_RECOVERY_INCOMPLETE`, sem fabricar catálogo incompleto.
- Falha terminal mantém cards como observações não confirmadas no catálogo.
- Persistência progressiva e SSE ficam adiados; não há benefício demonstrado
  que justifique alterar a semântica final ou introduzir outro canal.

Contrato e operação: [integração PriceScout](../integration/pricescout.md).
