# Resolução de pendências e Amazon — 2026-10-05

## Escopo

Imagens, qualidade global, build Camoufox e preview frio Magalu. ML/Shopee
continuam desativadas no Match conforme a decisão anterior do usuário; os itens
016/017 já estavam arquivados. 019, 020, 021, 026 e 027 também já estavam resolvidos.

## Amazon

O título normalizado continua primeiro. Cor explícita é localizada na mesma
posição, preservando acabamento: Rosa → pink; Titânio Preto → black titanium
nos EUA. Substrings de modelo/MPN não são traduzidas. Termos originais permanecem
como fallback. Duas expectativas antigas foram reconciliadas com o contrato
canônico de título primeiro; a regressão real de locale ganhou testes específicos.

Outra causa reproduzida: HTTP entregava PDP sem Buy Box mesmo após retry.
Agora, retry esgotado sem preço nem OOS explícito segue para browser direto.
Challenge/auth continuam sendo resolvidos; proxy somente após bloqueio
classificado. AOD/relacionados não são usados como preço e OOS não é fabricado.

Product Match real, persist=false, referência sem cotação artificial, C1:

| Mercado | ASIN descoberto | Preço observado | Decisão |
|---|---|---|---|
| BR | B0DSYJCY45 | BRL 4958.10 | auto_match |
| US | B0DP3GQ4QY | USD 815.98 | auto_match |

Execução completa: 28,49 s; uma PDP por mercado; nenhum erro. São observações
desta execução, não garantias de preço futuro.

## Magazine Luiza

Budget escolhido explicitamente pelo usuário: **15 s frio / 1 s cache**.
Baseline histórico: 61,30 s. Medições delimitaram aquisição do browser, warmup
e navegação; settle/extrair HTML ficou abaixo de 0,5 s. HTTP direto foi testado:
bloqueio classificado em 187 ms. Não foi adotado outro caminho ou serviço pago.

Networkidle incluía analytics e recursos sem relação com oferta. Homepage de
warmup agora termina a navegação em DOMContentLoaded e **mantém a pausa
configurada de cookies/challenge**. Na PDP, só dispensa networkidle se o documento
não for interstitial e NEXT_DATA já contiver item, título, id e lista de ofertas.
Documento incompleto, challenge/auth e outras lojas preservam o fluxo anterior.
Parsing de preço/seller, soft-404, imagens e resolução anti-bot continuam obrigatórios.

| Amostra final fria | Total | Cache | Imagens | Oferta válida |
|---|---|---|---|---|
| 1 | 13.772,60 ms | 0,38 ms | 25 | Sim |
| 2 | 7.171,11 ms | 0,31 ms | 25 | Sim |
| 3 | 7.466,80 ms | 0,40 ms | 25 | Sim |

Três sucessos dentro do budget, sem proxy. Amostra pequena: não estabelece P95
populacional nem garante tempo para qualquer challenge/falha upstream. Medições
intermediárias de 22,54 s e 19,08 s motivaram a correção adicional do warmup;
não foram contabilizadas como sucesso. Execuções com wrapper incorreto de um
método estático foram descartadas como diagnóstico inválido.

FetchCostMetrics.stage_timings_ms expõe browser_acquire, page_create, warmup,
navigation e settle_challenge, também em erro, preservando retorno/exceção.
O budget genérico de browser não foi relaxado; o contrato específico é o acima.

## Imagens, qualidade e runtime

- Drive respondeu ao teste operacional; não foi necessário alterar OAuth.
- Chrome com API local integrada: **29/29 imagens e logos carregados**, galeria
  e imagem principal do S25 Ultra. Nenhum upload novo ao Drive.
- Backend: **1090 passed, 10 skipped**, 21,74 s. Testes condicionais exigem
  serviços/cenários específicos. PostgreSQL progressivo executado explicitamente
  no banco isolado: **1 passed**, 0,31 s.
- Ruff check/formato e mypy passaram; 204 arquivos src. Excluídos apenas
  diretórios temporários/de agentes, sem excluir código, testes, migrations ou scripts.
- Frontend: **77 testes**, typecheck e lint completos passaram. Opção de imagens
  deriva do default da loja até escolha explícita; reset continua na mudança de
  produto, sem setState síncrono em effect.
- Build oficial: Camoufox **0.5.7**, browser **156.0.1-beta.34**, fpgen com ownership
  restrito a app. C1 permanece 1. Monitor não herda mais healthcheck HTTP da API.
- Conferência operacional: match-runner estava ativo, porém sua CLI não configurava
  INFO. A entrada agora respeita scraper_log_level e instala o redactor canônico;
  início e timings ficam visíveis. Subestágios também aparecem no texto do log
  fetch_cost_metrics, além do payload estruturado.

## Documentação impact review

Consultadas as fontes oficiais de [Camoufox](https://camoufox.com/python/installation/)
e [Playwright](https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state).
Playwright desaconselha networkidle como sinal de prontidão; a correção usa o
payload público observado e mantém os contratos específicos da loja.

Atualizados contratos Amazon/Magalu, matching, Docker, performance e pendências.
Auth/ownership, capacidade C1, credenciais, Buy Box e aprovação de imagens permanecem
com as mesmas regras.

## Rollout local verificado

Build final: exit=0. Antes da troca, nove Runs estavam completed e nenhuma ativa.
Workers de Match/monitor foram parados durante a mudança; API foi atualizada
primeiro e aplicou **0032_match_live**. Health retornou status=ok/database=ok.
Depois, match-runner, monitor e image-optimizer foram atualizados e iniciaram;
os três registraram alembic_upgrade_head: done. PostgreSQL/Redis/volumes não
foram reiniciados nem apagados. Camoufox 0.5.7, browser fixado, scheduler ativo,
capacity=1 e fpgen gravável por app confirmados na API em execução.

Nova aba Chrome após rollout: **29/29 imagens/logos carregadas**, zero erros
de console. O banco permanece com cinco produtos e 47 imagens; os smokes de
lojas usaram persist=false e não fizeram aprovação/upload. Ambiente continua
local de desenvolvimento, com a configuração de autenticação existente.
Nenhum deploy público foi realizado. Índice ativo reconciliado: **0 pendências**.

Imagem final com observabilidade aplicada e verificada: `match_embedding_runtime_ready`,
`match_run_worker_started` e `match_run_watchdog_started` aparecem em INFO;
monitor e image-optimizer também confirmaram inicialização. Health e schema
0032 foram conferidos novamente após a troca.
