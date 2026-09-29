# PENDING-020 — Product Match live smoke para monitores

- Status: RESOLVED
- Tipo: TESTING
- Prioridade: P2
- Área: matching/monitor
- Origem: 2026-09-23 — Product Match de monitores por código do fabricante
- Atualizado: 2026-09-25

## Contexto

A regra de código de modelo para monitores foi implementada e validada com
testes unitários. O `POST /match` integrado já foi executado sem persistência
em monitores reais publicados por Pichau e Terabyte, cobrindo código igual e
divergente. Falta um caso live com código ausente em um dos lados.

## Feito

- Identificação genérica de códigos de monitor e comparação completa/exata.
- Fallback quando o código não está disponível, com atributos técnicos de tela.
- Testes unitários cobrindo formatos de fabricantes, sufixo, código ausente e
  decisão conflitante.
- Busca gera consultas legíveis com marca/família e tamanho/frequência quando
  um monitor real não tem `monitor_model_code`; `tests/unit/test_matching_regression.py`
  cobre a regressão observada na PDP Samsung Odyssey G30 da Magalu.
- Tamanho de tela, resolução com dimensões explícitas e frequência são
  comparados após normalizar grafias equivalentes, sem colapsar valores
  diferentes; testes de igualdade e conflito cobrem esses gates.
- MatchingEngine validado com o título PDP real salvo de `Gigabyte GS24F14`.
- `POST /match` live, `persist=false`, ASUS TUF VG259Q5A: referência Pichau →
  Terabyte `auto_match` 0.99, razão `monitor_model_code_exact` para `VG259Q5A`,
  concluído em 18,1 s.
- `POST /match` live, `persist=false`, Gigabyte GS24F14: referência Terabyte →
  Pichau `auto_match` 0.99, razão `monitor_model_code_exact` para `GS24F14`,
  concluído em 2,8 s.
- Caso divergente, `POST /match` live `persist=false`, ASUS TUF VG259QM:
  referência Pichau → Terabyte, 64,2 s; candidatos com códigos `VG249QM5F` e
  `90LM09Q0-B011X1` foram rejeitados por `monitor_model_code_conflict`; nenhum
  match foi retornado. Os dois monitores são variantes reais distintas.
- As execuções não criaram `MatchRun`, produto ou listing e não persistiram
  dados. Os PDPs reais e os títulos/códigos estão descritos nas fontes públicas
  dos varejistas e no HTML local de GS24F14.
