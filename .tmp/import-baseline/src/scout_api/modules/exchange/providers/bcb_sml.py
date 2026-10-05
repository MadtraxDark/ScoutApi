"""BCB SML provider — scrapes https://www.bcb.gov.br/rex/sml/PRY_taxas.asp.

Extracts the first data row from the SML Brasil-Paraguai table:
  Column "Taxa SML Real/Guarani" (index 4, 0-based) = rate PYG→BRL.

HTML structure (from probe 2026-09-22):
    <table class="estilo_acess_4510" ...>
      <thead>
        <tr>
          <th>...</th>
          <th>Guarani/Dólar</th>         (idx 1)
          <th>Taxa SML Guarani/Real</th>  (idx 2 — BCP's side)
          <th>Real/Dólar</th>            (idx 3 — PTAX)
          <th>Taxa SML Real/Guarani</th> (idx 4 — what we want: PYG/BRL)
        </tr>
      </thead>
      <tbody>
        <tr ...>
          <td>21/09/2026&nbsp;&nbsp;</td>
          <td>5.948,28</td>
          <td>1.163,72813715</td>
          <td>5,1114</td>
          <td>0,00085935</td>    ← Taxa SML Real/Guarani = PYG/BRL
        </tr>
        ...

URL hardcoded (anti-SSRF). HTTP-only.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime

import httpx

from scout_api.modules.exchange.domain import FetchedRate, RateType
from scout_api.modules.exchange.money import parse_br_decimal
from scout_api.modules.exchange.providers.http_client import build_http_client

logger = logging.getLogger(__name__)

_SML_URL = "https://www.bcb.gov.br/rex/sml/PRY_taxas.asp?frame=1&idpai=SMLPRY"
_SOURCE_ID = "bcb_sml"

# Match a table row containing 5 td cells
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*"
    r"<td[^>]*>(.*?)</td>\s*"  # 0: date
    r"<td[^>]*>(.*?)</td>\s*"  # 1: Guarani/Dólar
    r"<td[^>]*>(.*?)</td>\s*"  # 2: SML Guarani/Real
    r"<td[^>]*>(.*?)</td>\s*"  # 3: Real/Dólar (PTAX)
    r"<td[^>]*>(.*?)</td>",    # 4: Taxa SML Real/Guarani ← our target
    re.DOTALL | re.IGNORECASE,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NBSPS_RE = re.compile(r"[\xa0\u200b&;]")
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _clean(s: str) -> str:
    cleaned = _HTML_TAG_RE.sub("", s)
    cleaned = cleaned.replace("&nbsp;", " ").replace("\xa0", " ")
    return cleaned.strip()


def _parse_date(s: str) -> datetime | None:
    m = _DATE_RE.search(s)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def parse_sml_html(html: str, *, fetched_at: datetime | None = None) -> list[FetchedRate]:
    """Parse BCB SML HTML page.

    Args:
        html: Full HTML content.
        fetched_at: Override fetch timestamp.

    Raises:
        ValueError: If no data rows found or rate parse fails.
    """
    now = fetched_at or datetime.now(UTC)
    rows = _ROW_RE.findall(html)

    # Filter to actual data rows (date col looks like dd/mm/yyyy)
    data_rows = [r for r in rows if _DATE_RE.search(_clean(r[0]))]

    if not data_rows:
        raise ValueError("bcb_sml: no data rows found in SML table")

    # First row is the most recent
    date_raw, _, _, _, taxa_raw = data_rows[0]
    date_clean = _clean(date_raw)
    taxa_clean = _clean(taxa_raw)

    source_ts = _parse_date(date_clean)

    try:
        rate = parse_br_decimal(taxa_clean)
    except ValueError as exc:
        raise ValueError(f"bcb_sml: cannot parse Taxa SML Real/Guarani: {exc}") from exc

    if rate <= 0:
        raise ValueError(f"bcb_sml: non-positive rate: {rate}")

    return [
        FetchedRate(
            base_currency="PYG",
            quote_currency="BRL",
            rate_type=RateType.OFFICIAL,
            rate=rate,
            source=_SOURCE_ID,
            source_timestamp=source_ts,
            fetched_at=now,
        )
    ]


class BcbSmlProvider:
    """BCB SML (Sistema de Pagamentos em Moeda Local) provider.

    URL hardcoded to www.bcb.gov.br (anti-SSRF). HTTP-only.
    """

    source_id = _SOURCE_ID

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self) -> list[FetchedRate]:
        started = time.perf_counter()
        client = self._client or build_http_client()
        own_client = self._client is None
        try:
            resp = client.get(_SML_URL)
            resp.raise_for_status()
            html = resp.text
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "bcb_sml_fetch_ok",
                extra={"status": resp.status_code, "duration_ms": duration_ms},
            )
            fetched_at = datetime.now(UTC)
            return parse_sml_html(html, fetched_at=fetched_at)
        except httpx.HTTPStatusError as exc:
            logger.warning("bcb_sml_http_error", extra={"status": exc.response.status_code})
            raise
        except httpx.TimeoutException:
            logger.warning("bcb_sml_timeout")
            raise
        finally:
            if own_client:
                client.close()
