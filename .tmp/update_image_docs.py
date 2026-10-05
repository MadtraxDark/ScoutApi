from pathlib import Path

path = Path('docs/persistence/product-images.md')
text = path.read_text(encoding='utf-8')
start = text.index('## Princípio')
end = text.index('## Conta dedicada')
text = text[:start] + '''## Princípio

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

''' + text[end:]
text = text.replace('Estado válido após cadastro: `original_status=ready` +\n`optimized_status=pending`.', 'Estado válido após importação: `original_status=pending` +\n`optimized_status=pending`. Depois da preservação: original `ready` + AVIF `processing`.')
text = text.replace('    display_url → ?variant=original  (pending | processing | failed)', '    display_url → ?variant=original, se original preservada\nelse if original_status in pending/downloading:\n    display_url → source_url aprovada')
start = text.index('## Pipeline detalhado')
end = text.index('## CRUD', start)
text = text[:start] + '''## Pipeline detalhado

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

''' + text[end:]
path.write_text(text, encoding='utf-8')

path = Path('docs/integration/pricescout.md')
with path.open('a', encoding='utf-8') as file:
    file.write('''
## Importação com referências pendentes (ADR 0048)

O frontend envia imagens aprovadas em `POST /products.images` (`source_url`,
`position`, `is_main`) e conclui após a resposta de persistência. Não encadeia
uploads individuais, não espera AVIF e não aumenta timeouts.
Detalhes e estados: [product-images.md](../persistence/product-images.md).
No detalhe, polling visível da galeria a cada 3 segundos só enquanto há estados
pendentes; atualiza mídia sem sobrescrever os campos de edição do usuário.
Falha posterior de imagem não transforma produto salvo em importação falha.
''')
