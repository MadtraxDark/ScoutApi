import ast
import json
import re
import textwrap
from pathlib import Path

diagnostics = json.loads(Path('.tmp/ruff-diagnostics.json').read_text())
by_file = {}
for item in diagnostics:
    by_file.setdefault(item['filename'], []).append(item)
for name, issues in by_file.items():
    path = Path(name)
    text = path.read_text(encoding='utf-8')
    if any(i['code'] == 'E402' for i in issues):
        tree = ast.parse(text)
        nodes = tree.body
        imports = [n for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))]
        last = max(n.end_lineno for n in imports)
        prelude = [n for n in nodes if n.lineno < last and not isinstance(n, (ast.Import, ast.ImportFrom, ast.Expr))]
        # ROOT is only a bootstrap convenience; keep path setup before imports.
        for n in prelude:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ROOT' for t in n.targets):
                value = ast.get_source_segment(text, n.value)
                text = text.replace('str(ROOT / "src")', f'str({value} / "src")')
                text = text.replace(ast.get_source_segment(text, n) or '', '') if False else text
        # Move declarations below the final import; runtime bootstrap calls stay.
        tree = ast.parse(text)
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        last = max(n.end_lineno for n in imports)
        movable = [n for n in tree.body if n.lineno < last and isinstance(n, (ast.Assign, ast.ClassDef))]
        lines = text.splitlines(keepends=True)
        moved = ''.join(''.join(lines[n.lineno-1:n.end_lineno])+'\n' for n in movable)
        for n in reversed(movable):
            lines[n.lineno-1:n.end_lineno] = ['\n'] * (n.end_lineno-n.lineno+1)
        lines.insert(last, '\n'+moved)
        text = ''.join(lines)
    # Rename unused bindings, retaining calls and their side effects.
    for issue in issues:
        if issue['code'] in ('F841', 'B007'):
            match = re.search(r'`([^`]+)`', issue['message'])
            if match:
                token = match.group(1)
                text = re.sub(r'\b'+re.escape(token)+r'\b', '_'+token, text)
    path.write_text(text, encoding='utf-8')

# Comments and prose docstrings are wrapped without changing executable strings.
for name in by_file:
    path = Path(name)
    text = path.read_text(encoding='utf-8')
    tree = ast.parse(text)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                doc_lines.update(range(node.body[0].lineno, node.body[0].end_lineno+1))
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        if len(line) > 88 and (line.lstrip().startswith('#') or number in doc_lines):
            indent = line[:len(line)-len(line.lstrip())]
            prefix = indent + '# ' if line.lstrip().startswith('#') else indent
            content = line.lstrip()[1:].lstrip() if line.lstrip().startswith('#') else line.strip()
            lines.extend(textwrap.wrap(content, width=88, initial_indent=prefix, subsequent_indent=prefix, break_long_words=False, break_on_hyphens=False))
        else:
            lines.append(line)
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
