"""Run the deterministic Product Match engine against a reviewed JSONL set."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from scout_api.modules.matching.engine import MatchingEngine
from scout_api.modules.matching.identity import ProductIdentity

Label = Literal["same_product", "different_product", "uncertain"]
Split = Literal["development", "train", "calibration", "validation", "holdout"]
DECISIONS = ("auto_match", "reject", "review")
PREDICTION_BY_DECISION = {
    "auto_match": "same_product",
    "reject": "different_product",
    "review": "uncertain",
}


@dataclass(frozen=True)
class Example:
    sample_id: str
    group_id: str
    split: Split
    label: Label
    category: str
    reference_language: str
    candidate_language: str
    reference: ProductIdentity
    candidate: ProductIdentity


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} deve ser texto não vazio")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} deve ser texto ou null")
    return value


def _text_set(value: object, field: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} deve ser uma lista de textos")
    return frozenset(value)


def _identity(value: object, field: str) -> ProductIdentity:
    if not isinstance(value, dict):
        raise ValueError(f"{field} deve ser um objeto")
    allowed = {
        "gtin",
        "brand",
        "model",
        "title",
        "title_normalized",
        "variant_attrs",
        "store",
        "product_id",
        "price",
        "currency",
        "mpn",
        "mpn_display",
        "mpn_aliases",
        "model_numbers",
        "monitor_model_code",
        "category",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field} contém campos desconhecidos: {sorted(unknown)}")

    variants = value.get("variant_attrs", {})
    if not isinstance(variants, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in variants.items()
    ):
        raise ValueError(f"{field}.variant_attrs deve mapear textos para textos")

    raw_price = value.get("price")
    if raw_price is None:
        price = None
    elif isinstance(raw_price, (str, int, Decimal)) and not isinstance(raw_price, bool):
        try:
            price = Decimal(str(raw_price))
        except InvalidOperation as exc:
            raise ValueError(f"{field}.price deve ser decimal ou null") from exc
        if not price.is_finite():
            raise ValueError(f"{field}.price deve ser finito")
    else:
        raise ValueError(f"{field}.price deve ser decimal ou null")

    return ProductIdentity(
        gtin=_optional_text(value.get("gtin"), f"{field}.gtin"),
        brand=_optional_text(value.get("brand"), f"{field}.brand"),
        model=_optional_text(value.get("model"), f"{field}.model"),
        title=_required_text(value.get("title"), f"{field}.title"),
        title_normalized=_required_text(
            value.get("title_normalized"), f"{field}.title_normalized"
        ),
        variant_attrs=variants,
        store=_optional_text(value.get("store"), f"{field}.store"),
        product_id=_optional_text(value.get("product_id"), f"{field}.product_id"),
        price=price,
        currency=_optional_text(value.get("currency"), f"{field}.currency"),
        mpn=_optional_text(value.get("mpn"), f"{field}.mpn"),
        mpn_display=_optional_text(value.get("mpn_display"), f"{field}.mpn_display"),
        mpn_aliases=_text_set(value.get("mpn_aliases"), f"{field}.mpn_aliases"),
        model_numbers=_text_set(value.get("model_numbers"), f"{field}.model_numbers"),
        monitor_model_code=_optional_text(
            value.get("monitor_model_code"), f"{field}.monitor_model_code"
        ),
        category=_optional_text(value.get("category"), f"{field}.category"),
    )


def load_dataset(path: Path) -> tuple[list[Example], str]:
    """Load and validate JSONL without evaluating code or normalizing labels."""
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    examples: list[Example] = []
    seen_ids: set[str] = set()
    group_splits: dict[str, Split] = {}
    for line_number, line in enumerate(content.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_float=Decimal)
            if not isinstance(row, dict):
                raise ValueError("cada linha deve conter um objeto JSON")
            allowed = {
                "schema_version",
                "sample_id",
                "group_id",
                "split",
                "label",
                "category",
                "reference_language",
                "candidate_language",
                "provenance",
                "label_basis",
                "reference",
                "candidate",
            }
            unknown = set(row) - allowed
            if unknown:
                raise ValueError(f"campos desconhecidos: {sorted(unknown)}")
            if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
                raise ValueError("schema_version deve ser 1")
            sample_id = _required_text(row.get("sample_id"), "sample_id")
            if sample_id in seen_ids:
                raise ValueError(f"sample_id duplicado: {sample_id}")
            seen_ids.add(sample_id)
            group_id = _required_text(row.get("group_id"), "group_id")
            split = row.get("split")
            if split not in (
                "development",
                "train",
                "calibration",
                "validation",
                "holdout",
            ):
                raise ValueError(
                    "split deve ser development, train, calibration, validation "
                    "ou holdout"
                )
            previous_split = group_splits.setdefault(group_id, split)
            if previous_split != split:
                raise ValueError(f"group_id {group_id} aparece em mais de um split")
            label = row.get("label")
            if label not in ("same_product", "different_product", "uncertain"):
                raise ValueError(
                    "label deve ser same_product, different_product ou uncertain"
                )
            provenance = row.get("provenance")
            label_basis = row.get("label_basis")
            if provenance is not None and provenance not in (
                "live_match_run",
                "live_public_listings",
                "regression_fixture",
                "synthetic",
            ):
                raise ValueError("provenance não reconhecida")
            if label_basis is not None and not isinstance(label_basis, str):
                raise ValueError("label_basis deve ser texto")
            examples.append(
                Example(
                    sample_id=sample_id,
                    group_id=group_id,
                    split=split,
                    label=label,
                    category=_required_text(row.get("category"), "category"),
                    reference_language=_required_text(
                        row.get("reference_language"), "reference_language"
                    ),
                    candidate_language=_required_text(
                        row.get("candidate_language"), "candidate_language"
                    ),
                    reference=_identity(row.get("reference"), "reference"),
                    candidate=_identity(row.get("candidate"), "candidate"),
                )
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"linha {line_number}: {exc}") from exc
    if not examples:
        raise ValueError("dataset vazio; adicione pares revisados antes do benchmark")
    return examples, digest


def _metrics(rows: Sequence[tuple[str, str]]) -> dict[str, int | float | None]:
    tp = sum(
        actual == "same_product" and predicted == "same_product"
        for actual, predicted in rows
    )
    fp = sum(
        actual == "different_product" and predicted == "same_product"
        for actual, predicted in rows
    )
    fn = sum(
        actual == "same_product" and predicted != "same_product"
        for actual, predicted in rows
    )
    tn = sum(
        actual == "different_product" and predicted != "same_product"
        for actual, predicted in rows
    )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "count": len(rows),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision_auto_match": precision,
        "recall_same_product": recall,
        "f1_same_product": f1,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
    }


def evaluate(
    examples: list[Example], split: Split, dataset_sha256: str
) -> dict[str, object]:
    engine = MatchingEngine()
    selected = [example for example in examples if example.split == split]
    if not selected:
        raise ValueError(f"dataset não contém amostras no split {split}")

    evaluated: list[tuple[Example, str]] = []
    case_results: list[dict[str, object]] = []
    for example in selected:
        score = engine.score(example.reference, example.candidate)
        prediction = PREDICTION_BY_DECISION[score.decision]
        evaluated.append((example, prediction))
        case_results.append(
            {
                "sample_id": example.sample_id,
                "label": example.label,
                "prediction": prediction,
                "decision": score.decision,
                "confidence": str(score.confidence),
                "reason_codes": [reason.code for reason in score.reasons],
            }
        )

    binary_rows = [
        (example.label, prediction)
        for example, prediction in evaluated
        if example.label != "uncertain"
    ]
    confusion = {
        label: {
            prediction: sum(
                example.label == label and actual_prediction == prediction
                for example, actual_prediction in evaluated
            )
            for prediction in PREDICTION_BY_DECISION.values()
        }
        for label in PREDICTION_BY_DECISION.values()
    }
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for example, prediction in evaluated:
        if example.label != "uncertain":
            groups[f"category:{example.category}"].append((example.label, prediction))
            language_pair = f"{example.reference_language}-{example.candidate_language}"
            groups[f"language_pair:{language_pair}"].append((example.label, prediction))

    return {
        "schema_version": 1,
        "dataset_sha256": dataset_sha256,
        "split": split,
        "sample_count": len(selected),
        "label_counts": dict(Counter(example.label for example in selected)),
        "decision_counts": dict(Counter(prediction for _, prediction in evaluated)),
        "confusion_matrix": confusion,
        "binary_same_product_metrics": _metrics(binary_rows),
        "cases": case_results,
        "groups": {key: _metrics(value) for key, value in sorted(groups.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", required=True, type=Path, help="arquivo JSONL revisado"
    )
    parser.add_argument(
        "--split",
        choices=("development", "train", "calibration", "validation", "holdout"),
        default="holdout",
    )
    args = parser.parse_args()
    try:
        examples, digest = load_dataset(args.dataset)
        report = evaluate(examples, args.split, digest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
