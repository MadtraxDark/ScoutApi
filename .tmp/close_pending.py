from pathlib import Path
import re

root=Path('.').resolve()
pending=root/'docs/pending'
report='../../performance/pending-recovery-2026-10-05.md'
notes={
 'PENDING-023-image-availability-frontend.md': 'Drive operacional; 29/29 imagens/logos carregados no Chrome antes e após rollout. Contratos de disponibilidade/retry cobertos pelos 77 testes frontend e pela suíte backend. Typecheck/lint completos passaram; pytest via python eliminou o bloqueio do executável antigo.',
 'PENDING-028-baseline-quality-gates.md': '1090 testes backend passaram (10 condicionais ignorados), PostgreSQL progressivo executado explicitamente, Ruff check/formato e mypy src verdes. 77 testes frontend, typecheck e lint completos verdes. Corrigidos locale Amazon e contratos tipados, sem reduzir cobertura ou suprimir código real.',
 'PENDING-029-camoufox-rebuild-fpgen-permissions.md': 'Build oficial final passou (exit=0). Camoufox 0.5.7/browser 156.0.1-beta.34 fixados e validados em C1 em lojas reais. API final saudável; fpgen/data gravável por app; capacidade=1 e scheduler ativo. Workers atualizados iniciaram com Alembic head 0032_match_live. Banco e volumes preservados.',
 'PENDING-030-cold-preview-browser-duration.md': 'Causa demonstrada por subestágios: warmup/networkidle aguardavam tráfego sem relação com oferta. Corrigida prontidão na homepage e PDP, preservando pausa configurada e resolução anti-bot. Budget escolhido pelo usuário: 15 s frio/1 s cache. Três amostras finais reais: 13.772,60/7.171,11/7.466,80 ms; cache 0,38/0,31/0,40 ms; 25 imagens e oferta válida em todas, sem proxy. Não representa P95 populacional.',
}
files=list(notes)+['PENDING-026-match-wave-c1-browser-queue-timeout.md','PENDING-027-match-c1-store-wall-time.md']
for name in files:
    source=pending/name
    target=pending/'resolved'/name
    assert source.is_relative_to(root) and target.is_relative_to(root)
    assert source.exists() and not target.exists(),name
    text=source.read_text(encoding='utf-8')
    if name in notes:
        text=re.sub(r'- Status: (?:OPEN|IN_PROGRESS)', '- Status: RESOLVED',text,count=1)
        heading,history=text.split('\n',1)
        text=heading+'\n\n- Status: RESOLVED\n- Resolvida: 2026-10-05\n\n## Resolução final\n\n'+notes[name]+'\n\n[Evidências e limites]('+report+').\n\n## Histórico anterior à resolução\n\n'+history
        text=text.replace('\n- Status: RESOLVED\n- Tipo:', '\n- Tipo:')
    # Moving one level deeper changes references outside the pending directory.
    text=text.replace('(../performance/','(../../performance/').replace('(../../.cursor/','(../../../.cursor/')
    source.write_text(text,encoding='utf-8')
    source.rename(target)

index=pending/'README.md'
text=index.read_text(encoding='utf-8')
start=text.index('| ID | Título | Status | Tipo | Prioridade | Área |')
end=text.index('Resolvida em 2026-09-24:',start)
resolved='\n'.join('- ['+name.split('-')[0]+'-'+name.split('-')[1]+'](resolved/'+name+')' for name in files)
replacement='Nenhuma pendência acionável aberta. **Pendências ativas: 0.**\n\nEncerramento e reconciliação em 2026-10-05:\n\n'+resolved+'\n\n[Validações, limitações e rollout](../performance/pending-recovery-2026-10-05.md).\nOs itens 016/017/019/020/021 já estavam arquivados; a listagem antiga foi corrigida.\nML/Shopee continuam desativadas conforme a decisão anterior do usuário.\n\n'
text=text[:start]+replacement+text[end:]
text=re.sub(r'\n- \[PENDING-030 — Preview frio Magazine Luiza\].*\n','\n',text)
index.write_text(text,encoding='utf-8')
old=pending/'resolved/PENDING-025-product-match-camoufox-live-recovery.md'
text=old.read_text(encoding='utf-8').replace('(../PENDING-026-','(PENDING-026-')
old.write_text(text,encoding='utf-8')
