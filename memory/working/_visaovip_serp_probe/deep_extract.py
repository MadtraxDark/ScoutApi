import re
from pathlib import Path

out = Path(
    "c:\\Users\\Thyago\\Documents\\Visual Studio Code\\"
    "ScoutApiV2\\memory\\working\\_visaovip_serp_prob"
    "e"
)

# 1) Extract Server Action / autocomplete strings from chunks
for name in [
    "chunk_serp2.body",
    "chunk_serp3.body",
    "chunk_serp1.body",
    "chunk_shared.body",
]:
    text = (out / name).read_text(encoding="utf-8", errors="replace")
    print("=" * 60, name)
    for pat in [
        r'createServerReference\("[^"]+",[^,]+,[^,]+,[^,]+,"([^"]+)"\)',
        r'findSourceMapURL,"([^"]+)"\)',
        (
            "[\"\\']([A-Za-z0-9_/.-]*(?:autocomplete|suggest"
            "|searchProducts|searchFacets|search)[A-Za-z0-"
            "9_/.-]*)[\"\\']"
        ),
        r'["\'](/[^"\']*(?:api|busca|search|auto)[^"\']*)["\']',
        r"callServer|NEXT_ACTION|next-action",
    ]:
        hits = re.findall(pat, text)
        if hits:
            uniq = sorted(
                set(
                    hits
                    if isinstance(hits[0], str)
                    else [h[0] if isinstance(h, tuple) else h for h in hits]
                )
            )
            print(pat[:50], "->", uniq[:30], "count", len(uniq))
    # show context around searchProducts
    for m in re.finditer(r".{0,120}searchProducts.{0,200}", text):
        print("CTX searchProducts:", m.group(0)[:300])
        break
    for m in re.finditer(r".{0,120}autocomplete.{0,200}", text, re.I):
        print("CTX autocomplete:", m.group(0)[:300])
        break
    for m in re.finditer(r".{0,80}searchFacets.{0,160}", text):
        print("CTX searchFacets:", m.group(0)[:240])
        break

# 2) Extract initialSearchPageData JSON-ish from SERP shells
flight_re = re.compile(r"self\.__next_f\.push\(\[1,\"((?:[^\"\\]|\\.)*)\"\]\)")
for name in [
    "serp_mb_curl_curl.txt",
    "serp_phone_curl_curl.txt",
    "serp_cpu.body",
    "serp_gpu.body",
    "serp_phone_short.body",
]:
    text = (out / name).read_text(encoding="utf-8", errors="replace")
    print("=" * 60, "FLIGHT", name)
    for m in flight_re.finditer(text):
        chunk = m.group(1).encode("utf-8").decode("unicode_escape")
        if "initialSearchPageData" in chunk:
            # print slice around it
            i = chunk.index("initialSearchPageData")
            snippet = chunk[i : i + 1200]
            print(snippet[:1200])
            # check product-ish keys
            for key in [
                "products",
                "items",
                "results",
                "total",
                "count",
                "searchModeType",
                "empty",
                "nenhum",
                "productCode",
            ]:
                if key in snippet or key in chunk[i : i + 4000]:
                    print(" KEYHIT", key, "in extended")
            # broader window
            window = chunk[i : i + 4000]
            print(
                "WINDOW_KEYS_HINTS:",
                sorted(set(re.findall(r"\"([A-Za-z_]{3,40})\"\s*:", window)))[:40],
            )
            break
    # bailout count
    print("BAILOUT count", text.count("BAILOUT_TO_CLIENT_SIDE_RENDERING"))
    print("has productCode", "productCode" in text)
    print("has /prod/ path cards", bool(re.search(r"href=\"/prod/", text)))
