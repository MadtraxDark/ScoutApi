import re
from pathlib import Path

out = Path(
    "c:\\Users\\Thyago\\Documents\\Visual Studio Code\\"
    "ScoutApiV2\\memory\\working\\_visaovip_serp_prob"
    "e"
)
text = (out / "chunk_serp3.body").read_text(encoding="utf-8", errors="replace")

# Find usages of searchProducts variable - assigned to p
# Look for patterns calling p( or searchProducts(
for m in re.finditer(r".{0,60}\bp\([^\)]{0,200}\)", text):
    s = m.group(0)
    if (
        "search" in s.lower()
        or "termo" in s.lower()
        or "page" in s.lower()
        or "filter" in s.lower()
    ):
        print("CALL?", s[:260])

# broader: extract function bodies mentioning searchProducts id or await p(
idx = text.find("searchProducts")
print("IDX", idx)
print(text[idx - 200 : idx + 2500])

# getProductFromIdList chunk
text1 = (out / "chunk_serp1.body").read_text(encoding="utf-8", errors="replace")
i = text1.find("getProductFromIdList")
print("\n=== getProductFromIdList ===")
print(text1[i - 150 : i + 1800])

# Analyze category pages vs termo for cards
for name in [
    "cat_placa_mae.body",
    "cat_notebook.body",
    "destaques.body",
    "marca_asus.body",
    "marca_samsung.body",
]:
    t = (out / name).read_text(encoding="utf-8", errors="replace")
    prods = re.findall(r"href=\"(/prod/[^\"]+/\d+/)\"", t)
    print(
        name,
        "len",
        len(t),
        "bailout",
        t.count("BAILOUT_TO_CLIENT_SIDE_RENDERING"),
        "prod_hrefs",
        len(prods),
        "productCode",
        "productCode" in t,
    )
    # initialSearchPageData
    m = re.search(r"initialSearchPageData\\\":(\{.*?\})", t)
    if not m:
        # try decoded via flight
        for fm in re.finditer(
            r"self\.__next_f\.push\(\[1,\"((?:[^\"\\]|\\.)*)\"\]\)", t
        ):
            chunk = fm.group(1)
            if "initialSearchPageData" in chunk:
                chunk = chunk.encode("utf-8").decode("unicode_escape")
                j = chunk.index("initialSearchPageData")
                print(" ", chunk[j : j + 500])
                break

# sitemap product urls
sm = (out / "sitemap0.body").read_text(encoding="utf-8", errors="replace")
print(
    "sitemap0 prod count",
    sm.count("/prod/"),
    "busca count",
    sm.count("/busca/"),
    "url count",
    sm.count("<url>"),
)
