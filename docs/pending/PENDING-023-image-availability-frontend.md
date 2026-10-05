# PENDING-023 — Estados de disponibilidade de imagem no PriceScout

- Status: IN_PROGRESS
- Tipo: INCOMPLETE
- Prioridade: P1
- Área: frontend/images
- Origem: 2026-09-29 — diagnóstico de imagens de catálogo
- Atualizado: 2026-09-29

## Contexto

O contrato backend informa disponibilidade em `ProductView` e `ProductImageView`, e o proxy de conteúdo classifica falhas conhecidas. O frontend está em checkout separado (`PriceScout`, informado pelo usuário). A validação em navegador observou a API ativa retornando 500 durante o refresh OAuth do Google: `invalid_grant` (token expirado ou revogado). A exceção escapava da versão em execução, e o navegador também bloqueava o diagnóstico por CORS.

## Feito

- Contrato backend com estado, código e retryability.
- Erros do proxy normalizados para causas storage genéricas.
- Documentação do fluxo e critérios para `onError` e retry manual.
- Logos de loja normalizam ausência e falhas conhecidas do Drive com contrato estruturado e logs sanitizados.
- PriceScout agora usa mensagens por estado, captura `onError`, reconsulta o endpoint de conteúdo pela camada central da API, oferece retry manual apenas para falhas recuperáveis e indica falha de otimização com a original mantida.
- Validação visual em listagem, detalhe e lojas: dimensões dos cards e linhas permanecem estáveis; erros de mídia viram estados acessíveis.

## Falta

- Corrigir o refresh token OAuth expirado/revogado na configuração operacional do backend; credenciais não devem ser registradas no repositório nem enviadas pelo frontend.
- Revalidar imagem e retry no navegador após a API atualizada responder com o contrato estruturado e CORS esperado.
- O teste backend `pytest.exe` foi bloqueado pelo Windows Application Control.

## Por que não terminou

O Docker estava executando imagens antigas de `api` e `image-optimizer` sem a revisão Alembic `0031_match_embedding_evidence`, embora o PostgreSQL do Compose já estivesse marcado nessa revisão. A migração original foi recuperada do branch local `origin/embedding` e adicionada ao checkout como `0031_match_candidate_embedding_evidence.py`, encadeada a `0030_store_logo_media`. As duas imagens foram reconstruídas e os serviços recriados sem reiniciar PostgreSQL/Redis nem apagar o volume; API está saudável e ambos os serviços registraram `alembic_upgrade_head: done`. O `match-runner` e o `monitor` existentes também contêm a revisão 0031. O bloqueio Docker/Alembic está resolvido; o token OAuth do Google Drive segue expirado/revogado e precisa ser reautorizado no ambiente seguro de operação. Os testes frontend passaram com `tsx` direto do cache (um shim temporário resolveu a falha `os.userInfo()`/ENOMEM do Node 26 no Windows); `pytest.exe` continua bloqueado pelo Windows Application Control. Typecheck (com incremental off), lint direcionado e Ruff passaram; lint completo ainda acusa violação preexistente de `react-hooks/set-state-in-effect` em `app/admin/page.tsx:222`.

## Impacto

O backend está ativo com a revisão Alembic reconciliada. Os downloads do Drive seguem dependendo da reautorização do refresh token, que foi recusado por estar expirado ou revogado; depois disso, revalidar a imagem e os estados no frontend.

## Relacionado

- `src/scout_api/modules/images/`
- `src/scout_api/modules/matching/router.py` (`GET /stores/{store_key}/logo`)
- `utils/api/product-images.ts`
- `components/ImageViewer.tsx`
- `docs/persistence/product-images.md`
- `docs/integration/pricescout.md`

## Pronto quando

O PriceScout mostrar ausência, processamento, arquivo removido, storage temporariamente indisponível, acesso negado, referência inválida, conversão AVIF falha e erro do navegador; retry manual recuperável funcionar; credencial OAuth for reautorizada; testes passarem e as telas reais forem revalidadas contra a API atualizada. O bloqueio Docker/Alembic foi resolvido e não é mais critério pendente.



