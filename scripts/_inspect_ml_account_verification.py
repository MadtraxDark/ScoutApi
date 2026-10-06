"""Inspect Mercado Livre account-verification HTML markers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from curl_cffi import requests as curl_requests

ROOT = Path(__file__).resolve().parents[1]


url = "https://lista.mercadolivre.com.br/gigabyte+rtx+5060"
r = curl_requests.get(url, impersonate="chrome", timeout=30, allow_redirects=True)
text = r.text or ""
print("final", r.url)
print("len", len(text))
for src in re.findall(r'src="([^"]+)"', text):
    low = src.lower()
    if any(
        k in low
        for k in (
            "suspicious",
            "snoopy",
            "captcha",
            "challenge",
            "bot",
            "verify",
            "pow",
        )
    ):
        print("src", src)
for mid in re.findall(r'id="([^"]+)"', text):
    low = mid.lower()
    if any(
        k in low
        for k in ("btn", "continue", "captcha", "challenge", "verify", "bot", "press")
    ):
        print("id", mid)
plain = re.sub(r"<script[\s\S]*?</script>", " ", text)
plain = re.sub(r"<style[\s\S]*?</style>", " ", plain)
plain = re.sub(r"<[^>]+>", " ", plain)
plain = re.sub(r"\s+", " ", plain)
print("plain:", plain[:1000])
