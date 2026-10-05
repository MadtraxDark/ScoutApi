"""Valor / Valor Investe provider for USD/BRL tourism (+ optional PTAX).

Strategies (tried in order; URLs hardcoded — anti-SSRF):

1. Legacy Valor Data HTML table (``table-date-value2``) with Compra/Venda.
2. Valor Investe ticker ``data-symbol="USDBRLT_VALR"`` → tourism_sell only
   (media publishes a single tourism quote; treated as sell-side for conversion).
3. Embedded ``Converter({quotes:...})`` on valor-data page for PTAX cross-check.

HTTP-only; no Camoufox. Pure FX — no taxes/fees.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime

import httpx

from scout_api.modules.exchange.domain import FetchedRate, RateType
from scout_api.modules.exchange.money import parse_br_decimal
from scout_api.modules.exchange.providers.http_client import build_http_client

logger = logging.getLogger(__name__)

_VALOR_MOEDAS_URL = "https://valor.globo.com/valor-data/moedas/"
_VALOR_INVESTE_URL = "https://valorinveste.globo.com/cotacoes/dolar/"
_SOURCE_ID = "valor_data"

_ROW_RULES: list[tuple[str, tuple[str, ...], tuple[RateType, RateType]]] = [
    (
        "turismo",
        ("dolar turismo", "dólar turismo"),
        (RateType.TOURISM_BUY, RateType.TOURISM_SELL),
    ),
    (
        "ptax",
        ("ptax",),
        (RateType.PTAX_BUY, RateType.PTAX_SELL),
    ),
]

_ROW_RE = re.compile(
    r"<tr>\s*"
    r'<td[^>]*class="table-date-value2"[^>]*>(.*?)</td>\s*'
    r'<td[^>]*class="table-date-value2"[^>]*>(.*?)</td>\s*'
    r'<td[^>]*class="table-date-value2"[^>]*>(.*?)</td>\s*'
    r"<td[^>]*>[^<]*</td>\s*"
    r'<td[^>]*class="table-date-value2"[^>]*>(.*?)</td>\s*'
    r"</tr>",
    re.DOTALL | re.IGNORECASE,
)

_TIMESTAMP_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})")

_TOURISM_TICKER_RE = re.compile(
    r'data-symbol=["\']USDBRLT_VALR["\'][^>]*>.*?'
    r'ticker__item__value[^>]*>\s*R\$\s*([0-9.,]+)\s*<',
    re.DOTALL | re.IGNORECASE,
)


def _parse_source_timestamp(raw: str) -> datetime | None:
    m = _TIMESTAMP_RE.search(raw.strip())
    if not m:
        return None
    day, month, year, hour, minute = (int(x) for x in m.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=UTC)
    except ValueError:
        return None


def _norm(name: str) -> str:
    return (
        name.strip()
        .lower()
        .replace("á", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ó", "o")
        .replace("ô", "o")
    )


def parse_valor_table_html(
    html: str, *, fetched_at: datetime | None = None
) -> list[FetchedRate]:
    """Parse legacy Valor Data Compra/Venda table."""
    now = fetched_at or datetime.now(UTC)
    rows = _ROW_RE.findall(html)
    if not rows:
        raise ValueError("valor_data: no table rows found in HTML")

    found: dict[str, tuple[str, str, str, tuple[RateType, RateType]]] = {}
    for name_raw, buy_raw, sell_raw, ts_raw in rows:
        name_clean = name_raw.strip().lower()
        name_norm = _norm(name_raw)
        for key, needles, types in _ROW_RULES:
            if key in found:
                continue
            if any(n in name_norm or n in name_clean for n in needles):
                if "dolar" not in name_norm and "dólar" not in name_clean:
                    continue
                found[key] = (
                    buy_raw.strip(),
                    sell_raw.strip(),
                    ts_raw.strip(),
                    types,
                )
                break

    if "turismo" not in found:
        raise ValueError("valor_data: 'Dólar Turismo' row not found in HTML")

    results: list[FetchedRate] = []
    for key, (buy_raw, sell_raw, ts_raw, (buy_type, sell_type)) in found.items():
        buy_rate = parse_br_decimal(buy_raw)
        sell_rate = parse_br_decimal(sell_raw)
        source_ts = _parse_source_timestamp(ts_raw)
        for rt, rate in ((buy_type, buy_rate), (sell_type, sell_rate)):
            results.append(
                FetchedRate(
                    base_currency="USD",
                    quote_currency="BRL",
                    rate_type=rt,
                    rate=rate,
                    source=_SOURCE_ID,
                    source_timestamp=source_ts,
                    fetched_at=now,
                )
            )
    return results


def parse_valorinveste_tourism_html(
    html: str, *, fetched_at: datetime | None = None
) -> list[FetchedRate]:
    """Parse Valor Investe ticker for USDBRLT_VALR as tourism_sell."""
    now = fetched_at or datetime.now(UTC)
    m = _TOURISM_TICKER_RE.search(html)
    if not m:
        raise ValueError("valor_data: USDBRLT_VALR ticker not found")
    sell_rate = parse_br_decimal(m.group(1))
    return [
        FetchedRate(
            base_currency="USD",
            quote_currency="BRL",
            rate_type=RateType.TOURISM_SELL,
            rate=sell_rate,
            source=_SOURCE_ID,
            source_timestamp=None,
            fetched_at=now,
        )
    ]


def parse_valor_converter_ptax(
    html: str, *, fetched_at: datetime | None = None
) -> list[FetchedRate]:
    """Extract PTAX from embedded Converter({quotes:...}) when present."""
    now = fetched_at or datetime.now(UTC)
    m = re.search(r"Converter\(\{quotes:\s*", html)
    if not m:
        raise ValueError("valor_data: Converter quotes embed not found")
    start = m.end()
    depth = 0
    end = None
    for i, ch in enumerate(html[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("valor_data: Converter quotes JSON incomplete")
    payload = json.loads(html[start:end])
    body = payload.get("body") or payload
    ptax = body.get("USDBRL_BCBR")
    if not isinstance(ptax, dict) or ptax.get("lastQuote") is None:
        raise ValueError("valor_data: USDBRL_BCBR missing in Converter embed")
    rate = parse_br_decimal(str(ptax["lastQuote"]))
    # Embed exposes a single quote; store as both buy/sell for agreement checks.
    return [
        FetchedRate(
            base_currency="USD",
            quote_currency="BRL",
            rate_type=RateType.PTAX_BUY,
            rate=rate,
            source=_SOURCE_ID,
            source_timestamp=None,
            fetched_at=now,
        ),
        FetchedRate(
            base_currency="USD",
            quote_currency="BRL",
            rate_type=RateType.PTAX_SELL,
            rate=rate,
            source=_SOURCE_ID,
            source_timestamp=None,
            fetched_at=now,
        ),
    ]


def parse_valor_html(html: str, *, fetched_at: datetime | None = None) -> list[FetchedRate]:
    """Backward-compatible entry: prefer table, else tourism ticker."""
    try:
        return parse_valor_table_html(html, fetched_at=fetched_at)
    except ValueError:
        return parse_valorinveste_tourism_html(html, fetched_at=fetched_at)


class ValorDataProvider:
    """HTTP provider for Valor tourism USD/BRL (and optional PTAX embed)."""

    source_id = _SOURCE_ID

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self) -> list[FetchedRate]:
        started = time.perf_counter()
        client = self._client or build_http_client()
        own_client = self._client is None
        try:
            fetched_at = datetime.now(UTC)
            results: list[FetchedRate] = []

            # 1) Try legacy/full table on valor-data
            moedas_html = client.get(_VALOR_MOEDAS_URL).text
            try:
                results.extend(parse_valor_table_html(moedas_html, fetched_at=fetched_at))
            except ValueError:
                logger.info("valor_data_table_unavailable_trying_fallbacks")
                # 2) Tourism ticker on Valor Investe
                investe_html = client.get(_VALOR_INVESTE_URL).text
                results.extend(
                    parse_valorinveste_tourism_html(investe_html, fetched_at=fetched_at)
                )
                # 3) PTAX from Converter embed on moedas page (audit)
                try:
                    results.extend(
                        parse_valor_converter_ptax(moedas_html, fetched_at=fetched_at)
                    )
                except ValueError as exc:
                    logger.debug("valor_data_ptax_embed_skipped", extra={"error": str(exc)})

            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "valor_data_fetch_ok",
                extra={"count": len(results), "duration_ms": duration_ms},
            )
            if not any(r.rate_type == RateType.TOURISM_SELL for r in results):
                raise ValueError("valor_data: tourism_sell not obtained from any source")
            return results
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "valor_data_http_error", extra={"status": exc.response.status_code}
            )
            raise
        except httpx.TimeoutException:
            logger.warning("valor_data_timeout")
            raise
        finally:
            if own_client:
                client.close()
