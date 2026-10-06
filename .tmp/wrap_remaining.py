import ast
import io
import json
import tokenize
from pathlib import Path

issues=json.loads(Path('.tmp/ruff-diagnostics.json').read_text())
for name in {i['filename'] for i in issues if i['code']=='E501'}:
    path=Path(name)
    source=path.read_text(encoding='utf-8')
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    offsets=[0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    def offset(row,col):
        return offsets[row-1]+len(lines[row-1].encode('utf-8')[:col].decode('utf-8'))
    forbidden={id(c) for p in ast.walk(tree) if isinstance(p,ast.JoinedStr) for c in ast.walk(p)}
    replacements=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Constant) or id(node) in forbidden or not isinstance(node.value,(str,bytes)):
            continue
        if not any(len(line.rstrip())>88 for line in lines[node.lineno-1:node.end_lineno]): continue
        indent=' '*(len(lines[node.lineno-1])-len(lines[node.lineno-1].lstrip())+4)
        pieces=[repr(node.value[i:i+44]) for i in range(0,len(node.value),44)]
        replacements.append((offset(node.lineno,node.col_offset),offset(node.end_lineno,node.end_col_offset),'(\n'+indent+('\n'+indent).join(pieces)+'\n'+indent[:-4]+')'))
    for node in ast.walk(tree):
        if not isinstance(node,ast.JoinedStr) or node.lineno!=node.end_lineno or len(lines[node.lineno-1].rstrip())<=88: continue
        segment=ast.get_source_segment(source,node)
        tokens=list(tokenize.generate_tokens(io.StringIO(segment).readline))
        middles=[t for t in tokens if t.type==tokenize.FSTRING_MIDDLE and ' ' in t.string]
        if not middles: continue
        quote=tokens[0].string[1:]
        choices=[t.start[1]+i+1 for t in middles for i,c in enumerate(t.string) if c==' ']
        cut=min(choices,key=lambda c:abs(c-len(segment)/2))
        indent=' '*(len(lines[node.lineno-1])-len(lines[node.lineno-1].lstrip())+4)
        replacement='(\n'+indent+segment[:cut]+quote+'\n'+indent+'f'+quote+segment[cut:]+'\n'+indent[:-4]+')'
        replacements.append((offset(node.lineno,node.col_offset),offset(node.end_lineno,node.end_col_offset),replacement))
    result=source
    for start,end,value in sorted(replacements,reverse=True): result=result[:start]+value+result[end:]
    assert ast.dump(ast.parse(result))==ast.dump(tree),name
    path.write_text(result,encoding='utf-8')
