"""Deterministic precision-first product matching engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from scout_api.modules.matching.identity import (
    ProductIdentity,
    _cooler_model_signature,
    condition_conflict,
    console_soft_model_title_exempt,
    critical_identity_conflict,
    form_factor_conflict,
    gpu_soft_model_title_exempt,
    looks_like_accessory,
    looks_like_bundle,
    models_compatible,
    motherboard_soft_model_title_exempt,
    parse_offer_condition,
    processor_model_exact,
    processor_socket,
    token_set_ratio,
    variant_comparison_uncertain,
    variants_equal,
)
from scout_api.modules.matching.schemas import MatchReason

MatchDecision = Literal["auto_match", "review", "reject"]

AUTO_THRESHOLD = Decimal("0.92")
REVIEW_THRESHOLD = Decimal("0.75")
TITLE_WEIGHT = Decimal("0.35")
BRAND_MODEL_WEIGHT = Decimal("0.45")
TITLE_ONLY_CAP = Decimal("0.74")  # never reaches auto_match alone
# Soft model equality (3≡3i, stripped suffixes) still needs title support.
SOFT_MODEL_TITLE_MIN = 0.75
EXTREME_PRICE_RATIO = Decimal("4.0")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchScore:
    decision: MatchDecision
    confidence: Decimal
    reasons: tuple[MatchReason, ...]


def _carrier_review_needed(ref: ProductIdentity, cand: ProductIdentity) -> bool:
    """True when candidate is carrier-locked and reference lock is unknown."""
    return (
        not ref.variant_attrs.get("network_lock")
        and cand.variant_attrs.get("network_lock") == "locked"
    )


def _maybe_carrier_review(
    reference: ProductIdentity,
    candidate: ProductIdentity,
    reasons: list[MatchReason],
    *,
    confidence: Decimal,
) -> MatchScore | None:
    if not _carrier_review_needed(reference, candidate):
        return None
    reasons.append(
        MatchReason(
            code="carrier_variant_uncertain",
            detail="reference_lock_unknown:candidate_locked",
            score=0.5,
        )
    )
    return MatchScore(
        decision="review",
        confidence=min(confidence, Decimal("0.8800")),
        reasons=tuple(reasons),
    )


def _variant_conflict(ref: ProductIdentity, cand: ProductIdentity) -> str | None:
    for key, ref_value in ref.variant_attrs.items():
        cand_value = cand.variant_attrs.get(key)
        if cand_value and not variants_equal(key, ref_value, cand_value):
            if variant_comparison_uncertain(key, ref_value, cand_value):
                continue
            return f"variant_{key}_mismatch:{ref_value}!={cand_value}"
    return None


def _variant_uncertainties(ref: ProductIdentity, cand: ProductIdentity) -> list[str]:
    return [
        f"color:{ref_value}!={cand_value}"
        for key, ref_value in ref.variant_attrs.items()
        if (cand_value := cand.variant_attrs.get(key))
        and variant_comparison_uncertain(key, ref_value, cand_value)
    ]


def _brand_compatible(ref: ProductIdentity, cand: ProductIdentity) -> bool:
    if not ref.brand or not cand.brand:
        return True
    return ref.brand == cand.brand or ref.brand in cand.brand or cand.brand in ref.brand


def price_extreme(ref: ProductIdentity, cand: ProductIdentity) -> bool:
    if ref.price is None or cand.price is None:
        return False
    if ref.price <= 0 or cand.price <= 0:
        return False
    # Only compare same currency.
    if ref.currency and cand.currency and ref.currency != cand.currency:
        return False
    ratio = max(ref.price, cand.price) / min(ref.price, cand.price)
    return ratio >= EXTREME_PRICE_RATIO


class MatchingEngine:
    """Score a candidate listing against a reference product identity."""

    def score(
        self, reference: ProductIdentity, candidate: ProductIdentity
    ) -> MatchScore:
        reasons: list[MatchReason] = []
        variant_uncertainties = _variant_uncertainties(reference, candidate)
        ref_monitor_code = reference.monitor_model_code
        cand_monitor_code = candidate.monitor_model_code
        monitor_code_exact = bool(
            ref_monitor_code
            and cand_monitor_code
            and ref_monitor_code.casefold() == cand_monitor_code.casefold()
        )
        if ref_monitor_code and cand_monitor_code:
            if not monitor_code_exact:
                logger.info(
                    "product_match_monitor_model_code_conflict",
                    extra={
                        "source_model_code": ref_monitor_code,
                        "candidate_model_code": cand_monitor_code,
                    },
                )
                reasons.append(
                    MatchReason(
                        code="monitor_model_code_conflict",
                        detail=f"{ref_monitor_code}!={cand_monitor_code}",
                        score=0.0,
                    )
                )
                return MatchScore(
                    decision="reject",
                    confidence=Decimal("0.0000"),
                    reasons=tuple(reasons),
                )
            logger.info(
                "product_match_monitor_model_code_exact",
                extra={
                    "source_model_code": ref_monitor_code,
                    "candidate_model_code": cand_monitor_code,
                },
            )
        elif (
            ref_monitor_code
            or cand_monitor_code
            or reference.category == "monitor"
            or candidate.category == "monitor"
        ):
            logger.info(
                "product_match_monitor_model_code_fallback",
                extra={
                    "source_model_code": ref_monitor_code or "not_found",
                    "candidate_model_code": cand_monitor_code or "not_found",
                    "reason": "identifier_missing",
                },
            )

        if not monitor_code_exact:
            conflict = _variant_conflict(reference, candidate)
            if conflict:
                reasons.append(
                    MatchReason(code="variant_mismatch", detail=conflict, score=0.0)
                )
                return MatchScore(
                    decision="reject",
                    confidence=Decimal("0.0000"),
                    reasons=tuple(reasons),
                )

            critical = critical_identity_conflict(reference, candidate)
            if critical:
                if critical.startswith("processor_"):
                    logger.info(
                        "product_match_processor_conflict",
                        extra={
                            "source_cpu_model": reference.model,
                            "candidate_cpu_model": candidate.model,
                            "source_socket": processor_socket(reference) or "not_found",
                            "candidate_socket": processor_socket(candidate)
                            or "not_found",
                            "source_mpn": reference.mpn or "not_found",
                            "candidate_mpn": candidate.mpn or "not_found",
                            "conflict": critical,
                            "decision": "reject",
                        },
                    )
                reasons.append(
                    MatchReason(
                        code="critical_conflict",
                        detail=critical,
                        score=0.0,
                    )
                )
                return MatchScore(
                    decision="reject",
                    confidence=Decimal("0.0000"),
                    reasons=tuple(reasons),
                )

        if looks_like_accessory(candidate.title, reference_title=reference.title):
            reasons.append(
                MatchReason(
                    code="accessory_reject",
                    detail="candidate_title_looks_like_accessory",
                    score=0.0,
                )
            )
            return MatchScore(
                decision="reject",
                confidence=Decimal("0.0000"),
                reasons=tuple(reasons),
            )

        if looks_like_bundle(candidate.title, reference_title=reference.title):
            reasons.append(
                MatchReason(
                    code="bundle_reject",
                    detail="candidate_title_looks_like_bundle",
                    score=0.0,
                )
            )
            return MatchScore(
                decision="reject",
                confidence=Decimal("0.0000"),
                reasons=tuple(reasons),
            )

        form_conflict = form_factor_conflict(reference.title, candidate.title)
        if form_conflict:
            reasons.append(
                MatchReason(
                    code="form_factor_reject",
                    detail=form_conflict,
                    score=0.0,
                )
            )
            return MatchScore(
                decision="reject",
                confidence=Decimal("0.0000"),
                reasons=tuple(reasons),
            )

        condition = condition_conflict(reference.title, candidate.title)
        if condition:
            reasons.append(
                MatchReason(code="condition_reject", detail=condition, score=0.0)
            )
            return MatchScore(
                decision="reject",
                confidence=Decimal("0.0000"),
                reasons=tuple(reasons),
            )

        cand_condition = parse_offer_condition(candidate.title)
        if cand_condition.is_renewed_family:
            detail: str = cand_condition.code
            if cand_condition.grade:
                detail = f"{detail}:{cand_condition.grade}"
            reasons.append(
                MatchReason(
                    code="offer_condition",
                    detail=detail,
                    score=1.0,
                )
            )

        ref_lock = reference.variant_attrs.get("network_lock")
        cand_lock = candidate.variant_attrs.get("network_lock")
        # Explicit unlocked vs carrier-locked is a commercial variant conflict.
        if ref_lock == "unlocked" and cand_lock == "locked":
            reasons.append(
                MatchReason(
                    code="carrier_lock_reject",
                    detail="network_lock:unlocked!=locked",
                    score=0.0,
                )
            )
            return MatchScore(
                decision="reject",
                confidence=Decimal("0.0000"),
                reasons=tuple(reasons),
            )

        if monitor_code_exact:
            reasons.append(
                MatchReason(
                    code="monitor_model_code_exact",
                    detail=f"model_code={ref_monitor_code}",
                    score=1.0,
                )
            )
            if (
                reference.brand
                and candidate.brand
                and not _brand_compatible(reference, candidate)
            ):
                reasons.append(
                    MatchReason(
                        code="monitor_model_code_brand_conflict",
                        detail="model_code_match_but_brand_diverges",
                        score=0.5,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8500"),
                    reasons=tuple(reasons),
                )
            return MatchScore(
                decision="auto_match",
                confidence=Decimal("0.9900"),
                reasons=tuple(reasons),
            )

        # Same store + same product_id → auto
        if (
            reference.store
            and candidate.store
            and reference.store == candidate.store
            and reference.product_id
            and candidate.product_id
            and reference.product_id == candidate.product_id
        ):
            reasons.append(
                MatchReason(
                    code="same_store_product_id",
                    detail="identical_store_local_id",
                    score=1.0,
                )
            )
            return MatchScore(
                decision="auto_match",
                confidence=Decimal("1.0000"),
                reasons=tuple(reasons),
            )

        # GTIN exact (validated) — auto if variants already passed
        if reference.gtin and candidate.gtin and reference.gtin == candidate.gtin:
            reasons.append(
                MatchReason(
                    code="gtin_exact",
                    detail=f"gtin={reference.gtin}",
                    score=1.0,
                )
            )
            if (
                reference.brand
                and candidate.brand
                and not _brand_compatible(reference, candidate)
            ):
                reasons.append(
                    MatchReason(
                        code="gtin_brand_conflict",
                        detail="gtin_match_but_brand_diverges",
                        score=0.5,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8500"),
                    reasons=tuple(reasons),
                )
            carrier_review = _maybe_carrier_review(
                reference, candidate, reasons, confidence=Decimal("0.9900")
            )
            if carrier_review is not None:
                return carrier_review
            return MatchScore(
                decision="auto_match",
                confidence=Decimal("0.9900"),
                reasons=tuple(reasons),
            )

        # Manufacturer part number exact — strong identifier after GTIN.
        ref_mpns = set(reference.mpn_aliases)
        if reference.mpn:
            ref_mpns.add(reference.mpn)
        cand_mpns = set(candidate.mpn_aliases)
        if candidate.mpn:
            cand_mpns.add(candidate.mpn)
        shared_mpns = ref_mpns & cand_mpns
        if shared_mpns:
            shared = sorted(shared_mpns)[0]
            reasons.append(
                MatchReason(
                    code="mpn_exact",
                    detail=f"mpn={shared}",
                    score=1.0,
                )
            )
            if (
                reference.brand
                and candidate.brand
                and not _brand_compatible(reference, candidate)
            ):
                reasons.append(
                    MatchReason(
                        code="mpn_brand_conflict",
                        detail="mpn_match_but_brand_diverges",
                        score=0.5,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8500"),
                    reasons=tuple(reasons),
                )
            return MatchScore(
                decision="auto_match",
                confidence=Decimal("0.9800"),
                reasons=tuple(reasons),
            )

        shared_model_numbers = reference.model_numbers & candidate.model_numbers
        if shared_model_numbers:
            shared = sorted(shared_model_numbers)[0]
            reasons.append(
                MatchReason(
                    code="model_number_exact",
                    detail=f"model_number={shared}",
                    score=1.0,
                )
            )
            if variant_uncertainties:
                reasons.append(
                    MatchReason(
                        code="variant_semantic_uncertain",
                        detail=";".join(variant_uncertainties),
                        score=0.5,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8500"),
                    reasons=tuple(reasons),
                )
            if (
                reference.brand
                and candidate.brand
                and not _brand_compatible(reference, candidate)
            ):
                reasons.append(
                    MatchReason(
                        code="model_number_brand_conflict",
                        detail="model_number_match_but_brand_diverges",
                        score=0.5,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8500"),
                    reasons=tuple(reasons),
                )
            return MatchScore(
                decision="auto_match",
                confidence=Decimal("0.9800"),
                reasons=tuple(reasons),
            )

        cpu_model_exact = bool(
            reference.category == candidate.category == "cpu"
            and processor_model_exact(reference, candidate)
        )
        if cpu_model_exact:
            logger.info(
                "product_match_processor_model_exact",
                extra={
                    "source_cpu_model": reference.model,
                    "candidate_cpu_model": candidate.model,
                    "source_socket": processor_socket(reference) or "not_found",
                    "candidate_socket": processor_socket(candidate) or "not_found",
                    "source_mpn": reference.mpn or "not_found",
                    "candidate_mpn": candidate.mpn or "not_found",
                    "socket_conflict": False,
                    "decision": "auto_match",
                },
            )
            reasons.append(
                MatchReason(
                    code="processor_model_exact",
                    detail=f"{reference.model or 'title'}~{candidate.model or 'title'}",
                    score=1.0,
                )
            )
            if (
                reference.brand
                and candidate.brand
                and not _brand_compatible(reference, candidate)
            ):
                reasons.append(
                    MatchReason(
                        code="processor_brand_conflict",
                        detail=f"{reference.brand}!={candidate.brand}",
                        score=0.0,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8500"),
                    reasons=tuple(reasons),
                )
            return MatchScore(
                decision="auto_match",
                confidence=Decimal("0.9800"),
                reasons=tuple(reasons),
            )

        confidence = Decimal("0.0000")
        has_strong_id = False
        monitor_spec_support = False

        if (
            reference.brand
            and candidate.brand
            and _brand_compatible(reference, candidate)
        ):
            reasons.append(
                MatchReason(code="brand_match", detail=reference.brand, score=1.0)
            )
            confidence += Decimal("0.25")
        elif reference.brand and candidate.brand:
            reasons.append(
                MatchReason(
                    code="brand_mismatch",
                    detail=f"{reference.brand}!={candidate.brand}",
                    score=0.0,
                )
            )
            return MatchScore(
                decision="reject",
                confidence=Decimal("0.0000"),
                reasons=tuple(reasons),
            )

        if reference.category == candidate.category == "monitor":
            monitor_keys = ("screen_size", "resolution", "refresh_rate", "panel")
            shared_specs = [
                key
                for key in monitor_keys
                if reference.variant_attrs.get(key)
                and candidate.variant_attrs.get(key)
                and reference.variant_attrs[key] == candidate.variant_attrs[key]
            ]
            if shared_specs:
                reasons.append(
                    MatchReason(
                        code="monitor_specs_agree",
                        detail=f"attributes={','.join(shared_specs)}",
                        score=len(shared_specs) / len(monitor_keys),
                    )
                )
                confidence += Decimal("0.11") * len(shared_specs)
                monitor_spec_support = len(shared_specs) >= 3

        title_sim = token_set_ratio(reference.title, candidate.title)
        cooler_model_exact = bool(
            reference.category == candidate.category == "cooler"
            and _cooler_model_signature(reference)
            and _cooler_model_signature(reference) == _cooler_model_signature(candidate)
        )
        model_soft_ok = bool(
            reference.model
            and candidate.model
            and models_compatible(
                reference.model,
                candidate.model,
                left_title=reference.title,
                right_title=candidate.title,
            )
            and reference.brand
            and candidate.brand
            and _brand_compatible(reference, candidate)
        )
        if cooler_model_exact and reference.brand and candidate.brand:
            model_soft_ok = _brand_compatible(reference, candidate)
        if model_soft_ok and reference.model != candidate.model:
            # Consoles / GPUs: sparse vs marketing titles must not veto family
            # identity when critical edition/VRAM/storage gates already agree.
            if title_sim < SOFT_MODEL_TITLE_MIN and not (
                console_soft_model_title_exempt(reference, candidate)
                or gpu_soft_model_title_exempt(reference, candidate)
                or motherboard_soft_model_title_exempt(reference, candidate)
                or cpu_model_exact
                or cooler_model_exact
            ):
                model_soft_ok = False

        if model_soft_ok:
            reasons.append(
                MatchReason(
                    code="brand_model_exact",
                    detail=f"{reference.brand}:{reference.model}~{candidate.model}",
                    score=1.0,
                )
            )
            confidence += BRAND_MODEL_WEIGHT
            has_strong_id = True
            # Brand+model+compatible variants is high confidence.
            if confidence < AUTO_THRESHOLD:
                confidence = AUTO_THRESHOLD
            if title_sim > 0:
                reasons.append(
                    MatchReason(
                        code="title_similarity",
                        detail=f"token_set_ratio={title_sim:.3f}",
                        score=title_sim,
                    )
                )
            if variant_uncertainties:
                reasons.append(
                    MatchReason(
                        code="variant_semantic_uncertain",
                        detail=";".join(variant_uncertainties),
                        score=0.5,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=min(confidence, Decimal("0.8900")),
                    reasons=tuple(reasons),
                )
            if price_extreme(reference, candidate):
                reasons.append(
                    MatchReason(
                        code="price_deviation",
                        detail="extreme_price_ratio",
                        score=0.0,
                    )
                )
                return MatchScore(
                    decision="review",
                    confidence=Decimal("0.8800"),
                    reasons=tuple(reasons),
                )
            # Carrier-locked candidate vs unspecified reference → review, not
            # silent auto_match as unlocked-equivalent.
            carrier_review = _maybe_carrier_review(
                reference, candidate, reasons, confidence=confidence
            )
            if carrier_review is not None:
                return carrier_review
            return MatchScore(
                decision="auto_match",
                confidence=min(confidence, Decimal("0.9700")),
                reasons=tuple(reasons),
            )

        if title_sim > 0:
            reasons.append(
                MatchReason(
                    code="title_similarity",
                    detail=f"token_set_ratio={title_sim:.3f}",
                    score=title_sim,
                )
            )
            confidence += (TITLE_WEIGHT * Decimal(str(round(title_sim, 4)))).quantize(
                Decimal("0.0001")
            )

        # Title alone must never auto-match.
        if (
            not has_strong_id
            and not (reference.gtin and candidate.gtin)
            and not monitor_spec_support
        ):
            confidence = min(confidence, TITLE_ONLY_CAP)

        if price_extreme(reference, candidate):
            reasons.append(
                MatchReason(
                    code="price_deviation",
                    detail="extreme_price_ratio",
                    score=0.0,
                )
            )
            if confidence >= AUTO_THRESHOLD:
                confidence = REVIEW_THRESHOLD

        confidence = min(confidence, Decimal("1.0000")).quantize(Decimal("0.0001"))

        if has_strong_id and confidence >= AUTO_THRESHOLD:
            decision: MatchDecision = "auto_match"
        elif has_strong_id and confidence >= REVIEW_THRESHOLD:
            decision = "review"
        elif confidence >= AUTO_THRESHOLD and has_strong_id:
            decision = "auto_match"
        elif confidence >= REVIEW_THRESHOLD:
            decision = "review"
        else:
            decision = "reject"

        # Extra safety: without brand+model or GTIN, never auto.
        if decision == "auto_match" and not has_strong_id:
            decision = "review"
            confidence = min(confidence, Decimal("0.8900"))

        if variant_uncertainties and decision == "reject":
            reasons.append(
                MatchReason(
                    code="variant_semantic_uncertain",
                    detail=";".join(variant_uncertainties),
                    score=0.5,
                )
            )
            decision = "review"
            confidence = max(confidence, REVIEW_THRESHOLD)

        return MatchScore(
            decision=decision, confidence=confidence, reasons=tuple(reasons)
        )
