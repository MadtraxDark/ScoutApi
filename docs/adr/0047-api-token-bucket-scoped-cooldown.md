# ADR 0047: Reposição contínua e cooldown por escopo na API

- Status: Accepted
- Data: 2026-10-05
- Complementa ADR 0023; substitui apenas seu trade-off de rate limiting fixed-window.

## Contexto

PriceScout combina leituras, polling de jobs e operações caras. A separação
de buckets já existe, mas uma janela fixa pode bloquear por quase um minuto
após uma rajada legítima. O cliente pausava por rota enquanto a API limita
várias rotas no mesmo escopo. Hash do Bearer também renovava a identidade
da cota junto com o access token. Cooldowns de marketplace compartilham
o código `RATE_LIMITED`, sem significar uma cota global da API.

## Problema / decisão necessária

Preservar proteção contra abuso e capacidade operacional sem tornar navegação
normal dependente de uma janela de bloqueio longa e opaca.

## Alternativas consideradas

- Aumentar cotas ou desligar limites: não resolve coordenação e enfraquece proteção.
- Janela fixa: simples, mas recuperação abrupta e rajada nas bordas.
- Janela deslizante: apropriada para teto temporal estrito, ainda pode impor espera longa.
- Token bucket por identidade e escopo: reposição gradual e rajada finita explícita.
- Serviço pago ou nova biblioteca: desnecessários; Redis e Lua já fazem parte da stack.

## Decisão

Adotar token bucket com capacidade igual às cotas existentes e taxa de
reposição igual à capacidade por minuto. Redis executa a decisão atomicamente
com `TIME`; memória usa relógio monotônico e lock. Rejeições não gastam tokens.
TTL limita vida das chaves, sem redefinir o saldo enquanto existe tráfego.
Usar namespace novo para evitar conflito de tipos com contadores legados.

Identidade Bearer deriva do `sub` validado; rotas públicas de auth usam IP.
Contrato 429 aditivo explicita política e escopo, com headers CORS legíveis.
O frontend pausa consumidores do mesmo escopo, mantendo demais políticas
independentes. Cooldowns upstream permanecem locais por rota e identificáveis.
Manter jobs persistentes, single-flight, cache, cooldowns de loja e browser C1.

Contrato completo e fallback: [segurança da API](../security/api-auth.md).

## Justificativa

Recuperação proporcional ao custo configurado e coordenação coerente entre
cliente e servidor. Implementação usa capacidades existentes, sem dependência,
serviço pago ou alteração no fluxo Camoufox. Referências:
[Redis](https://redis.io/docs/latest/develop/use-cases/rate-limiter/),
[OWASP API4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/),
[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).

## Consequências positivas

- Saldo recupera mesmo sob tentativas contínuas rejeitadas.
- Renovar JWT não contorna a cota; usuários diferentes continuam isolados.
- Polling não paralisa CRUD nem início de coletas.
- Logs distinguem cota API e fallback sem registrar dados pessoais.

## Trade-offs / consequências negativas

- Capacidade mais reposição permite mais que a capacidade numa janela de
  60 segundos iniciada com saldo cheio; a taxa sustentada permanece limitada.
- Redis indisponível mantém a degradação por processo existente, sem garantia
  de teto global entre réplicas. Warning torna essa condição observável.
- Durante rollout, réplicas antigas e novas têm namespaces diferentes. Atualizar
  réplicas da API em conjunto; não confiar em teto único durante versões mistas.
- Pausa do cliente é por aba e sessão; a API mantém a autoridade entre abas.
- Não determina novas cotas de produção sem medir tráfego e origem dos 429.
