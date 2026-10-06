"""Locate Magento swatch jsonConfig payload in Nissei HTML."""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path("data/nissei_variant_diag")


def try_extract_json_object(text: str, start: int) -> tuple[dict | None, int]:
    """Extract a JSON object starting at the first '{' after start."""
    brace = text.find("{", start)
    if brace < 0:
        return None, -1
    depth = 0
    in_str = False
    escape = False
    for i in range(brace, min(len(text), brace + 2_000_000)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[brace : i + 1]
                try:
                    return json.loads(raw), i + 1
                except json.JSONDecodeError:
                    # Magento sometimes uses unquoted keys — bail
                    return None, i + 1
    return None, -1


def summarize_config(cfg: dict, label: str) -> None:
    print("CONFIG", label, "keys", list(cfg.keys())[:40])
    attrs = cfg.get("attributes")
    if isinstance(attrs, dict):
        for aid, ainfo in attrs.items():
            if not isinstance(ainfo, dict):
                continue
            opts = ainfo.get("options") or []
            print(
                " attr",
                aid,
                ainfo.get("code"),
                ainfo.get("label"),
                "n_opts",
                len(opts) if isinstance(opts, list) else "?",
            )
            if isinstance(opts, list):
                for opt in opts[:12]:
                    if isinstance(opt, dict):
                        print(
                            "  ",
                            opt.get("id"),
                            opt.get("label"),
                            "products",
                            len(opt.get("products") or []),
                            (opt.get("products") or [])[:3],
                        )
    print(" defaultValues", cfg.get("defaultValues"))
    print(" productId", cfg.get("productId"))
    index = cfg.get("index")
    if isinstance(index, dict):
        print(" index size", len(index), "sample", list(index.items())[:3])
    skus = cfg.get("skus")
    if isinstance(skus, dict):
        print(" skus size", len(skus), "sample", list(skus.items())[:5])
    prices = cfg.get("optionPrices")
    if isinstance(prices, dict):
        print(" optionPrices size", len(prices), "sample", list(prices.items())[:2])
    # names / salable?
    for key in ("names", "sku", "optionPrices", "images", "salable"):
        if key in cfg and key not in {"optionPrices"}:
            val = cfg[key]
            print(
                " ",
                key,
                type(val).__name__,
                (len(val) if hasattr(val, "__len__") else val),
            )


def main() -> None:
    for i, label in enumerate(["A", "B"]):
        text = (OUT / f"page_{i}.html").read_text(encoding="utf-8")
        print("=" * 50, label)

        # Find swatch-opt-* containers and nearby scripts
        for m in re.finditer(r'id="(swatch-opt-\d+)"', text):
            print("swatch container", m.group(1), "at", m.start())
            window = text[m.start() : m.start() + 500]
            print(" window", window[:300])

        # Search for '"attributes":{' near product config patterns
        needles = [
            '"attributes":{',
            '"attributes": {',
            "jsonConfig",
            '"defaultValues"',
            '"optionPrices"',
            "selectedProductId",
            "cacheKey",
        ]
        for needle in needles:
            positions = [m.start() for m in re.finditer(re.escape(needle), text)]
            print(needle, "count", len(positions), "first", positions[:3])

        # Magento often embeds config as: "Magento_Swatches/js/swatch-renderer":
        # {"jsonConfig": ...}
        for m in re.finditer(r"Magento_Swatches/js/swatch-renderer", text):
            start = m.start()
            print("renderer ref at", start)
            cfg, end = try_extract_json_object(text, start)
            if cfg:
                path = OUT / f"swatch_renderer_near_{i}.json"
                path.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2)[:500000],
                    encoding="utf-8",
                )
                print(" extracted object keys", list(cfg.keys())[:20], "->", path)
                # dig for nested jsonConfig
                _blob = json.dumps(cfg)
                if "attributes" in cfg:
                    summarize_config(cfg, f"{label}-direct")
                else:
                    # walk
                    stack = [cfg]
                    while stack:
                        cur = stack.pop()
                        if isinstance(cur, dict):
                            if "attributes" in cur and "productId" in cur:
                                summarize_config(cur, f"{label}-nested")
                                (OUT / f"jsonConfig_{i}.json").write_text(
                                    json.dumps(cur, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )
                                break
                            stack.extend(cur.values())
                        elif isinstance(cur, list):
                            stack.extend(cur)

        # Also try: script tags that contain '"attributes"' and 'optionPrices'
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", text, flags=re.I | re.S)
        print("script blocks", len(scripts))
        for si, body in enumerate(scripts):
            if '"optionPrices"' in body and '"attributes"' in body:
                print("candidate script", si, "len", len(body))
                # find the jsonConfig object
                _idx = body.find('"attributes"')
                # walk backwards to nearest {
                # Better: find '"jsonConfig":'
                j = body.find('"jsonConfig"')
                if j < 0:
                    j = body.find("jsonConfig")
                if j >= 0:
                    cfg, _ = try_extract_json_object(body, j)
                    if cfg and "attributes" in cfg:
                        summarize_config(cfg, f"{label}-script-{si}")
                        (OUT / f"jsonConfig_{i}.json").write_text(
                            json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    elif cfg:
                        # maybe wrapper
                        for key, val in cfg.items():
                            if isinstance(val, dict) and "attributes" in val:
                                summarize_config(val, f"{label}-script-{si}-{key}")
                                (OUT / f"jsonConfig_{i}.json").write_text(
                                    json.dumps(val, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )

        # RequireJS text! or initConfig patterns used by Hyvä / custom themes
        for m in re.finditer(r"initConfig\((\{.*?\})\)", text, flags=re.S):
            raw = m.group(1)
            if "optionPrices" in raw or "attributes" in raw:
                print("initConfig hit len", len(raw))

        # Check for Amasty / custom selected defaults in JS
        for m in re.finditer(r"selectedOptions\s*=\s*\{", text):
            print(
                "selectedOptions assign at",
                m.start(),
                text[m.start() : m.start() + 200],
            )

        # For URL B: look for simple product meta / related configurable parent
        for m in re.finditer(
            r"parent.?product|parentId|parent_id|configurable", text, flags=re.I
        ):
            if m.start() < 400000:  # skip minified noise somewhat
                snippet = text[m.start() : m.start() + 80]
                if "function" in snippet or "prototype" in snippet:
                    continue
                print("parent-ish", m.group(0), repr(snippet[:80]))
                break


if __name__ == "__main__":
    main()
