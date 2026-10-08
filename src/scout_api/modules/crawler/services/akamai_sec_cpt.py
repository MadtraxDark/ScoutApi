"""Akamai sec-cpt parse and crypto proof-of-work (ADR 0017).

The crypto/adaptive answer is the public hashcash-style check: SHA-256 of
``sec + timestamp + nonce + difficulty + answer`` must be divisible by
``difficulty``. Behavioral challenges are not forged here — the browser
session runs the page script and pointer telemetry. Clearance is the
``sec_cpt`` cookie containing ``~3~`` plus a document that is no longer
the interstitial. No static token, cookie, or ``_abck`` is embedded.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_MAX_ANSWERS = 8
_MAX_HASHES = 400_000
_CHALLENGE_ATTR_RE = re.compile(
    r"""challenge\s*=\s*["']([^"']+)["']""",
    re.I,
)
_DURATION_ATTR_RE = re.compile(
    r"""data-duration\s*=\s*["']?(\d+)""",
    re.I,
)
_PROVIDER_ATTR_RE = re.compile(
    r"""\bprovider\s*=\s*["']([a-z]+)["']""",
    re.I,
)

_POW_PROVIDERS = frozenset({"crypto", "adaptive"})


@dataclass(frozen=True, slots=True)
class SecCptChallenge:
    """Fields read from the live interstitial. Nothing here is a saved secret."""

    provider: str
    token: str
    timestamp: int
    nonce: str
    difficulty: int
    count: int
    duration_s: int
    needs_proof_of_work: bool


def sec_cpt_prefix(cookie_value: str) -> str | None:
    """Leading segment of ``sec_cpt`` (the PoW ``sec`` input)."""
    value = (cookie_value or "").strip()
    prefix, sep, _rest = value.partition("~")
    if not sep or not prefix:
        return None
    return prefix


def sec_cpt_satisfied(cookie_value: str) -> bool:
    """True when Akamai marked the challenge solved (``~3~`` segment)."""
    return "~3~" in (cookie_value or "")


def parse_sec_cpt(body: str) -> SecCptChallenge | None:
    """Parse an HTML iframe challenge or a JSON ``sec-cp-challenge`` body."""
    text = body or ""
    if not text:
        return None
    stripped = text.lstrip()
    if stripped.startswith("{") and "sec-cp-challenge" in stripped[:4_000]:
        parsed = _from_mapping(_loads_object(stripped))
        if parsed is not None:
            return parsed
    if "challenge=" not in text.casefold() and "sec-cp-challenge" not in text:
        return None
    payload = _challenge_payload(text)
    if payload is None:
        return None
    provider = (str(payload.get("provider") or "")).strip().casefold()
    if not provider:
        match = _PROVIDER_ATTR_RE.search(text)
        provider = match.group(1).casefold() if match else ""
    duration = _int_field(payload.get("chlg_duration"))
    if duration <= 0:
        duration_match = _DURATION_ATTR_RE.search(text)
        if duration_match:
            duration = _int_field(duration_match.group(1))
    return _from_mapping(payload, provider=provider, duration_s=duration)


def generate_sec_cpt_answers(
    *,
    sec: str,
    timestamp: int,
    nonce: str,
    difficulty: int,
    count: int,
) -> list[str] | None:
    """Search answers until each digest is divisible by the running difficulty.

    Returns ``None`` when the parameters cannot be solved inside the hash cap.
    """
    if not sec or difficulty <= 0 or count <= 0:
        return None
    needed = min(count, _MAX_ANSWERS)
    answers: list[str] = []
    current = difficulty
    attempts = 0
    while len(answers) < needed:
        if attempts >= _MAX_HASHES:
            logger.info(
                "akamai_sec_cpt_pow_capped",
                extra={"difficulty": difficulty, "found": len(answers)},
            )
            return None
        attempts += 1
        answer = "0." + os.urandom(8).hex()
        material = f"{sec}{timestamp}{nonce}{current}{answer}"
        digest = hashlib.sha256(material.encode("ascii")).digest()
        if int.from_bytes(digest, "big") % current == 0:
            answers.append(answer)
            current += 1
    return answers


def build_sec_cpt_payload(token: str, answers: list[str]) -> str:
    return json.dumps({"token": token, "answers": answers})


