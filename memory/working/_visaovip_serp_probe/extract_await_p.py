"""Extract the full searchProducts function call context with variable meanings."""

from pathlib import Path

base = Path(r"memory\working\_visaovip_serp_probe")
text = (base / "chunk_serp3.body").read_text(encoding="utf-8", errors="replace")

# Find await p( and go back a few KB to understand context
idx = text.find("await p(")
print("=== Function calling await p() — wider context ===")
print(text[max(0, idx - 3000) : idx + 500])
