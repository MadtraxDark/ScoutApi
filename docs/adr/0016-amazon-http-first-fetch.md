# ADR 0016: Amazon HTTP-first progressive fetch

- Status: Accepted
- Data: 2026-09-12

## Contexto

A coleta Amazon (BR/US) no ScoutApiV2 usava Camoufox (browser) como caminho
padrão, com proxy pago apenas após `UPSTREAM_BLOCKED` (ADR 0014). Evidência
live mostrou que:

1. HTTP direto com headers de locale (`Accept-Language` pt-BR / en-US) frequentemente
   entrega o PDP completo nos EUA, incluindo Buy Box, sem browser e sem proxy.
2. Camoufox costuma acionar robot-check/CAPTCHA mais cedo que HTTP simples — o
   proxy então “compensava” a estratégia de fetch, não a ausência de dados
   estruturados.
3. No BR, o HTML estático às vezes chega sem widgets de Buy Box (só AOD /
   “outras ofertas”) ou com HTTP 500 intermitente; isso é estado de página /
   oferta, não prova de que proxy seja requisito estrutural do parser.
4. APIs oficiais (Creators API / ex-PA-API) exigem Associates + elegibilidade
   (~10 vendas/30d) e não devem ser integradas sem aprovação (ADR 0015).

## Problema / decisão necessária

Como reduzir browser/proxy na Amazon sem sacrificar a precisão da Buy Box e
sem contornar CAPTCHA?

## Alternativas consideradas

- **A — Manter só Camoufox + proxy FALLBACK:** alto custo; proxy mascara
  detecção do browser.
- **B — HTTP-first só para `/crawl/offer`:** inconsistente com details/images
  que leem a mesma PDP.
- **C — HTTP-first para hosts Amazon; browser (+ proxy policy) só se HTTP
  falhar, challenge, PDP inválida ou Buy Box ausente sem OOS claro.**
- **D — Integrar Creators API:** credenciais / ToS; fora de escopo sem
  autorização.
- **E — TLS impersonation (`curl_cffi`) agressiva:** risco de ser tratado como
  falsificação de fingerprint; não adotar sem pedido explícito.
  *(Atualização 2026-09-19: Mercado Livre adotou `curl_cffi` HTTP-first sob
  pedido explícito — ver [ADR 0025](0025-mercadolivre-curl-cffi-http-first.md).
  Amazon permanece em urllib HTTP-first.)*

## Decisão

Adotar **C**:

1. `AmazonHttpFirstHtmlFetcher` envolve o `StoreAwareHtmlFetcher` quando
   Camoufox está habilitado.
2. Ordem para `amazon.com` / `amazon.com.br`:
   **HTTP (urllib + cookies de sessão + locale) → browser direto → proxy só
   após `UPSTREAM_BLOCKED`**.
3. HTTP é aceito quando a resposta é um PDP reconhecível. Se a Buy Box estiver
   ausente e não houver OOS claro, **repete HTTP uma vez** (~1.25s) — o BR
   frequentemente omite widgets na primeira resposta. Retry ainda sem Buy Box
   e sem OOS explícito, ou retry com falha, escala para browser direto (correção
   validada em 2026-10-05). Challenge, HTTP error ou página não-PDP também
   escalam para browser. Parser continua fail-closed
   (`MissingPriceError`) se ainda não houver Buy Box.
4. Spiders Amazon normalizam a URL de fetch para `https://www.{host}/dp/{ASIN}`
   (`prepare_fetch_url`), reduzindo ruído de tracking.
5. Parser continua Buy-Box-only; nunca usa preço de “outras ofertas” / AOD
   ingress como `price`.
6. Caminho de produção pretendido para Offer = **HTTP-first**. Camoufox/proxy
   Amazon permanece fallback operacional.

> **Nota (2026-09-12):** a frase anterior “sem CAPTCHA bypass” nesta ADR foi
> **supersedida** por [ADR 0017](0017-captcha-challenge-resolution.md): challenge
> deve ser resolvido; HTTP-first continua válido para *evitar* o challenge.

## Justificativa

- Alinha Proxy Cost Mode: proxy deixa de ser pré-requisito estrutural.
- `/crawl/offer` fica leve quando o HTML público já traz a Buy Box.
- Preserva Camoufox/proxy como fallback operacional.
- Challenge/CAPTCHA no fallback: ver ADR 0017 (resolução obrigatória).

## Consequências positivas

- Menos tráfego pago e menos cold-start de browser no caminho feliz US.
- Métricas `fetch_strategy=http-direct` distinguem sucesso sem browser.
- Testabilidade: fixture + mocks do wrapper HTTP-first.

## Trade-offs / consequências negativas

- BR live ainda pode exigir browser ou falhar (Buy Box vazia / 500) — documentado
  como limitação / pendência de confiabilidade, não como “precisa proxy”.
- Duas camadas de fetch aumentam complexidade de wiring (`build_html_fetcher`).