- PDPs de referência: [Pichau VG259Q5A](https://www.pichau.com.br/monitor-gamer-asus-tuf-gaming-vg259q5a-24-5-pol-fast-ips-fhd-0-3ms-200hz-elmb-sync-hdmi-dp-vg259q5a), [Terabyte GS24F14](https://www.terabyteshop.com.br/produto/41251/monitor-gamer-gigabyte-gs24f14-238-pol-full-hd-ips-144hz-1ms-104srgb-hdmidp) e [Pichau VG259QM](https://www.pichau.com.br/monitor-gamer-asus-tuf-vg259qm-24-5-pol-ips-fhd-1ms-280hz-g-sync-compativel-altura-ajustavel-hdmi-dp-vg259qm).
- Confirmação cruzada da variante igual: [Terabyte VG259Q5A](https://www.terabyteshop.com.br/produto/37202/monitor-gamer-asus-tuf-gaming-vg259q5a-245-pol-full-hd-200hz-fast-ips-03ms-99-srgb-freesync-premium-hdmidp).

## Falta

- Concluído: caso live sem código de modelo na referência encontrou candidato
  real no Terabyte e passou pela cascata normal sem rejeição automática.

## Por que não terminou

- A consulta de catálogo de 2026-09-25 mostrou que não havia produtos monitor
  cadastrados. Para não inserir dados artificiais, a validação foi feita com
  PDPs de monitores reais via `POST /match` e `persist=false`.
- O smoke cobre igualdade nos dois sentidos e conflito de código entre modelos
  parecidos. Ainda não há evidência live para o fallback quando um dos lados
  não fornece código.

## Tentativas live adicionais (2026-09-25)

- `POST /match`, `persist=false`, referência [Pichau Cepheus F24M](https://www.pichau.com.br/monitor-gamer-pichau-24-full-hd-cepheus-f24m-ips-1ms-144hz-hdmi-dp-pg-f24m-bl01) → TerabyteShop: 12,0 s; scrape retornou SKU/modelo `PG-F24M-BL01`, GTIN `7898663065213`, categoria monitor e disponibilidade esgotada; nenhum candidato nem erro. Descartado como cobertura do caso sem código, pois a identidade de referência contém código e não houve identidade candidata.
- `POST /match`, `persist=false`, referência [Terabyte Redragon Blackmagic](https://www.terabyteshop.com.br/produto/13924/monitor-gamer-redragon-blackmagic-rgb-27-pol-full-hd-144hz-1ms-hdmi-display-port) → KaBuM: 6,8 s; o código não aparece no título público, mas foi extraído de `json-ld.mpn` como `GM7FT27`; nenhum candidato nem erro. Descartado como cobertura do caso sem código porque o pipeline recuperou o modelo e não houve identidade candidata.
- Ambas as chamadas não criaram `MatchRun`, produto ou oferta. Resultado negativo é inconclusivo para matching quando a busca não retorna candidato; não foi contado como acerto ou erro do matcher.

## Investigação de referência sem código (2026-09-25)

- `POST /crawl` + `identity_from_price_item` na PDP [Magazine Luiza Samsung
  Odyssey G30](https://www.magazineluiza.com.br/monitor-gamer-samsung-odyssey-g30-24-led-full-hd-144hz-1ms-hdmi-dp-freesync-ajuste-altura/p/jh4abh84b2/in/mogm/)
  confirmou `category=monitor`, `model=Odyssey G30`,
  `monitor_model_code=null`, sem MPN e atributos `24\"`, FHD, 144 Hz e VA.
- `POST /match`, `persist=false`, tentou essa referência em TerabyteShop,
  Amazon Brasil e KaBuM; depois usou a PDP sem código da KaBuM como referência
  para Magalu. Todas as tentativas foram concluídas sem erro, mas sem candidatos;
  nenhum score foi produzido nessa etapa da investigação.
- Antes da correção, `build_search_queries` emitia como primeiras consultas
  `samsung odysseyg30` e `odysseyg30`, perdendo o espaço da família que aparece
  nos títulos públicos. O teste de regressão falhou com essa saída. Agora gera
  também `samsung odyssey g30` e `samsung odyssey g30 24\" 144hz`; a suíte
  `test_matching_regression.py` passou (92 testes). Em seguida, a descoberta
  e o matching desse candidato foram confirmados no smoke integrado abaixo.

## Smoke live de fallback concluído (2026-09-25)

- A primeira consulta legível descobriu a PDP Terabyte `25384` (Samsung
  Odyssey G30, código `LS24BG300ELMZD`) para a referência real da Magalu sem
  `monitor_model_code`.
- Os logs apontaram divergências apenas de notação nos gates: `24"`/`24`,
  `FHD (1920x1080)`/`1920 x 1080 pixels` e `144Hz`/`144 Hz`. Foram corrigidas
  normalizações específicas para monitor em `identity.py`, mantendo rejeição
  para tamanho diferente; a suíte de regressão terminou com 96 testes passando.
- `POST /match`, `persist=false`, `include_review=true`, Magalu → Terabyte:
  `auto_match` 0.97 em 1,7 s, quatro atributos de monitor concordantes
  (`screen_size`, `resolution`, `refresh_rate`, `panel`), sem erro, sem loja
  não correspondida e sem `canonical_product_id` (nenhuma persistência).
- O controle negativo live já registrado para ASUS VG259QM permaneceu coberto:
  códigos distintos foram rejeitados por `monitor_model_code_conflict`; testes
  unitários também continuam cobrindo tamanhos/resoluções/frequências
  divergentes.

## Impacto

A validação integrada cobre modelos reais com código igual, divergente e
ausente na referência, sem gravar dados. A PDP sem código foi pareada com o
candidato correto sem relaxar os conflitos explícitos de variante.

## Relacionado

- `src/scout_api/modules/crawler/utils/category_profiles/extra_parsers.py`
- `src/scout_api/modules/matching/identity.py`
- `src/scout_api/modules/matching/engine.py`
- `tests/unit/test_matching_regression.py`
- `data/_terabyte_live.html`
- `docs/crawler/product-identity.md`

## Pronto quando

- Product Match integrado executado em vários monitores reais.
- Resultados registrados para códigos iguais, diferentes e ausentes; ausência
  de código segue para a cascata normal sem rejeição automática pelo novo sinal.
- Sem aumento de falsos positivos entre modelos semelhantes.
