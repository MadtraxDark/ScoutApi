# iPhone 18 Pro 512GB Bordô — investigação de Product Match (2026-10-08)

Produto canônico `c4015a7a-4ff6-496b-be28-bba7451101d5`. A fonte de lojas é
`STORE_CONFIGS` e a interseção com os `StoreSearchAdapter`s registrados. Este
documento registra observações das SERPs/PDPs e dos `ProductMatchRun`s reais;
resultados de estoque e ranking podem mudar sem alteração de código.

## Identidade e política de comparação

A [Apple](https://support.apple.com/en-asia/108044) lista iPhone 18 Pro e Pro
Max separadamente, o Pro com 512GB e Burgundy entre as opções. O
[anúncio oficial](https://www.apple.com/newsroom/2026/09/apple-debuts-iphone-18-pro-and-iphone-18-pro-max/)
confirma 6,3 polegadas no Pro e 6,9 no Pro Max. A identidade extraída do
produto canônico é `brand=apple`, `model=iphone18pro`, `storage=512gb`,
`color=bordo` (normalizada para `burgundy`). A20 Pro, câmera de 48MP e tela são
evidências auxiliares: não substituem modelo, capacidade ou cor.

O matcher rejeita conflitos explícitos de geração, Pro/Pro Max, capacidade e
cor; dado ausente não equivale a conflito. `Bordô`, `Burgundy`, `Borgoña` e
`Burdeos` são aliases globais de `burgundy`. A cor da variante selecionada na
PDP prevalece sobre o título da SERP. `USED` e `OPEN_BOX` são rejeitados contra
a referência nova; `Renewed` conserva a identidade com condição comercial
rotulada, segundo [ADR 0050](../adr/0050-search-locale-and-offer-condition.md).
Oferta vinculada a operadora com referência sem informação de desbloqueio
permanece em `review`; moeda, país, seller e condição acompanham a oferta.
MPN/part number confiável igual é evidência forte; `A3472` é modelo regional e
não vira conflito automático com uma referência sem esse identificador.

## Registry e estratégia

As 11 lojas implementadas, habilitadas para Match e com adapter registrado
foram: AliExpress, Amazon Brasil, Amazon US, Best Buy, KaBuM!, Magazine Luiza,
Nissei, Pichau, Shopping China, TerabyteShop e Visão VIP. Mercado Livre e
Shopee têm integração, mas `match_enabled=False` por instabilidade de login;
eBay, GameStop, Newegg, Micro Center, Cellshop e Star Games não estão
implementadas. Não foram incluídas artificialmente no `MatchRun`.

A única `StoreListing` ativa anterior era KaBuM!: sua estratégia foi
`refresh_existing`, com `refresh_unchanged`. As demais lojas seguiram
`discover`. A URL conhecida da KaBuM! é
[esta PDP](https://www.kabum.com.br/produto/1072447/iphone-18-pro-apple-512gb-camera-de-48mp-a20-pro-tela-6-3-super-retina-xdr-bordo).

## Consultas e correções

Antes, o título longo normalizado ocupava a primeira consulta, seguido de
`celular/smartphone apple iphone 18 pro`, `apple iphone 18 pro`,
`iphone 18 pro` e `apple iphone 18 pro 512gb`. A combinação de modelo +
capacidade + cor aparecia além do budget de cinco consultas. O primeiro ajuste
a colocou na terceira posição; a ladder final põe
`apple iphone 18 pro 512gb bordo` (pt-BR) ou
`apple iphone 18 pro 512gb burgundy` (en-US) primeiro, depois contexto de
categoria e relaxamento progressivo de cor/capacidade. A regra se aplica a
smartphones com título natural longo, sem hardcode de modelo ou loja.

Na Best Buy, a SERP sem `intl=nosplash` respondeu HTTP 200 com a seleção
internacional de país. O parâmetro, já usado no PDP do projeto, retornou SERP
real. Essa seleção agora é classificada como `SEARCH_INCOMPLETE_RESPONSE` caso
volte a ocorrer, e links com/sem `/sku/{sku}` do mesmo BSIN são deduplicados.
Na Amazon US, `Screen Protector` entrou como candidato antes da PDP; a palavra
`protector` entrou no filtro genérico de acessórios.

## Presença externa (consulta em 2026-10-08)

- [Magazine Luiza própria](https://www.magazineluiza.com.br/apple-iphone-18-pro-512gb-bordo-63-48mp-ios-27-5g/p/242487500/te/18pr/): PDP exata, exibida como indisponível. Há também [anúncio marketplace iPlace](https://www.magazineluiza.com.br/iphone-18-pro-512gb-bordo-apple/p/begbk0ajgc/te/18pr/); seller é separado do storefront.
- [Nissei](https://nissei.com/br/apple-iphone-18-pro-mjq94ll-a-5g-dual-esim-512-gb-burgundy-we): PDP exata, part number `MJQ94LL/A`, variante selecionada `Borgoña`, preço observado US$ 1.690 na coleta.
- [Shopping China](https://www.shoppingchina.com.py/producto/celular-apple-iphone-18-pro-a3472-mjq94ll-a-512gb-burgundy-1114426): URL exata indexada externamente, mas abriu como 404 nesta investigação; disponibilidade atual não confirmada.
- [Best Buy AT&T](https://www.bestbuy.com/product/apple-iphone-18-pro-512gb-burgundy-at-t/JCQ6HRFT7V): PDP exata de modelo/capacidade/cor, com vínculo AT&T e indicação `Coming Soon`; não é oferta desbloqueada confirmada.
- Nas outras lojas elegíveis não foi confirmada externamente uma PDP exata e disponível nesta amostra. Isso não prova ausência de catálogo.

## Execuções e benchmark observacional

| Run | Código/observação | Duração total | KaBuM! | Nissei | Best Buy |
|---|---|---:|---|---|---|
| `86dc94aa-caac-484f-8bbe-5874bcba6f37` | baseline | 195.205 ms | refresh, 8 ms | no_match, 37.352 ms, 0 PDP | no_match, 5.938 ms, 0 SERP |
| `ed319396-969a-44d8-bcf7-525156fc97e6` | cor na 3ª query | 244.222 ms | refresh, 781 ms | no_match, 49.710 ms, 0 PDP | no_match, 19.035 ms, 0 SERP |
| `953ffe80-bee1-4fd9-92ed-8a19645b8a26` | consulta curta primeiro, `intl=nosplash` | 409.508 ms | refresh, 1.227 ms | review, 53.513 ms, 1 PDP | timeout, 180.060 ms, 12 candidatos/4 PDP |

Esses tempos são observações de rede real, sem controle de cache, posição na
fila do browser ou ranking das lojas. A terceira execução diagnosticou o alias
espanhol ausente na Nissei e a duplicação de URLs na Best Buy. Não se deve
atribuir a diferença de duração total somente ao gerador de queries.

## Matriz por loja — Run `953ffe80-bee1-4fd9-92ed-8a19645b8a26`

Todas as lojas `discover` usaram cinco queries, exceto Best Buy, interrompida
após quatro pelo tempo de loja. As queries pt-BR foram: `apple iphone 18 pro
512gb bordo`, `celular apple iphone 18 pro`, `apple iphone 18 pro 512gb`,
`iphone 18 pro 512gb`, `apple iphone 18 pro`. As lojas com locale en-US
usaram `burgundy` e `smartphone` nas duas primeiras, seguidas das mesmas
consultas sem cor. As contagens abaixo são candidatos de SERP capturados e
PDPs efetivamente avaliadas. `no_match` não indica falha de persistência.

| Loja | Estratégia; SERP/PDP | Etapa decisiva e resultado |
|---|---|---|
| KaBuM! | refresh; 0/1 listing conhecida | `refresh_unchanged` em 1.227 ms; listing e snapshot anteriores preservados. |
| AliExpress | discover; 25/0 | SERP capturada, mas nenhuma PDP passou o prefilter para a variante exata; PDP externa exata não confirmada. |
| Amazon BR | discover; 25/4 | O card `B0HJJLFGWD` trouxe título Bordô, mas a PDP entregue foi `B006ZARA98` Glacial; rejeição `variant_color_mismatch`, sem listing. Outros candidatos eram cores/capacidades/modelos diferentes. |
| Amazon US | discover; 25/1 | A PDP avaliada era uma película `Screen Protector`, rejeitada. O filtro genérico inglês foi corrigido após esta execução; PDP exata externa não confirmada. |
| Best Buy | discover; 12/4 | `intl=nosplash` recuperou SERP e PDP AT&T Burgundy. `review` por `carrier_variant_uncertain` (referência sem lock explícito), sem listing; quatro queries atingiram `STORE_WALL_TIMEOUT` de 180.060 ms. Links duplicados do mesmo BSIN foram corrigidos depois. |
| Magazine Luiza | discover; 21/4 | PDPs Prateado/Glacial carregaram e foram rejeitadas por cor. A própria PDP Bordô está indisponível e não apareceu nas cinco SERPs. Seller Magalu e `lojaiplace` ficaram distintos. Sem listing. |
| Nissei | discover; 25/1 | PDP exata Burgundy carregou com 512 GB e `MJQ94LL/A`, mas o seletor retornou `Borgoña`: `review` por alias de cor ausente, sem `StoreListing`. Alias corrigido depois desta execução. |
| Pichau | discover; 25/0 | SERPs capturadas, nenhum candidato da variante passou ao PDP; PDP externa exata não confirmada. |
| Shopping China | discover; 21/1 | SERP retornou Pro Glacier e Pro Max Burgundy; PDP Pro Glacier carregou e foi rejeitada por cor. URL Burgundy exata indexada externamente respondeu 404 na verificação. |
| TerabyteShop | discover; 25/0 | SERPs capturadas, nenhum candidato da variante passou ao PDP; PDP externa exata não confirmada. |
| Visão VIP | discover; 12/0 | SERPs capturadas, nenhum candidato da variante passou ao PDP; PDP externa exata não confirmada. |

Não houve `PERSISTENCE_FAILED` nessa execução: os candidatos corretos Nissei e
Best Buy chegaram a `review`, que não cria `StoreListing`; Magalu e Amazon BR
foram rejeitados por variante. O `matches_found=1` do Run inclui o candidato
Nissei em `review` e **não** comprova uma listing persistida. O registro da
Nissei tinha `matched_listing_id=NULL` na leitura do banco.

Uma quarta execução com alias espanhol, deduplicação Best Buy e filtro
`protector` foi iniciada como
`b9a832b1-bf43-4f81-bc8a-7f8d397de035`. A última leitura permitida do banco
mostrou `running`, 7/11 lojas concluídas, KaBuM! em `refresh_unchanged`,
Shopping China/Amazon BR/AliExpress/Visão VIP/Pichau/TerabyteShop em `no_match`
e Magalu em andamento. A consulta seguinte foi rejeitada pela revisão
automática da ferramenta por limite de uso; portanto o resultado final dessa
Run, inclusive a persistência Nissei e o tempo Best Buy, permanece **sem
verificação** neste relatório.
