# Imagens de produto — Google Drive + PostgreSQL

Documento canônico do lifecycle de imagens do catálogo.
Decisões: [ADR 0029](../adr/0029-google-drive-product-images.md) (storage) +
[ADR 0031](../adr/0031-durable-image-optimization-queue.md) (AVIF durável).
Integração FE: [pricescout.md](../integration/pricescout.md).

## Princípio

Decisão vigente para importação: [ADR 0048](../adr/0048-import-image-references-before-original-preservation.md).

```text
Crawler encontra candidatas → PriceScout aprova
  → POST /products com images
  → produto + referências + jobs pending no PostgreSQL
  → commit → resposta HTTP
  → worker baixa/valida/hash → original no Drive → checkpoint
  → worker converte AVIF → AVIF no Drive → ready
```

Salvar produto não aguarda preservação original nem otimização.
Upload só ocorre após aprovação. Preview `/crawl` não cadastra imagens.

## Dois caminhos

### Importação (síncrono)

Identidade, listing/oferta, referências de imagens e criação de jobs na mesma
transação. `register_saved` commita antes de retornar sucesso.
`register_references` não usa downloader, Drive, Pillow ou optimizer.
Logs: `product_import_stage` (`image_references`, `product_view`) e
`product_import_saved` (`registration_ms`, `commit_ms`, `images`).

### Background

O worker PostgreSQL existente reclama imagens `pending` e originais pendentes,
preserva original e commita antes da AVIF. Leases expirados permitem recovery.
`image_original_ready` registra download/upload/enqueue em ms no texto do log;
`avif_ready` registra conversão/upload. O request não aguarda nenhum deles.

Adição individual em `POST /products/{id}/images` mantém o contrato anterior
de original síncrono; PriceScout não usa esse endpoint durante a importação.

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

### Recuperar `storage_credentials_invalid`

Se imagens e logos retornarem `502` e o refresh OAuth responder `invalid_grant`
(token expirado ou revogado), renove o consentimento da **conta dedicada**:

```powershell
make renew-drive-token
```

O modo `--write-env` grava o novo token diretamente no `.env`, sem exibir
credenciais, e valida/preserva a pasta já configurada. Não basta `restart`:
o ambiente dos containers só muda ao recriá-los. Verifique a leitura das
imagens existentes e dos logos; um upload que falhou antes de persistir a
imagem precisa ser reenviado pela seleção aprovada no frontend.

No Google Cloud, confira o estado de publicação do app OAuth dedicado.
Apps externos em **Testing** emitem refresh tokens de sete dias para o scope
Drive. Para uso contínuo, configure o estado apropriado antes de gerar um
novo token. Expiração e revogação exigem novo consentimento; retries ou
relogin no PriceScout não restauram essa autorização.
Fonte: [expiração OAuth do Google](https://developers.google.com/identity/protocols/oauth2#expiration).

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

Estado válido após importação: `original_status=pending` +
`optimized_status=pending`. Depois da preservação: original `ready` + AVIF `processing`.

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

1. Request registra URLs aprovadas, posição/principal e jobs `pending`; commit.
2. Worker valida URL/SSRF (DNS→IP, redirects revalidados), baixa com limites,
   valida conteúdo com Pillow, calcula SHA-256 e reutiliza original por hash.
3. Upload original com nome UUID; original `ready` é checkpoint persistido.
4. Conversão e upload AVIF; `display_url` passa a preferir AVIF quando `ready`.
5. Original não é apagada pela conversão. Erros de imagem não desfazem produto.

`GET /products/{id}/import-status`: `saved`, `images_registered`, `pending`,
`processing`, `ready`, `error`. Exige `products:read` e ownership; galeria também.
`POST .../retry-optimization` reagenda original com falha, ou apenas AVIF quando
original já está pronta. Jobs pendentes sobrevivem a restart e worker ausente.
Falhas terminais precisam de retry explícito; não há retry automático infinito.

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
