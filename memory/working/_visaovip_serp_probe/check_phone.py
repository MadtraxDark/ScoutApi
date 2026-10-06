import re
from pathlib import Path

phone = Path(
    "c:\\Users\\Thyago\\Documents\\Visual Studio Code\\"
    "ScoutApiV2\\tests\\fixtures\\visaovip\\product_ph"
    "one.html"
).read_text(encoding="utf-8", errors="replace")
print("phone fixture len", len(phone))
print("productCode hits", len(re.findall(r"productCode", phone)))
for key in ["Galaxy", "Samsung", "iPhone", "Smartphone", "celular"]:
    print(key, phone.lower().count(key.lower()))
codes = re.findall(r"productCode\\\":(\d+)", phone) or re.findall(
    r"productCode\":(\d+)", phone
)
print("codes", codes[:8])
# title
m = re.search(r"<title[^>]*>([^<]+)</title>", phone, re.I)
print("title", m.group(1)[:120] if m else None)

serp = Path(
    "c:\\Users\\Thyago\\Documents\\Visual Studio Code\\"
    "ScoutApiV2\\memory\\working\\_visaovip_serp_prob"
    "e\\serp_phone_curl_curl.txt"
).read_text(encoding="utf-8", errors="replace")
print(
    "phone SERP empty markers",
    any(
        x in serp.casefold()
        for x in (
            "nenhum resultado",
            "não encontramos",
            "nao encontramos",
            "sin resultados",
            "0 resultados",
        )
    ),
)
print("phone SERP BAILOUT", serp.count("BAILOUT_TO_CLIENT_SIDE_RENDERING"))
print("phone SERP initialSearchPageDataNotFound")
print("false" in serp and "initialSearchPageDataNotFound" in serp)
