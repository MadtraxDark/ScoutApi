import re
from pathlib import Path

text = Path(
    "c:\\Users\\Thyago\\Documents\\Visual Studio Code\\"
    "ScoutApiV2\\memory\\working\\_visaovip_serp_prob"
    "e\\chunk_serp3.body"
).read_text(encoding="utf-8", errors="replace")
# print more after await p to see response shape
i = text.find("await p(")
print(text[i : i + 2200])
print("\n==== searchModeType contexts ====")
for m in re.finditer(r".{0,100}searchModeType.{0,150}", text):
    print(m.group(0)[:250])
    print("---")
print("\n==== H( or fetchProducts call patterns ====")
for m in re.finditer(r"fetchProducts|H\(|y\([^)]{0,80}\)", text):
    if m.start() > 120000 and m.start() < 128000:
        print(m.start(), m.group(0)[:100])
