import re
from pathlib import Path

t = Path("tests/fixtures/visaovip/_raw_serp.html").read_text(
    encoding="utf-8", errors="replace"
)
print("len", len(t))
print("41749", "41749" in t)
print("/prod/ count", len(re.findall(r"/prod/", t)))
paths = sorted({m.group(0) for m in re.finditer(r"/prod/(?:[^/\s\"'<>]+/)+(\d+)/?", t)})
print("paths", paths[:20], "n=", len(paths))
print("postponed", "postponed" in t.lower() or "loading" in t[:2000].lower())
print("termo in url title", "B650M" in t[:3000])
