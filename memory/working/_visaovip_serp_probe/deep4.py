from pathlib import Path

text = Path(
    "c:\\Users\\Thyago\\Documents\\Visual Studio Code\\"
    "ScoutApiV2\\memory\\working\\_visaovip_serp_prob"
    "e\\chunk_serp3.body"
).read_text(encoding="utf-8", errors="replace")
# Around H( usage near 126049
print(text[125800:127200])
print("\n==== around z useMemo / useEffect that calls H ====")
# find H( after fetchProducts definition
idx = text.find("products:l?.products||[]")
print(text[idx : idx + 2500])
