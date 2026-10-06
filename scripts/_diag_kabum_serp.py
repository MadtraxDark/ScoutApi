"""Inspect KaBuM SERP HTML structure for product links."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scrapy.http import HtmlResponse, Request

h = Path("data/kabum_q3.html").read_text(encoding="utf-8", errors="replace")
print(
    "a href produto count",
    len(re.findall(r"<a[^>]+href=[\"'][^\"']*produto[^\"']*", h, re.I)),
)
print(
    "sample a tags",
    re.findall(r"<a[^>]+href=[\"']([^\"']*produto[^\"']*)[\"']", h, re.I)[:10],
)
print("href without quotes?", "/produto/777166" in h)

# NEXT_DATA
m = re.search(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    h,
    re.S,
)
assert m
data = json.loads(m.group(1))
props = data.get("props", {}).get("pageProps", {})
print("pageProps keys", list(props.keys()))


def find_paths(obj, path="$"):
    if isinstance(obj, dict):
        if "777166" in json.dumps(obj, ensure_ascii=False)[:500000]:
            for k, v in obj.items():
                if (
                    "777166" in json.dumps(v, ensure_ascii=False)
                    if not isinstance(v, (str, int))
                    else str(v)
                ):
                    yield from find_paths(v, f"{path}.{k}")
                elif str(v) == "777166" or (isinstance(v, str) and "777166" in v):
                    yield f"{path}.{k}={v!r}"[:200]
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            blob = (
                json.dumps(v, ensure_ascii=False)
                if not isinstance(v, (str, int, float))
                else str(v)
            )
            if "777166" in blob:
                yield from find_paths(v, f"{path}[{i}]")


hits = list(find_paths(props))
print("path hits", len(hits))
for hit in hits[:20]:
    print(hit)

# Try scrapy css on file
resp = HtmlResponse(
    "https://www.kabum.com.br/busca/x",
    body=Path("data/kabum_q3.html").read_bytes(),
    encoding="utf-8",
    request=Request("https://www.kabum.com.br/busca/x"),
)
print("css all produto", len(resp.css("a[href*='/produto/']::attr(href)").getall()))
print("css sample", resp.css("a[href*='/produto/']::attr(href)").getall()[:5])
print(
    "any a with produto in link",
    [x for x in resp.css("a::attr(href)").getall() if "produto" in x][:5],
)
