from pathlib import Path

p = Path("tests/unit/test_matching_regression.py")
text = p.read_text(encoding="utf-8")
marker = "def test_cpu_x3d_suffix_rejects_base_sku() -> None:"
idx = text.find(marker)
if idx < 0:
    raise SystemExit("marker not found")
prefix = text[:idx]
new_tail = """def test_cpu_x3d_suffix_rejects_base_sku() -> None:
    score = MatchingEngine().score(
        _identity(
            brand="amd",
            model="ryzen75800x3d",
            title="AMD Ryzen 7 5800X3D",
        ),
        _identity(
            brand="amd",
            model="ryzen75800x",
            title="AMD Ryzen 7 5800X",
        ),
    )
    assert score.decision == "reject"


def test_motherboard_parse_model_excludes_socket_marketing_tail() -> None:
    from scout_api.modules.crawler.utils.category_profiles.extra_parsers import (
        parse_motherboard,
    )

    parsed = parse_motherboard(_MB_LONG_TITLE, "motherboard")
    model = (parsed.model or "").casefold()
    assert "b650m" in model
    assert "socket" not in model
    assert "chipset" not in model
    assert "ddr5" not in model
    assert "m-atx" not in model and "matx" not in model.replace(" ", "")
"""
p.write_text(prefix + new_tail, encoding="utf-8")
print("ok", len(prefix + new_tail))
