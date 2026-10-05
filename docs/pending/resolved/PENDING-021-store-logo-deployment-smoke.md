# PENDING-021 — Validar o fluxo de logos no deploy HTTPS

- Status: RESOLVED
- Tipo: TESTING
- Prioridade: P1
- Área: matching/store-admin
- Atualizado: 2026-09-26
- Resolvida: 2026-09-26 por delimitação de escopo ao ambiente local.

## Contexto

O pipeline de upload, storage no Drive, fila de otimização, entrega de AVIF e atualização visual está implementado. Os critérios locais foram exercitados com eBay como loja descartável, sem substituir logos existentes.

## Concluído localmente

- Antes do teste, eBay não tinha linha em `store_metadata`, logo nem arquivos no Drive. A quota do Drive, que causou falha numa tentativa anterior, foi corrigida; upload posterior passou.
- POST real de PNG temporário em `/admin/stores/ebay/logo` respondeu 200 e persistiu o original no Drive. O download do original correspondeu byte a byte ao payload.
- O worker concluiu o job e a metadata chegou a `ready`. A API retornou URL versionada; GET da mídia respondeu 200 `image/avif` (3.403 bytes) com ETag contendo a versão.
- Reiniciei apenas API e `image-optimizer`, sem ProductMatchRun ativo. A API voltou saudável (health 200, banco `ok`), ambos executaram `alembic_upgrade_head` e o worker iniciou. A linha, a versão e os file IDs sobreviveram ao restart; GET da AVIF continuou 200.
- No PriceScout local (`http://localhost:3000/admin?view=stores`), após refresh, a lista carregou e a imagem de teste do eBay ficou visível. O log da API confirmou GET da URL versionada `...-avif` com 200.
- Cleanup condicionado à versão `488964e2-03cc-420a-be4a-3070a10f69d5` removeu apenas os dois IDs criados pelo teste: original `1hyqC22mgvPP_dxojlq9QC-WD6WfOU9WV` e AVIF `15TzY9nFzZqss7kIZkxFt6RFjTmdhLJr4`. A consulta individual de ambos retornou 404; a linha eBay foi removida e `GET /stores` voltou sem `logo_url`. Nenhum arquivo preexistente foi apagado.
- Upload + worker levou cerca de 14 s; restart até health levou cerca de 34 s. Não houve regressão de duração identificada.

## HTTPS de deploy — investigação e bloqueio

Busca em `.env`, `.env.example`, `compose.yaml`, arquivos de deploy/workflows e configurações/documentação do ScoutApiV2 e do PriceScout não encontrou domínio público. Os valores de origem ativos são locais: API (`CORS_ALLOWED_ORIGINS`, `AUTH_FRONTEND_SUCCESS_URL` e `AUTH_GOOGLE_REDIRECT_URL`) usa `localhost`; PriceScout (`NEXT_PUBLIC_API_BASE_URL`) aponta para `http://localhost:8000`. O README do frontend contém somente o placeholder `https://api.seu-dominio.example`. Compose não executa o frontend.

O usuário confirmou que o sistema ficará apenas neste ambiente, sem deploy de
produção. Portanto, o smoke HTTPS público não faz parte do escopo. O fluxo local
foi validado de ponta a ponta, incluindo autenticação no contexto local, mídia
AVIF e visualização no PriceScout local. Nenhum hostname foi presumido.

## Pronto quando

Critérios do escopo local concluídos. Uma eventual implantação futura exige uma
nova validação de HTTPS, autenticação e carregamento da logo nos hostnames reais.

## Relacionado

- `alembic/versions/0030_store_logo_media.py`
- `docs/adr/0042-store-logo-media-processing.md`
- `docs/persistence/product-images.md`
- `tests/unit/test_store_metadata.py`
- `tests/unit/test_store_logo_worker.py`
