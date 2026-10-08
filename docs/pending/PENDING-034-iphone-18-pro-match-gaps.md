# PENDING-034 — Lacunas de discovery do iPhone 18 Pro 512GB Bordô

- Status: OPEN
- Tipo: INCOMPLETE
- Prioridade: P1
- Área: matching/search, crawler/amazon, crawler/magazineluiza, crawler/bestbuy
- Origem: 2026-10-08 — investigação real nas lojas elegíveis
- Atualizado: 2026-10-08

## Contexto

A investigação e matriz de evidências estão em
[`docs/matching/iphone-18-pro-investigation-2026-10-08.md`](../matching/iphone-18-pro-investigation-2026-10-08.md).
Os resultados de SERP/PDP abaixo são amostras do dia, não prova de catálogo
estável. Não classificar uma PDP ausente, um seletor de país ou um desvio de
variante como oferta indisponível fabricada.

## Feito

- Consulta compacta de modelo + capacidade + cor antes do limite de cinco
  queries; aliases multilíngues de Burgundy, inclusive `Borgoña`.
- Best Buy: `intl=nosplash` na SERP, seleção internacional classificada como
  resposta incompleta, deduplicação de links pelo BSIN.
- `Screen Protector` rejeitado como acessório antes da PDP.
- KaBuM! percorreu `refresh_existing`; todas as outras lojas elegíveis
  percorreram `discover` em Match Runs reais.
- A Run final `b9a832b1-bf43-4f81-bc8a-7f8d397de035` foi iniciada com as
  correções implantadas em Docker; sua conclusão ainda precisa ser lida do
  banco. A última observação foi `running`, 7/11 lojas concluídas.

## Falta

- Ler a conclusão da Run `b9a832b1-bf43-4f81-bc8a-7f8d397de035`, validar
  `matched_decision`, `matched_listing_id` e `OfferSnapshot` da Nissei,
  comparar o tempo/erro da Best Buy e atualizar a matriz e o benchmark.
- Amazon BR: entender por que card `B0HJJLFGWD` (título Bordô 512GB) acaba
  em PDP `B006ZARA98`/Glacial. Confirmar se a loja redireciona para variante
  selecionada diferente ou se o fetch/extrator escolhe ASIN errado; corrigir
  genericamente com evidência de selected ASIN e teste de fixture. O matcher
  rejeitou Glacial com segurança.
- Magazine Luiza: a PDP própria Bordô `242487500` está indisponível e o
  marketplace iPlace tem anúncio separado. Verificar se alguma oferta está
  ativa e por que as queries internas só devolvem outras cores. Preservar
  `store=magazineluiza` e o seller real, sem criar listing para PDP OOS.
- Best Buy: a SERP e PDP AT&T funcionam, mas a oferta fica em `review` por
  carrier lock. Medir se a deduplicação elimina `STORE_WALL_TIMEOUT` de 180 s;
  investigar alternativas nativas de menor custo de navegação dentro de C1,
  sem elevar capacidade ou usar proxy pago sem justificativa.
- Shopping China: confirmar se a URL Burgundy exata, indexada externamente
  mas 404 no acesso atual, voltou a estar disponível. O resultado Glacier
  visto no discovery deve seguir rejeitado por cor.

## Por que não terminou

As SERPs e PDPs têm ranking, disponibilidade e respostas de variante variáveis.
A correção segura exige reprodução da seleção de variante/ASIN e validação de
oferta atual antes de criar listing. O tempo da Best Buy é dominado por warmup
e navegação observados no browser real.

A [documentação oficial Amazon sobre famílias de variações](https://developer-docs.amazon.com/sp-api/docs/building-listings-management-workflows-guide)
explica que cada combinação de cor/capacidade pode ser um child ASIN distinto
sob uma PDP de família. Isso sustenta investigar o ASIN selecionado na página;
**não** comprova que a transição específica `B0HJJLFGWD` → `B006ZARA98`
tenha ocorrido por seleção de variante. A SERP e a PDP precisam ser capturadas
e comparadas em uma mesma sessão antes da correção.

Em 2026-10-08, a revisão automática da ferramenta recusou a leitura seguinte
do banco por limite de uso (nenhum comando foi executado) e informou nova
tentativa após 14h47. Isso impediu verificar a conclusão da Run final neste
turno; não constitui evidência de falha da aplicação.

## Impacto

O produto pode continuar sem oferta exata persistida em Magalu, Amazon BR,
Shopping China e Best Buy. A identidade foi preservada: cores divergentes e
ofertas com operadora não são auto-vinculadas.

## Relacionado

- [`docs/matching/README.md`](../matching/README.md)
- [`docs/crawler/stores/bestbuy.md`](../crawler/stores/bestbuy.md)
- [`docs/crawler/stores/magazineluiza.md`](../crawler/stores/magazineluiza.md)
- [`docs/crawler/stores/nissei.md`](../crawler/stores/nissei.md)
- ADR 0038, ADR 0039 e ADR 0050.

## Pronto quando

- A causa do desvio de ASIN/cor da Amazon estiver demonstrada e corrigida em
  dados reais sem aceitar a cor Glacial como Bordô.
- Magalu e Shopping China tiverem disponibilidade verificada por PDP atual;
  oferta ativa exata deverá ser descoberta e persistida, ou a ausência atual
  documentada como limitação de catálogo.
- Best Buy completar Run sem timeout recorrente, preservando carrier lock e
  sem raspagem duplicada por BSIN.
- Run final lida do banco, com Nissei/Best Buy e snapshot persistido
  explicitamente conferidos.
