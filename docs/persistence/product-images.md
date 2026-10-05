# Imagens de produto — Google Drive + PostgreSQL

Documento canônico do lifecycle de imagens do catálogo.
Decisões: [ADR 0029](../adr/0029-google-drive-product-images.md) (storage) +
[ADR 0031](../adr/0031-durable-image-optimization-queue.md) (AVIF durável).
Integração FE: [pricescout.md](../integration/pricescout.md).

## Princípio

```text
Crawler encontra candidatas
  → PriceScout revisa / seleciona / ordena / define principal
  → ScoutApiV2 baixa só as aprovadas
  → original no Drive + metadata no Postgres
  → API retorna sucesso (optimized_status=pending é válido)
  → AVIF em background (fila PostgreSQL)
  → quando ready, display_url passa a preferir AVIF
```

**SALVAR PRODUTO ≠ CONVERTER AVIF.**  
O original torna a imagem utilizável. O AVIF só otimiza.

**EXTERNAL CANDIDATE IMAGE ≠ CATALOG PRODUCT IMAGE.**  
Upload no Drive só ocorre **após aprovação**.

## Dois caminhos

### SAVE PATH (bloqueante para o cadastro)

```text
Product
  → download seguro + validate
  → persist original (Drive)
  → ProductImage (original_status=ready, optimized_status=pending)
  → HTTP 200/201 sucesso
```

Métricas neste caminho: `product_save_ms`, `original_download_ms`,
`original_upload_ms`, `optimization_enqueue_ms`.

**`avif_conversion_ms` NÃO entra neste caminho.**

### BACKGROUND PATH (pós-processamento)

```text
original ready
  → claim (lease + SKIP LOCKED)
  → AVIF convert + upload
  → optimized_status=ready
```

Métricas: `avif_conversion_ms`, `avif_upload_ms`, `compression_ratio`.

Recovery: após restart, `pending` ou `processing` com lease expirado é
reclamado pelo worker.

## Conta dedicada

- Uma conta Google exclusiva do PriceScout.
- OAuth 2.0 administrativo (offline); usuários finais **não** autorizam Drive.
- Credenciais só no backend: `GOOGLE_DRIVE_CLIENT_ID`,
  `GOOGLE_DRIVE_CLIENT_SECRET`, `GOOGLE_DRIVE_REFRESH_TOKEN`,
  `GOOGLE_DRIVE_ROOT_FOLDER_ID`.
- Scope: `https://www.googleapis.com/auth/drive.file`.
- Bootstrap one-shot: `python scripts/google_drive_oauth_bootstrap.py`.
  - Preferir OAuth client tipo **Desktop app** no Google Cloud Console
    (APIs e serviços → Credenciais → Criar credenciais → ID do cliente OAuth
    → **Aplicativo para computador**). Atualize `GOOGLE_DRIVE_CLIENT_ID` /
    `GOOGLE_DRIVE_CLIENT_SECRET` no `.env` com esse cliente.
  - Se usar client **Web**: em "URIs de redirecionamento autorizados" adicione
    exatamente (barra final obrigatória):
    `http://127.0.0.1:8765/` e `http://localhost:8765/`
    (erro `400: redirect_uri_mismatch` = URI ausente, barra faltando, ou
    client_id errado — tipicamente o do login Supabase).
  - Não reutilizar o client_id do login Supabase/PriceScout.

Não use o Google OAuth do login Supabase do usuário para storage.

## Organização no Drive

```text
{ROOT_FOLDER}/
└── products/
    └── <product_uuid>/
        ├── original/
        │   └── <image_uuid>.<ext>
        └── optimized/
            └── <image_uuid>.avif
```

Identidade = UUID interno. Identificador de arquivo = `drive_file_id`.
O original **nunca** é apagado após AVIF.

## Schema `product_images`

PostgreSQL conhece: produto → imagens → file IDs original/AVIF, status,
hash, dimensões, `position`, `is_main`, e lease do job AVIF.

### Status

| Campo | Valores |
|---|---|
| `original_status` | `pending`, `downloading`, `ready`, `failed`, `deleting` |
| `optimized_status` | `pending`, `processing`, `ready`, `failed` |

Estado válido após cadastro: `original_status=ready` +
`optimized_status=pending`.

### Lease do job (ADR 0031)

| Campo | Função |
|---|---|
| `optimization_attempts` | Contador de claims |
| `optimization_next_attempt_at` | Quando o job fica elegível |
| `optimization_claimed_at` / `optimization_claim_expires_at` | Lease |
| `optimization_worker_id` | Worker que detém o lease |

## IMAGE DELIVERY

```text
if optimized_status == ready:
    display_url → ?variant=optimized  (AVIF)
else:
    display_url → ?variant=original   (pending | processing | failed)
```

- Listagem: `ProductView.primary_image_url` aplica a mesma regra na capa.
- Galeria / detalhe: usar `display_url` (não espalhar `if optimized_status`
  no FE).
- URLs de variante são **imutáveis por arquivo** — evita cache inconsistente
  quando o AVIF fica pronto.
- `GET .../content` sem `variant` = `auto` (prefer AVIF se ready).

### Auth no browser (`<img>`)

`GET .../content` aceita **Bearer** ou cookie HttpOnly `scout_access_token`
(`path=/`, definido em login/refresh). Tags `<img>` não enviam Bearer; o
cookie same-site cobre a entrega. APIs JSON continuam Bearer-only (sem
cookie) — ver [ADR 0035](../adr/0035-media-access-cookie.md).

