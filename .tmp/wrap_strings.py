import ast
import io
import json
import tokenize
from pathlib import Path

for name in {i['filename'] for i in json.loads(Path('.tmp/ruff-diagnostics.json').read_text()) if i['code']=='E501'}:
    path=Path(name)
    source=path.read_text(encoding='utf-8')
    original=ast.dump(ast.parse(source), include_attributes=False)
    lines=source.splitlines(keepends=True)
    tree=ast.parse(source)
    forbidden={id(child) for parent in ast.walk(tree) if isinstance(parent,ast.JoinedStr) for child in ast.walk(parent)}
    nodes=sorted((n for n in ast.walk(tree) if isinstance(n,ast.Constant) and id(n) not in forbidden),key=lambda n:(n.lineno,n.col_offset),reverse=True)
    for node in nodes:
        row=node.lineno-1
        if node.lineno != node.end_lineno or len(lines[row].rstrip()) <= 88:
            continue
        value=node.value
        if not isinstance(value,(str,bytes)):
            continue
        width=45
        pieces=[repr(value[i:i+width]) for i in range(0,len(value),width)]
        indent=' '*(len(lines[row])-len(lines[row].lstrip())+4)
        replacement='(\n'+indent+('\n'+indent).join(pieces)+'\n'+indent[:-4]+')'
        before=lines[row].encode('utf-8')[:node.col_offset].decode('utf-8')
        after=lines[row].encode('utf-8')[node.end_col_offset:].decode('utf-8')
        lines[row]=before+replacement+after
    result=''.join(lines)
    assert ast.dump(ast.parse(result),include_attributes=False)==original,name
    path.write_text(result,encoding='utf-8')

path=Path('scripts/_ps5_fast.py')
text=path.read_text()
start=text.index('print(')
end=text.index('# SC candidate search')
block=text[start:end]
text=text[:start]+text[end:]
text=text.replace('from scout_api.modules.matching.identity import identity_from_price_item', 'from scout_api.modules.matching.identity import build_search_queries, identity_from_price_item')
text=text.replace('from scout_api.modules.matching.identity import build_search_queries\n','')
needle='s3 = engine.score(id_kab, id_sc)'
text=text.replace(needle,needle+'\n\n'+block)
path.write_text(text)
