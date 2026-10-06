"""Walk KaBuM __NEXT_DATA__ for search catalog items."""

from __future__ import annotations

import json
import re
from pathlib import Path

h = Path("data/kabum_q1.html").read_text(encoding="utf-8", errors="replace")
m = re.search(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    h,
    re.S,
)
assert m
data = json.loads(m.group(1))
props = data["props"]["pageProps"]
print("keys", props.keys())
inner = props.get("data")
print("data type", type(inner), list(inner.keys()) if isinstance(inner, dict) else None)

# dump shallow
if isinstance(inner, dict):
    for k, v in inner.items():
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]", type(v[0]).__name__ if v else None)
            if v and isinstance(v[0], dict):
                print("   sample keys", list(v[0].keys())[:20])
                print("   sample", {kk: v[0].get(kk) for kk in list(v[0].keys())[:8]})
        elif isinstance(v, dict):
            print(f"  {k}: dict keys", list(v.keys())[:20])
        else:
            print(f"  {k}:", repr(v)[:80])

# also search whole next for code patterns like "code":777166
text = m.group(1)
for pat in [
    r'"code":777166',
    r'"id":777166',
    r'"productId":777166',
    r"/produto/777166",
]:
    print(pat, text.count(pat))

# Find list of products by looking for dictionaries with code/id and name
found = []


def walk(obj, depth=0):
    if depth > 12:
        return
    if isinstance(obj, dict):
        code = obj.get("code") or obj.get("id") or obj.get("productId")
        name = obj.get("name") or obj.get("title") or obj.get("friendlyName")
        link = obj.get("link") or obj.get("url") or obj.get("friendlyUrl")
        if code and name and ("5070" in str(name) or "shadow" in str(name).lower()):
            found.append({"code": code, "name": str(name)[:80], "link": link})
        for v in obj.values():
            walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, depth + 1)


walk(props)
print("found gpu-ish", len(found))
for row in found[:15]:
    print(row)

# Count all product-like dicts with numeric code
codes = []


def walk2(obj, depth=0):
    if depth > 12:
        return
    if isinstance(obj, dict):
        code = obj.get("code")
        if isinstance(code, int) and (obj.get("name") or obj.get("title")):
            codes.append(code)
        for v in obj.values():
            walk2(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            walk2(v, depth + 1)


walk2(props)
print(
    "product-like codes",
    len(codes),
    codes[:20],
    "777166" in [str(c) for c in codes] or 777166 in codes,
)
