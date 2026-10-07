# PENDING-032 — Remediar cookies rastreados no histórico Git

- Status: RESOLVED
- Tipo: INCOMPLETE
- Prioridade: P0
- Área: segurança / Git / Docker
- Origem: 2026-10-07 — auditoria de arquivos ignorados
- Atualizado: 2026-10-07

## Contexto

O arquivo local `cookies.txt` estava rastreado pelo Git. O conteúdo não foi
aberto nem copiado durante a auditoria. Como cookies podem conceder acesso a
contas, é necessário tratar o histórico como potencialmente sensível.

## Feito

- Adicionado `cookies.txt` ao `.gitignore`.
- Adicionado `cookies.txt` ao `.dockerignore` para excluí-lo do contexto de build.
- Removido `cookies.txt` do índice do Git; a cópia local foi preservada.

## Falta

- Nenhuma ação de revogação necessária: a única entrada era um verifier PKCE de
  `localhost`, expirado em 2026-09-22; não era cookie de sessão de loja.
- A publicação foi confirmada em `origin/main`; essa branch já removeu o caminho.
  `origin/embedding` e `origin/dependabot/uv/uv-5cc95df640` ainda contêm o mesmo
  blob expirado na árvore atual.
- Os commits históricos e as refs de pull request não foram reescritos. Como o
  único valor identificado expirou e não concede uma sessão de loja, reescrever
  o histórico compartilhado traria impacto em branches/PRs sem revogar uma
  credencial ativa. A [orientação do GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
  também recomenda primeiro revogar/rotacionar credenciais e reconhece que isso
  pode bastar sem reescrita quando mitiga o risco.

## Impacto

O blob expirado permanece acessível no histórico de `origin/embedding` e
`origin/dependabot/uv/uv-5cc95df640`. Não há credencial de sessão válida nele.

## Relacionado

- `.gitignore`
- `.dockerignore`
- `cookies.txt` (cópia local ignorada)

## Pronto quando

- A única entrada for confirmada expirada e a cópia local for removida.
- A publicação e as branches que ainda carregam o blob forem identificadas.
- O arquivo deixar de ser incluído em novos commits e builds Docker.

## Resolução

- Data: 2026-10-07
- Resumo: entrada `scout_pkce_verifier` para `localhost` expirada em
  2026-09-22; cópia local removida. `cookies.txt` permanece protegido por
  `.gitignore` e `.dockerignore`. `origin/main` não contém o caminho; duas
  branches remotas mantêm apenas o blob expirado no histórico/árvore.
- Follow-ups: nenhum para credenciais ativas. Reescrita histórica não executada
  porque não há valor válido a revogar e a operação afetaria branches e PRs.
