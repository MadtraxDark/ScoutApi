# PENDING-033 — Fechar Product Match em Magalu e AliExpress

- Status: RESOLVED
- Tipo: INCOMPLETE
- Prioridade: P2
- Resolvido: 2026-10-08

## Causa

- **AliExpress:** a SERP SSR já traz `itemList` com título em cerca de 1 s via
  `curl_cffi`, mas o Match abria Camoufox oneshot e raspava os primeiros
  `/item/` sem título. Três PDPs de acessório consumiam o wall de 180 s.
- **Magalu:** o egress direto cai em Akamai sec-cpt. A resolução rodava duas
  vezes na mesma página e, depois do proxy que funciona, cada PDP repetia o
  direto condenado. Na Run original isso estourava `STORE_WALL_TIMEOUT`
  (~188 s / ~180 s) antes do `auto_match`.

## Correção

- Busca AliExpress HTTP-first; parser lê o `itemList` embutido e o reject de
  SERP descarta acessório/modelo divergente sem PDP.
- Magalu HTTP-first só aceita documento pronto; sec-cpt não espera
  `networkidle` e não repete o solver na mesma página.
- Depois de um bloqueio direto cujo proxy teve sucesso, o mesmo host pula o
  direto seguinte por alguns minutos. Proxy não começa se o prazo da loja não
  couber outra navegação.
- Reject de SERP de cooler usa a categoria da referência, então radiador
  divergente não vira scrape.

## Evidência

Match isolado das duas lojas (referência MSI MAG CoreLiquid A12 360 /
CLA12360), 2026-10-08:

- Magalu: `auto_match` 0.92 em 86,7 s (busca 76,9 s, PDP 9,8 s, 1 scrape,
  5 rejects de título). PDP `hdd64c95h3`, preço/Pix coletados.
- AliExpress: `no_match` em 4,4 s, 0 scrapes, busca válida só com acessórios
  e itens sem o A12.
- Nenhum `STORE_WALL_TIMEOUT`. Erros da Run: nenhum.