def fetch_after_http_crypto_sec_cpt(
    url: str,
    *,
    timeout: float = 20.0,
    max_wait_s: int = 12,
) -> Any | None:
    """Same-session HTTP impersonation for a crypto/adaptive sec-cpt only.

    A fresh ``curl_cffi`` session GETs the URL, waits the server duration when
    it fits the budget, POSTs the proof, verifies, and GETs the URL again.
    Behavioral challenges return ``None`` so the browser path can run the
    sensor. The returned response is not checked for a real document — the
    caller must reject a body that is still an interstitial.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        logger.info("akamai_sec_cpt_http_unavailable")
        return None

    from scrapy.http import HtmlResponse, Request

    from .html_fetcher import accept_language_for_url

    try:
        session = curl_requests.Session(impersonate="chrome")
    except Exception:
        logger.info("akamai_sec_cpt_http_session_failed", exc_info=True)
        return None

    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": accept_language_for_url(url),
    }
    try:
        probed = session.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception:
        logger.info("akamai_sec_cpt_http_get_failed", exc_info=True)
        return None

    parsed = parse_sec_cpt(_response_text(probed))
    if parsed is None or not parsed.needs_proof_of_work:
        logger.info(
            "akamai_sec_cpt_http_not_crypto",
            extra={"provider": None if parsed is None else parsed.provider},
        )
        return None
    if parsed.duration_s > max_wait_s:
        logger.info(
            "akamai_sec_cpt_http_duration_over_budget",
            extra={"duration_s": parsed.duration_s, "max_wait_s": max_wait_s},
        )
        return None
    prefix = sec_cpt_prefix(_response_cookie(probed, "sec_cpt"))
    if prefix is None:
        logger.info("akamai_sec_cpt_http_cookie_missing")
        return None
    if parsed.duration_s > 0:
        logger.info(
            "akamai_sec_cpt_http_wait",
            extra={"duration_s": parsed.duration_s},
        )
        time.sleep(parsed.duration_s)
    answers = generate_sec_cpt_answers(
        sec=prefix,
        timestamp=parsed.timestamp,
        nonce=parsed.nonce,
        difficulty=parsed.difficulty,
        count=parsed.count,
    )
    if not answers:
        return None
    origin = _origin(str(getattr(probed, "url", None) or url))
    if not origin:
        return None
    payload = build_sec_cpt_payload(parsed.token, answers)
    try:
        session.post(
            f"{origin}/_sec/verify?provider={parsed.provider}",
            data=payload,
            headers={
                "content-type": "text/plain;charset=UTF-8",
                "origin": origin,
                "referer": str(getattr(probed, "url", None) or url),
            },
            timeout=timeout,
        )
        session.get(
            f"{origin}/_sec/cp_challenge/verify",
            headers={"referer": url, "accept": "*/*"},
            timeout=timeout,
        )
        solved = session.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception:
        logger.info("akamai_sec_cpt_http_submit_failed", exc_info=True)
        return None

    status = int(getattr(solved, "status_code", 0) or 0)
    if status != 200:
        logger.info(
            "akamai_sec_cpt_http_retry_status",
            extra={"status": status},
        )
        return None
    body = _response_text(solved).encode("utf-8", errors="replace")
    final_url = str(getattr(solved, "url", None) or url)
    response = HtmlResponse(
        url=final_url,
        status=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=body,
        encoding="utf-8",
        request=Request(final_url),
    )
    response.meta["fetch_metrics"] = {
        "fetch_strategy": "http-sec-cpt-crypto",
        "browser_used": False,
        "proxy_used": False,
    }
    return response


def _from_mapping(
    payload: dict[str, Any] | None,
    *,
    provider: str = "",
    duration_s: int = 0,
) -> SecCptChallenge | None:
    if not payload:
        return None
    resolved_provider = (
        (provider or str(payload.get("provider") or "")).strip().casefold()
    )
    token = str(payload.get("token") or "")
    nonce = str(payload.get("nonce") or "")
    difficulty = _int_field(payload.get("difficulty"))
    count = _int_field(payload.get("count")) or 1
    duration = duration_s or _int_field(payload.get("chlg_duration"))
    needs_pow = (
        resolved_provider in _POW_PROVIDERS
        and bool(token)
        and bool(nonce)
        and difficulty > 0
    )
    if not resolved_provider and not needs_pow:
        return None
    return SecCptChallenge(
        provider=resolved_provider or "behavioral",
        token=token,
        timestamp=_int_field(payload.get("timestamp")),
        nonce=nonce,
        difficulty=difficulty,
        count=count,
        duration_s=max(0, duration),
        needs_proof_of_work=needs_pow,
    )


def _challenge_payload(text: str) -> dict[str, Any] | None:
    match = _CHALLENGE_ATTR_RE.search(text)
    if match:
        decoded = _b64_json(match.group(1))
        if decoded is not None:
            return decoded
    if "sec-cp-challenge" not in text:
        return None
    return _loads_object(text)


def _b64_json(value: str) -> dict[str, Any] | None:
    raw = value.strip()
    padded = raw + ("=" * (-len(raw) % 4))
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            parsed = json.loads(decoder(padded))
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _int_field(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(response, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8", errors="replace")
    return ""


def _response_cookie(response: Any, name: str) -> str:
    jar = getattr(response, "cookies", None)
    if jar is None:
        return ""
    getter = getattr(jar, "get", None)
    if callable(getter):
        value = getter(name)
        if value:
            return str(value)
    return ""


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"
