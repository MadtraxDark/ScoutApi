from pathlib import Path
p=Path('scripts/benchmark_import_images.py')
s=p.read_text(encoding='utf-8').replace('Controlled benchmark: real approved sources, isolated catalog, no Drive writes.','Controlled benchmark: real approved sources and an isolated catalog.').replace('database is read-only; benchmark catalog state is SQLite. Existing Drive files\nare reused. Drive reuse timings must not be reported as new-upload timings.','database is read-only; benchmark catalog state is SQLite or an Alembic-migrated\nprobe PostgreSQL database. Existing Drive files are reused by default.\n--upload-drive creates a unique probe folder and removes only probe artifacts.\nDrive reuse timings must not be reported as new-upload timings.')
p.write_text(s,encoding='utf-8')
p=Path('docs/performance/import-images-2026-10-05.md');p.parent.mkdir(exist_ok=True)
p.write_text('''# Investigação da importação e imagens — 2026-10-05

## 1. Causa

O PriceScout fazia POST `/products`, seguido de um POST `/products/{id}/images` por imagem, sequencialmente, e um GET final. Cada upload individual aguardava download, validação e upload do original no Drive. AVIF já era background. O catálogo era salvo, mas a interface continuava aguardando I/O externo.

## 2. Evidência anterior por etapa

Logs Docker reais do produto `37fe4214-752e-4e32-b615-e482535d7a02`: POST de produto concluído às 19:25:06.942197 UTC; último upload de imagem concluído às 19:26:29.398672 UTC. Espera adicional: **82,456 s** para 20 imagens. Requests individuais observados: aproximadamente 3–7 s; primeiro: 5,958 s. A 21ª referência foi rejeitada por limite de 20. Os logs anteriores não guardaram duração separada de banco, download, Drive e conversão; não é possível reconstruir esses números com precisão. Não atribuir 82 s ao POST `/products`, que já havia terminado.

## 3. Comparação com ScoutApi antigo

Inspecionado o repositório real no commit `7c26815163dee64c80daf679d14ee6a05d52c7d2`, incluindo `app/main.py`, `catalog_service.py`, `image_pipeline.py`, ADR 0028, testes de importação e README. O antigo resolvia/persistia o catálogo, registrava referências, agendava processamento com BackgroundTasks e retornava o produto. O V2 tinha lease PostgreSQL para AVIF, mas preservava o original dentro de cada upload síncrono.

## 4. Reaproveitamento conceitual

Reaproveitado o contrato de salvar catálogo e referências antes do processamento pesado. Não foram copiados o acesso Supabase Data API nem o registry local antigo. Preview V2 é `/crawl`; não existe preview persistido a resolver/deletar no servidor. O preview não registra catálogo, imagens ou Drive.

## 5. Operações síncronas

Autorização/ownership, validação, resolução da identidade, listing/oferta/snapshots conforme contrato existente, registro das URLs aprovadas, construção da ProductView e commit. Variante faz parte da identidade existente; não foi criada nova tabela. A resposta confirma o commit, não uma simulação no frontend.

## 6. Operações em background

Download seguro, validação/hash, busca/criação de folders e upload original Drive; checkpoint do original; leitura do original preservado, conversão AVIF e upload otimizado. Logos já pertenciam ao worker e continuam fora da importação.

## 7. Infraestrutura

Reutilizada a fila persistida nas imagens, claim PostgreSQL com SKIP LOCKED e leases, worker/poller existentes e limites de concorrência existentes. BackgroundTasks não fornece o estado durável necessário em restart; Redis/Celery não eram necessários. A decisão está na ADR 0048. Capacidade Camoufox não foi alterada.

## 8. Referências persistidas

`register_references` grava source_url, posição, principal, original pending, optimized pending e agendamento dentro da transação do produto. Limite existente: 20. URLs repetidas do mesmo produto reutilizam a referência. Não há chamada ao downloader ou Drive nesse método.

## 9. Original

Worker preserva o original e confirma essa etapa antes do AVIF. Falha de AVIF mantém original disponível. Hash permite reutilização de originais do mesmo produto; identidade da referência é preservada. Lookup Drive por nome estável reduz duplicação após retry. Drive e PostgreSQL não formam uma transação distribuída: não se promete exactly-once externo.

## 10. AVIF

Conversão existente com Pillow/optimizer, sobre original preservado, respeitando limites do worker. Otimização pronta de um mesmo original pode ser reutilizada. Original nunca é apagado ao gerar AVIF.

## 11. Status

Original: pending → downloading → ready/failed. Otimizado: pending → processing → ready/failed. Lease expirada torna trabalho recuperável. GET privado `/products/{id}/import-status` agrega pending/processing/ready/error e saved=true, com ownership.

## 12. Frontend

Um único POST `/products` envia todas as referências aprovadas. A interface informa produto salvo e processamento em segundo plano. Catálogo e galeria fazem polling visível de 3 s enquanto há trabalho; não há espera dentro da importação. source_url temporária → original preservado → AVIF pronta. Polling é cancelável e preserva edições do formulário.

## 13. Falhas/retries

Falha de imagem não desfaz o produto. Erros persistidos são sanitizados. Retry de original falho reabre job, sem download síncrono. Restart recupera lease expirada; commit do original evita repetir preservação quando AVIF falha. Downloads continuam sob validação SSRF existente. Sem retries infinitos novos.

## 14–16. Medições depois

Fonte real aprovada: 20 imagens do produto acima. Script `scripts/benchmark_import_images.py`; catálogos isolados, sem alterar catálogo de produção. PostgreSQL de benchmark foi migrado com Alembic. HTTP por TestClient usa serviço/transação reais; não inclui rede do navegador nem latência do Supabase remoto. Cada linha é uma execução, sem afirmar P95.

| Referências | POST com PostgreSQL local (ms) | Background com reuso Drive (s) | Background com uploads reais (s) |
|---|---:|---:|---:|
| 1 | 187,6 | 1,233 | 11,723 |
| 5 | 48,7 | 5,132 | 46,681 |
| 10 | 54,8 | 9,162 | 88,059 |
| 20 | 103,9 | 19,481 | 168,970 |

Background com reuso não mede upload novo. Coluna com uploads reais usa catálogo SQLite isolado e pasta única de probe no Drive; 36 imagens chegaram a ready, e somente arquivos/pastas de probe foram removidos após cada caso. Não comparar esse tempo total diretamente com a espera antiga, pois agora inclui AVIF e consultas de idempotência.

| Imagens | Download (s) | Folders (s) | Upload original (s) | Leitura original (s) | Conversão AVIF (s) | Upload AVIF (s) |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 0,180 | 4,641 | 4,036 | 0,625 | 0,121 | 2,104 |
| 5 | 0,677 | 13,704 | 13,526 | 3,598 | 0,607 | 14,530 |
| 10 | 1,218 | 23,525 | 27,665 | 7,453 | 2,735 | 25,386 |
| 20 | 2,481 | 42,950 | 50,437 | 13,939 | 8,544 | 50,460 |

Um downloader artificialmente lento (10 s) fez o teste falhar no caminho anterior; após a correção, o POST termina sem chamá-lo. Isso demonstra desacoplamento real. Os tempos por etapa agora aparecem nos logs de registro, commit, preservação e AVIF. O limite de 20 continua; não há benchmark de quantidade inválida acima desse limite.

## 17. Arquivos ScoutApiV2

Módulos images: pipeline, claim, worker, repository, service, schemas, router e drive_client; matching: product_registration_service, repository e router; Dockerfile; testes de imagens/crawler; script de benchmark; ADR 0048 e índices; documentação de imagens, integração e desempenho. Alterações simultâneas em exchange são de outra tarefa.

## 18. Arquivos PriceScout

`utils/api/services.ts`, `utils/api/contracts.ts`, `app/admin/page.tsx`, `app/admin/produtos/[id]/page.tsx`, `utils/api/import-product.test.ts` e memória de trabalho da importação.

## 19. Validação automatizada e real

46 testes direcionados backend passaram em 1,24 s: referências 1/5/10/20 sem I/O pesado, POST real da aplicação, falha/download/retry, original antes de AVIF, falha de AVIF, dedup, lease expirada e banco reaberto após fechar engine, preview sem efeitos. Frontend: 10 testes passaram (importação em um POST, adapters e polling); TypeScript passou. Catálogo e galeria reais foram inspecionados no navegador com 20 imagens disponíveis. Benchmark usa downloads, conversões e Drive reais, não dados fictícios de produção.

## 20. Regressões e limites

Corrigidos: espera sequencial frontend; original síncrono na importação; recuperação de original pending; comparação naive/aware no claim SQLite; reuso de original/AVIF e erro sanitizado; modelo fpgen sem permissão no build sob usuário app. Build oficial passou e modelo foi validado sob app. Runtime local mantém Camoufox 0.5.6 validado, sem adotar atualização de browser não benchmarkada (PENDING-029).

Suíte rápida ampla: 1048 passaram, 8 ignorados e 3 falharam antes dos dois últimos testes adicionados. As mesmas três falhas de query Amazon foram reproduzidas no código HEAD anterior. Mypy global: 37 erros em 9 arquivos, contra 41 em 11 arquivos no baseline; sem novos erros dos módulos de imagens. ESLint global do admin tem falha preexistente de set-state-in-effect. Não declarar suíte global verde; acompanhamento canônico: PENDING-028. A duração do preview real depende do crawler e não foi otimizada nem prometida por esta alteração.
''',encoding='utf-8')
for name in ['docs/performance.md','docs/README.md']:
 p=Path(name);s=p.read_text(encoding='utf-8');s+='\n- [Investigação e medições da importação de imagens (2026-10-05)]('+('performance/import-images-2026-10-05.md' if name=='docs/README.md' else 'performance/import-images-2026-10-05.md')+').\n';p.write_text(s,encoding='utf-8')
p=next(Path('docs/pending').glob('PENDING-029*'));s=p.read_text(encoding='utf-8');s+='\n## Atualização — investigação da importação (2026-10-05)\n\nDockerfile concede ownership somente a fpgen/data ao usuário app. Build oficial\napi + image-optimizer passou; ensure_fpgen_model foi validado sob app. O bloqueio\nde permissão está corrigido. Permanece aberta somente a validação/pin do runtime\nCamoufox novo; execução local conserva 0.5.6 da imagem validada. Não implantar\nbrowser novo sem evidência exigida pelo projeto. Ver relatório de desempenho\n[importação](../performance/import-images-2026-10-05.md).\n';p.write_text(s,encoding='utf-8')