**Nunca** colocar access token na query string. **Nunca** apontar o FE para
URLs `drive.google.com`.

### Disponibilidade e diagnóstico

`ProductImageView` e `ProductView` expõem `image_status` /
`primary_image_status`, `image_error_code` e `image_retryable`. Estados de
metadata: `ready`, `missing`, `processing`, `invalid_reference` e
`storage_error`. Um produto sem linha de imagem tem status `missing` e código
`image_not_configured`. Quando AVIF falha, a original continua `ready` e
`image_warning_code=conversion_failed`; o detalhe retornado é uma mensagem
segura, nunca a exceção interna.

Se o download do conteúdo falhar, o proxy responde JSON com `detail.code`,
`detail.image_status`, mensagem amigável e `detail.retryable`:

| `image_status` | Significado | Retry |
|---|---|---|
| `not_found` | Arquivo referenciado não existe mais | Não |
| `permission_denied` | Storage recusou acesso ao arquivo | Não |
| `temporarily_unavailable` | Timeout, quota/limite ou storage indisponível | Sim |
| `storage_error` | Falha sem classificação mais específica | Conforme resposta |
| `invalid_reference` | Linha marcada pronta sem ID de arquivo | Não |
| `processing` | Original ainda não pronta | Sim, com espera moderada |

O frontend que receber `onError` em `<img>` deve substituir a imagem por estado
visual acessível e, se precisar distinguir uma falha conhecida do backend,
consultar a resposta JSON do endpoint de conteúdo. Repetir apenas após ação do
usuário e somente se `retryable=true`; não fazer polling ou retry infinito.
Os logs de falha incluem `image_id`, produto, provider, código normalizado,
status HTTP e retryability, sem Drive file ID, URL assinada ou credenciais.

Falha de AVIF: original permanece; `optimized_status=failed`; retry via
`POST .../retry-optimization` (reusa original; não rebaixa URL).

## Pipeline detalhado

1. Validar URL (http/https) + SSRF (DNS→IP, redirects revalidados).
2. Download com limite de bytes/timeout/redirects.
3. Validar conteúdo real (Pillow), não só `Content-Type`.
4. SHA-256; dedup por `(product_id, sha256)`.
5. Upload original → `original_status=ready`.
6. Enfileirar AVIF (`optimized_status=pending` + `next_attempt_at`).
7. Responder sucesso do cadastro.
8. Worker claim → convert → upload AVIF → `ready`.

Atomicidade de originais no import: falha de **original** aborta a
transação do request (produto/imagens do batch não commitam). Falha de
**AVIF** é independente e nunca invalida o cadastro já commitado.

## CRUD

| Método | Path |
|---|---|
| GET | `/products/{id}/images` |
| POST | `/products/{id}/images` |
| PATCH | `/products/{id}/images` |
| DELETE | `/products/{id}/images/{image_id}` |
| POST | `/products/{id}/images/{image_id}/retry-optimization` |
| GET | `/products/{id}/images/{image_id}/content?variant=` |

Reorder / set main = só metadata. Delete = remove files conhecidos + linha;
“not found” no Drive é idempotente. Delete de produto limpa imagens conhecidas
antes do cascade (sem delete recursivo cego de pasta).

## Worker

- In-process: lifespan da API (`IMAGE_OPTIMIZATION_ENABLED=true`).
- Compose: serviço `image-optimizer`
  (`python -m scout_api.modules.images.worker`).
- Concorrência: `IMAGE_AVIF_MAX_CONCURRENCY` (ou
  `IMAGE_OPTIMIZATION_CONCURRENCY`).
- O `GoogleDriveClient` é compartilhado entre threads do pool AVIF; cada
  request Drive usa `httplib2.Http` próprio via `requestBuilder` (httplib2
  não é thread-safe — sem isso aparece `SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC`).

## Configuração

| Variável | Função |
|---|---|
| `GOOGLE_DRIVE_*` | Credenciais OAuth + pasta raiz |
| `IMAGE_MAX_BYTES` | Tamanho máximo do download |
| `IMAGE_MAX_DIMENSION` | Lado máximo (sem upscale) |
| `IMAGE_DOWNLOAD_TIMEOUT_SECONDS` | Timeout HTTP |
| `IMAGE_MAX_REDIRECTS` | Redirects permitidos |
| `IMAGE_MAX_PER_PRODUCT` | Cap por produto |
| `IMAGE_AVIF_QUALITY` | Qualidade Pillow AVIF |
| `IMAGE_AVIF_MAX_CONCURRENCY` | Conversões paralelas |
| `IMAGE_OPTIMIZATION_CONCURRENCY` | Alias de concorrência |
| `IMAGE_OPTIMIZATION_ENABLED` | Liga poller/worker |
| `IMAGE_OPTIMIZATION_SWEEP_INTERVAL_SECONDS` | Intervalo do poller |
| `IMAGE_OPTIMIZATION_BATCH_SIZE` | Claims por sweep |
| `IMAGE_OPTIMIZATION_LEASE_SECONDS` | TTL do lease |
| `IMAGE_MEDIA_CACHE_MAX_AGE_SECONDS` | Cache-Control do proxy |

## SSRF

Rejeitar localhost, loopback, redes privadas, link-local, metadata e
redirects que apontem para esses destinos.

## Shared Drive (futuro)

Se a conta passar a usar **Google Workspace Shared Drive** verdadeiro,
avaliar Service Account + Shared Drive. Enquanto for My Drive de conta
comum dedicada, permanece OAuth offline.
