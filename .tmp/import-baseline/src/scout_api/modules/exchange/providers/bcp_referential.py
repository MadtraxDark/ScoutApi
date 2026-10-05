"""BCP Referential provider — scrapes https://www.bcp.gov.py/webapps/web/cotizacion/monedas.

Parses table#cotizacion-interbancaria for:
- USD row: ₲/ME column → USD/PYG official rate
- BRL row: ₲/ME column → guaranis_per_BRL → PYG/BRL = 1/guaranis_per_BRL

HTML structure (from probe 2026-09-22):
    <table id="cotizacion-interbancaria" ...>
      <thead>
        <tr>
          <th colspan="2">MONEDA</th>
          <th>ME/USD</th>
          <th>₲ / ME</th>   ← column index 3 (0-based in <td> rows)
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>DÓLAR ESTADOUNIDENSE</td>
          <td>USD</td>                  ← symbol column
          <td>1,0000</td>               ← ME/USD
          <td>5.935,01</td>             ← ₲/ME = guaranis per USD
        </tr>
        <tr>
          <td>REAL BRASILEÑO</td>
          <td>BRL</td>                  ← symbol column
          <td>5,1099</td>               ← ME/USD (BRL/USD rate) — not used
          <td>1.161,47</td>             ← ₲/ME = guaranis per BRL
        </tr>
      ...

URL hardcoded (anti-SSRF). HTTP-only.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from scout_api.modules.exchange.domain import FetchedRate, RateType
from scout_api.modules.exchange.money import parse_py_decimal
from scout_api.modules.exchange.providers.http_client import build_http_client

logger = logging.getLogger(__name__)

_BCP_URL = "https://www.bcp.gov.py/webapps/web/cotizacion/monedas"
_SOURCE_ID = "bcp_referential"

# Extract rows from the cotizacion-interbancaria table.
# Each row has 4 cells: name, symbol, ME/USD, ₲/ME
_TABLE_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*"
    r"<td[^>]*>\s*([A-Z]{3})\s*</td>\s*"
    r"<td[^>]*>\s*([^<]+)</td>\s*"
    r"<td[^>]*>\s*([^<]+)</td>",
    re.DOTALL | re.IGNORECASE,
)

# Strip HTML tags from cell content
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return _HTML_TAG_RE.sub("", s).strip()


def parse_bcp_html(html: str, *, fetched_at: datetime | None = None) -> list[FetchedRate]:
    """Parse BCP cotización HTML.

    Args:
        html: Full page HTML.
        fetched_at: Override fetch timestamp.

    Raises:
        ValueError: If USD or BRL rows not found, or rate parse fails.
    """
    now = fetched_at or datetime.now(UTC)

    # Find the cotizacion-interbancaria table section
    table_match = re.search(
        r'<table[^>]+id=["\']?cotizacion-interbancaria["\']?(.*?)</table>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not table_match:
        raise ValueError("bcp_referential: table#cotizacion-interbancaria not found")

    table_html = table_match.group(1)
    rows = _TABLE_ROW_RE.findall(table_html)

    rates_by_symbol: dict[str, tuple[str, str]] = {}  # symbol → (me_usd, guaranis_per_me)
    for name_raw, symbol_raw, me_usd_raw, guaranis_raw in rows:
        symbol = symbol_raw.strip().upper()
        rates_by_symbol[symbol] = (_clean(me_usd_raw), _clean(guaranis_raw))

    if "USD" not in rates_by_symbol:
        raise ValueError("bcp_referential: USD row not found in table")
    if "BRL" not in rates_by_symbol:
        raise ValueError("bcp_referential: BRL row not found in table")

    results: list[FetchedRate] = []

    # USD/PYG: ₲/ME for USD row = guaranis_per_usd
    _, guaranis_per_usd_raw = rates_by_symbol["USD"]
    try:
        usd_pyg = parse_py_decimal(guaranis_per_usd_raw)
    except ValueError as exc:
        raise ValueError(f"bcp_referential: cannot parse USD/PYG: {exc}") from exc

    results.append(
        FetchedRate(
            base_currency="USD",
            quote_currency="PYG",
            rate_type=RateType.OFFICIAL,
            rate=usd_pyg,
            source=_SOURCE_ID,
            source_timestamp=None,
            fetched_at=now,
        )
    )

    # PYG/BRL: ₲/ME for BRL row = guaranis_per_brl → rate = 1/guaranis_per_brl
    _, guaranis_per_brl_raw = rates_by_symbol["BRL"]
    try:
        guaranis_per_brl = parse_py_decimal(guaranis_per_brl_raw)
    except ValueError as exc:
        raise ValueError(f"bcp_referential: cannot parse guaranis_per_brl: {exc}") from exc

    if guaranis_per_brl == 0:
        raise ValueError("bcp_referential: guaranis_per_brl is zero")

    pyg_brl = Decimal("1") / guaranis_per_brl

    results.append(
        FetchedRate(
            base_currency="PYG",
            quote_currency="BRL",
            rate_type=RateType.OFFICIAL,
            rate=pyg_brl,
            source=_SOURCE_ID,
            source_timestamp=None,
            fetched_at=now,
        )
    )

    return results


class BcpReferentialProvider:
    """BCP cotización referencial provider.

    URL hardcoded to www.bcp.gov.py (anti-SSRF). HTTP-only.
    """

    source_id = _SOURCE_ID

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self) -> list[FetchedRate]:
        started = time.perf_counter()
        client = self._client or build_http_client()
        own_client = self._client is None
        try:
            resp = client.get(_BCP_URL)
            resp.raise_for_status()
            html = resp.text
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "bcp_referential_fetch_ok",
                extra={"status": resp.status_code, "duration_ms": duration_ms},
            )
            fetched_at = datetime.now(UTC)
            return parse_bcp_html(html, fetched_at=fetched_at)
        except httpx.HTTPStatusError as exc:
            logger.warning("bcp_referential_http_error", extra={"status": exc.response.status_code})
            raise
        except httpx.TimeoutException:
            logger.warning("bcp_referential_timeout")
            raise
        finally:
            if own_client:
                client.close()
