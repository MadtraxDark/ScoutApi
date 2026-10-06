# ADR 0040: Canonicalização multilíngue de atributos no Product Match

- Status: Accepted
- Data: 2026-09-23

## Contexto

O Product Match já protegia variantes explícitas, mas a comparação de cor usava
aliases pontuais e comparava compostos de forma textual. No MatchRun de
`Apple iPhone 17 Pro (256 GB) - Laranja cósmico`, os candidatos eram rejeitados
com `variant_color_mismatch:laranja cosmico!=cosmic orange` antes do score de
modelo, capacidade e condição. A interface do PriceScout exibia score zero,
mas o endpoint de detalhes da Run preservava a razão por candidato.

O mesmo título continha evidências semanticamente distintas: `Pro Max` era uma
variante incompatível, `Refurbished` era uma condição comercial incompatível, e
`A3256` / `MG7L4LL/A` eram identificadores adicionais.

## Problema / decisão necessária

Como reduzir falsos negativos entre idiomas sem traduzir títulos inteiros,
afrouxar thresholds ou permitir que sinais textuais escondam diferenças de
variante e condição?

## Alternativas consideradas

- Embeddings multilíngues como decisor: rejeitados por custo operacional,
  latência e menor explicabilidade; sem pares rotulados locais para calibrar
  variantes próximas.
- Tradução automática de títulos: rejeitada como identidade, pois pode
  inventar ou apagar detalhes de SKU, condição e conectividade.
- Fuzzy score global/threshold menor: rejeitado porque Pro/Pro Max e
  capacidades diferentes poderiam passar por alta sobreposição lexical.
- Vocabulário tipado de atributos com aliases semânticos determinísticos:
  adotado por custo previsível, explicabilidade e compatibilidade com os gates
  críticos existentes.

## Decisão

1. A recuperação de candidatos continua ampla e independente da validação.
2. Valores estruturados de atributo são normalizados por chave semântica. Cor
   preserva matiz e qualificadores conhecidos, normaliza idioma e ordem e só
   considera qualificadores ausentes compatíveis quando o matiz explícito
   coincide. Assim `Laranja cósmico`, `Cosmic Orange` e `Orange` podem concordar
   no matiz; azul continua incompatível com laranja.
3. O vocabulário é organizado por conceitos reutilizáveis e aliases PT/EN/ES;
   novos idiomas e atributos são adicionados ao vocabulário do atributo, não a
   exceções por produto. A normalização lexical auxilia score, mas não substitui
   a validação tipada.
4. Se duas cores explícitas não puderem ser comparadas pelo vocabulário, a
   diferença vira `review` (`variant_semantic_uncertain`), não rejeição nem
   `auto_match` sustentado apenas por marca/modelo. Uma tradução conhecida de
   outra língua não passa a ser conflito lexical; permanece incerteza até o
   vocabulário cobri-la.
5. `network_lock` é um atributo explícito: `Unlocked` e `Desbloqueado` são
   equivalentes; `Locked` e `Bloqueado` entram em conflito quando ambos estão
   presentes. Ausência permanece desconhecida.
6. Capacidade e assinatura de modelo continuam bloqueadores anteriores ao
   score; `Pro` e `Pro Max` permanecem modelos distintos.
7. `renewed`, `refurbished`, `reconditioned`, `used`, `open box` e aliases
   localizados continuam condição comercial, nunca stopwords.
   **Atualização (ADR 0050):** Renewed/Refurbished podem representar o mesmo
   produto canônico e **não** rejeitam automaticamente contra referência nova;
   USED/OPEN_BOX preservam rejeição. A condição é persistida e exibida.
8. Part numbers com estrutura alfanumérica e sufixo regional `/A` são extraídos
   como MPN e só fortalecem identidade quando iguais nos dois lados. Códigos
   `A####` são extraídos como model number apenas em contexto de telefone. A
   ausência de identificador no lado de referência não é conflito; atributos
   críticos ainda são verificados primeiro.
9. Embeddings podem ser reavaliados para geração/reranking de candidatos se
   métricas de recall e precisão justificarem; não podem sobrepor bloqueadores
   determinísticos.

## Justificativa

A abordagem mantém o custo por comparação próximo de uma busca de dicionário,
registra a razão por atributo e evita um modelo estatístico sem conjunto
rotulado. A pesquisa considerou benchmarks de entity matching, blocking,
transformers multilíngues, recursos de locale e implementações comunitárias.
O benchmark WDC documenta atributos esparsos e avaliação em muitos sites;
Splink recomenda blocking para limitar pares; estudos multilíngues mostram
benefício de transformers depois de treinamento específico. Ditto e Zingg
trazem stacks e necessidades de treino/escala acima do caso atual. Relatos de
fórum foram usados somente como evidência complementar.

Fontes de referência: [WDC Products](https://webdatacommons.org/largescaleproductcorpus/v2/),
[Splink blocking](https://moj-analytical-services.github.io/splink/topic_guides/blocking/blocking_rules.html),
[Ditto](https://github.com/megagonlabs/ditto),
[benchmark multilíngue](https://arxiv.org/abs/2205.15712),
[documentação CLDR](https://cldr.unicode.org/),
[Apple Support: números de modelo do iPhone](https://support.apple.com/en-gb/108044).

## Consequências positivas

- Diferenças conhecidas de idioma/ordem em atributos não causam rejeição
  automática.
- Modelo, capacidade, cor incompatível, lock explícito e condição continuam
  sendo evidências independentes e explicáveis.
- Identificadores adicionais são aproveitados sem assumir que códigos
  regionais ausentes sejam divergência.

## Trade-offs / consequências negativas

- O vocabulário determinístico exige manutenção conforme novas lojas e idiomas
  aparecem; termos desconhecidos permanecem sem normalização semântica.
- Cores explicitamente desconhecidas passam para revisão, o que aumenta o
  volume de revisão manual até que aliases confiáveis sejam adicionados.
- Igualdade de matiz quando um acabamento está ausente pode aceitar diferenças
  de nuance comercial da mesma cor. O matcher ainda bloqueia matizes distintos;
  códigos exatos e revisão de pares próximos seguem úteis para calibrar maior
  granularidade.
- Não há cobertura de tradução aberta para qualquer idioma ou escrita. Embedding
  multilíngue permanece uma opção futura, condicionada a benchmark e avaliação
  de falsos positivos.
