# ADR-0048: Importação confirma referências antes de preservar originais

- Status: Accepted
- Data: 2026-10-05

## Contexto

O PriceScout aguardava uploads individuais depois de `POST /products`.
`ImagePipeline._persist_one` baixava, validava, calculava hash e enviava
o original ao Drive dentro de cada request. Só AVIF estava desacoplada.
Uma importação real permaneceu nessa sequência por 82,46 segundos após
o produto já estar salvo. ScoutApi antigo registrava referências primeiro.

## Problema / decisão necessária

Concluir o cadastro após persistência obrigatória, preservando jobs e recovery.
Esta decisão substitui o requisito de original pronto no caminho síncrono
de importação dos ADRs 0029/0031; mantém storage, leases e entrega desses ADRs.

## Alternativas consideradas

- `BackgroundTasks`: reproduz o legado, mas não oferece recovery durável sozinho.
- Redis e outro worker: adicionaria outra fila sem necessidade; Redis não é SoT.
- Ampliar a fila PostgreSQL existente: mesma transação para produto, referências
  e jobs; worker já possui lease, poller e serviço Compose.

## Decisão

1. `POST /products` recebe `images` aprovadas, registra `source_url`, posição,
   principal e estados `pending`, e commita antes de confirmar sucesso.
2. O request não baixa imagens, não valida bytes, não calcula hash, não envia
   arquivos ao Drive e não converte AVIF. Sem polling interno ou espera de jobs.
3. `image-optimizer` reclama também originais pendentes, preserva original e
   commita esse checkpoint antes de converter/enviar AVIF.
4. Job e estado permanecem em `product_images`. Lease expirado permite retomada.
5. Reimportação serializa registro por produto e reutiliza a URL registrada.
   Hash reutiliza originais; otimizações prontas da mesma original são reutilizadas.
   Nomes UUID no Drive são consultados antes de uploads repetidos.
6. Falha de imagem não desfaz o produto. Retry explícito existente também
   reagenda preservação original quando ela falhou. Não há retry infinito.
7. Enquanto original está pendente, `display_url` pode usar a origem aprovada.
   Original preservada e AVIF pronta usam os endpoints privados de mídia da API.
8. A consulta privada `GET /products/{id}/import-status` informa produto salvo
   e contagens `pending`, `processing`, `ready`, `error`. A galeria existente
   expõe os dois estados detalhados. O frontend consulta a galeria separadamente.
9. Preview continua sendo `/crawl`, sem cadastro/Drive/AVIF. V2 não mantém
   preview de servidor; aprovação envia o snapshot revisado no contrato atual.
10. O endpoint de adição individual de imagem mantém seu contrato síncrono
    anterior; ele não pertence mais ao fluxo de importação do frontend.

## Justificativa

Reaproveita PostgreSQL e o worker existente, sem novas dependências nem mudança
de arquitetura de persistência. A referência antiga orienta a separação de
responsabilidades, sem copiar sua Data API ou registry local.
Fontes: [FastAPI](https://fastapi.tiangolo.com/tutorial/background-tasks/) e
[PostgreSQL](https://www.postgresql.org/docs/current/sql-select.html).

## Consequências positivas

- Sucesso depende de banco e registro dos jobs, não de Drive/AVIF.
- Falha de otimização preserva original e produto.
- API/worker podem reiniciar sem perder as referências pendentes.

## Trade-offs / consequências negativas

- URL externa pode expirar antes da preservação; imagem falha e permite retry.
- Galeria usa origem aprovada temporariamente, sujeita a bloqueios/hotlink.
- Leases e lookup de arquivo dão retomada idempotente; Drive não oferece uma
  transação conjunta com PostgreSQL. Claims expirados exigem atenção operacional.
- Advisory lock transacional exclusivo dos workers serializa preservação/conversão
  desse produto, sem bloquear a linha de produto usada pelo cadastro, evitando
  disputa por hashes; concorrência configurada continua disponível entre produtos.
- Limite atual permanece 20 imagens por produto; não foi aumentado.

O checkpoint downloading é confirmado antes de I/O externo: o worker não segura
lock de linha da imagem durante o download. A coordenação por advisory lock é
readquirida após esse commit; o lease continua delimitando ownership do job.
