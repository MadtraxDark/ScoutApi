from __future__ import annotations

import json
from decimal import Decimal

import pytest
from scripts.bench_matching_baseline import Example, evaluate, load_dataset

from scout_api.modules.matching.identity import ProductIdentity


def _identity(*, title: str = "Produto", gtin: str | None = None) -> ProductIdentity:
    return ProductIdentity(
        gtin=gtin,
        brand="Marca",
        model="Modelo",
        title=title,
        title_normalized=title.casefold(),
    )


def _row(*, sample_id: str = "anon-1", label: str = "uncertain") -> dict[str, object]:
    identity = {
        "gtin": None,
        "brand": "Marca",
        "model": "Modelo",
        "title": "Produto",
        "title_normalized": "produto",
        "price": "12.50",
    }
    return {
        "schema_version": 1,
        "sample_id": sample_id,
        "group_id": "family-1",
        "split": "holdout",
        "label": label,
        "category": "other",
        "reference_language": "pt",
        "candidate_language": "en",
        "reference": identity,
        "candidate": identity,
    }


def test_load_dataset_parses_decimal_and_hashes_exact_bytes(tmp_path) -> None:
    path = tmp_path / "pairs.jsonl"
    content = (json.dumps(_row(), ensure_ascii=False) + "\n").encode()
    path.write_bytes(content)

    examples, digest = load_dataset(path)

    assert len(examples) == 1
    assert examples[0].reference.price == Decimal("12.50")
    assert len(digest) == 64


def test_load_dataset_accepts_calibration_split(tmp_path) -> None:
    path = tmp_path / "pairs.jsonl"
    row = _row()
    row["split"] = "calibration"
    path.write_text(json.dumps(row), encoding="utf-8")

    examples, _ = load_dataset(path)

    assert examples[0].split == "calibration"


def test_load_dataset_rejects_duplicate_ids_and_empty_files(tmp_path) -> None:
    path = tmp_path / "pairs.jsonl"
    row = json.dumps(_row())
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sample_id duplicado"):
        load_dataset(path)

    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dataset vazio"):
        load_dataset(path)


def test_load_dataset_rejects_groups_spanning_splits(tmp_path) -> None:
    path = tmp_path / "pairs.jsonl"
    first = _row(sample_id="anon-1")
    second = _row(sample_id="anon-2")
    second["split"] = "train"
    path.write_text(
        "\n".join(json.dumps(row) for row in (first, second)), encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match="group_id family-1 aparece em mais de um split"
    ):
        load_dataset(path)


def test_evaluation_excludes_uncertain_gold_from_binary_metrics() -> None:
    identity = _identity(gtin="7891991010863")
    examples = [
        Example(
            sample_id="anon-different",
            group_id="family-different",
            split="holdout",
            label="different_product",
            category="smartphone",
            reference_language="pt",
            candidate_language="en",
            reference=identity,
            candidate=identity,
        ),
        Example(
            sample_id="anon-uncertain",
            group_id="family-uncertain",
            split="holdout",
            label="uncertain",
            category="smartphone",
            reference_language="pt",
            candidate_language="en",
            reference=identity,
            candidate=identity,
        ),
    ]

    report = evaluate(examples, "holdout", "f" * 64)

    assert report["sample_count"] == 2
    assert report["decision_counts"] == {"same_product": 2}
    metrics = report["binary_same_product_metrics"]
    assert metrics["count"] == 1  # type: ignore[index]
    assert metrics["false_positive"] == 1  # type: ignore[index]
    assert metrics["f1_same_product"] is None  # type: ignore[index]
