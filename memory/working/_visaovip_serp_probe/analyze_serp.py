"""Offline analysis of Visão VIP SERP shells / chunks (no ScoutApiV2 mutation)."""

from __future__ import annotations

import re
from pathlib import Path

OUT = Path(__file__).resolve().parent

MARKERS = [
    "__NEXT_DATA__",
    "self.__next_f.push",
    "BAILOUT_TO_CLIENT_SIDE_RENDERING",
    "/prod/",
    "nenhum resultado",
    "não encontramos",
    "nao encontramos",
    "sin resultados",
    "0 resultados",
    "Resultados para",
    "Attention Required",
    "Just a moment",
    "captcha",
    "productCode",
    "autocomplete",
    "busca/termo",
    "Código:",
    "Codigo:",
    "x-nextjs-postponed",
]

URL_PAT = re.compile(r"https?://[^\"'\s<>]+|/api/[^\"'\s<>]+|/busca/[^\"'\s<>]+")
APIISH = re.compile(
    r"(autocomplete|suggest|search|busca|/api/|graphql|algolia|typesense|"
    r"meilisearch|elastic|produto|product)",
    re.I,
)
PROD_PATH = re.compile(r"/prod/[^\"'\s<>]+/\d+/")
TITLE_RE = re.compile(r"<title[^>]*>([^<]*)</title>", re.I)
FLIGHT_RE = re.compile(r"self\.__next_f\.push\(\[1,\"((?:[^\"\\]|\\.)*)\"\]\)")
STRING_RE = re.compile(r'["\'](/[^"\']{3,120}|https?://[^"\']{5,160})["\']')


def analyze_text(name: str, text: str) -> None:
    found = [m for m in MARKERS if m.lower() in text.lower()]
    titles = TITLE_RE.findall(text)[:1]
    prods = sorted(set(PROD_PATH.findall(text)))
    next_f = len(re.findall(r"self\.__next_f\.push", text))
    urls = sorted({u for u in URL_PAT.findall(text) if APIISH.search(u)})
    strings = sorted(
        {m.group(1) for m in STRING_RE.finditer(text) if APIISH.search(m.group(1))}
    )
    flight_hits: list[str] = []
    for m in FLIGHT_RE.finditer(text):
        chunk = m.group(1)
        if re.search(
            r"busca|product|result|termo|autocomplete|/prod/|nenhum", chunk, re.I
        ):
            sample = chunk[:260].replace("\\n", " | ").replace('\\"', '"')
            flight_hits.append(sample)
            if len(flight_hits) >= 8:
                break
    print("=" * 60)
    print(name, "len=", len(text))
    print("title=", titles[0].strip() if titles else None)
    print("markers=", found)
    print("next_f_push=", next_f, "prod_paths=", len(prods))
    if prods[:8]:
        print("prod_sample=", prods[:8])
    if urls:
        print("apiish_urls=", urls[:25])
    if strings:
        print("apiish_strings=", strings[:40])
    if flight_hits:
        print("flight_samples:")
        for h in flight_hits:
            print(" -", h[:220])


def main() -> None:
    patterns = ("*.body", "*_curl.txt", "serp_*.html", "phone_*.html", "mb_*.html")
    seen: set[Path] = set()
    for pat in patterns:
        for path in sorted(OUT.glob(pat)):
            if path in seen or path.name.endswith(".meta.txt") or path.suffix == ".py":
                continue
            seen.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            if text.startswith("STATUS=") or len(text) < 80:
                continue
            analyze_text(path.name, text)


if __name__ == "__main__":
    main()
