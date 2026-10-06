import re
from pathlib import Path

t = Path("tests/fixtures/visaovip/product_motherboard.html").read_text(
    encoding="utf-8", errors="replace"
)
print("has MPN string", "TUF-GAMING-B650M-E-WIFI" in t.upper())
print("REFER count", len(re.findall(r"REFER", t, re.I)))
# Extract productSpecifications-ish pairs from escaped JSON
names = re.findall(r"specificationName\\?\":\\?\"([^\"]+)\\?\"", t)
print("spec names", names[:30], "n=", len(names))
