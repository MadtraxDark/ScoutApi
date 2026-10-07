# PENDING-032 — Remediar cookies rastreados no histórico Git

- Status: IN_PROGRESS
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

- Confirmar se os cookies ainda são válidos e invalidar/renovar as sessões nas
  lojas ou contas de origem, se aplicável.
- Confirmar se commits que continham o arquivo foram enviados a algum remoto.
- Se houve publicação, avaliar a remoção do conteúdo do histórico compartilhado
  e coordenar a atualização dos clones; a regra de ignore não altera commits
  anteriores.

## Impacto

Se os valores ainda forem válidos e o histórico tiver sido compartilhado, uma
terceira pessoa com acesso ao histórico pode tentar reutilizar as sessões.

## Relacionado

- `.gitignore`
- `.dockerignore`
- `cookies.txt` (cópia local ignorada)

## Pronto quando

- Cookies potencialmente ativos forem invalidados ou confirmados expirados.
- O alcance de publicação do histórico for confirmado.
- Caso o arquivo tenha sido publicado, o histórico compartilhado for remediado
  com a coordenação necessária, sem deixar valores válidos acessíveis.
