"""BCB PTAX provider via OLINDA OData API.

Endpoint: https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/
CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)

Fetches last N days, takes the most recent entry.
Fields: cotacaoCompra (ptax_buy), cotacaoVenda (ptax_sell) for USD/BRL.

No API key required. JSON response.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx

from scout_api.modules.exchange.domain import FetchedRate, RateType
from scout_api.modules.exchange.providers.http_client import build_http_client

logger = logging.getLogger(__name__)

_SOURCE_ID = "bcb_ptax"
_BASE_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
)
_PARAMS = {
    "$top": "100",
    "$orderby": "dataHoraCotacao desc",
    "$format": "json",
    "$select": "cotacaoCompra,cotacaoVenda,dataHoraCotacao",
}
_DAYS_LOOKBACK = 5


def _build_url(start: date, end: date) -> str:
    params = dict(_PARAMS)
    params["@dataInicial"] = f"'{start.strftime('%m-%d-%Y')}'"
    params["@dataFinalCotacao"] = f"'{end.strftime('%m-%d-%Y')}'"
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{_BASE_URL}?{qs}"


def parse_ptax_json(data: dict[object, object], *, fetched_at: datetime | None = None) -> list[FetchedRate]:
    """Parse OLINDA PTAX JSON response.

    Args:
        data: Parsed JSON dict.
        fetched_at: Override fetch timestamp (tests).

    Raises:
        ValueError: If value array is empty or fields missing.
    """
    now = fetched_at or datetime.now(UTC)
    value = data.get("value")
    if not isinstance(value, list) or not value:
        raise ValueError("bcb_ptax: empty or missing 'value' array in response")

    # Data is ordered desc by dataHoraCotacao — take the first (most recent)
    latest = value[0]
    if not isinstance(latest, dict):
        raise ValueError("bcb_ptax: unexpected entry type in 'value'")

    compra_raw = latest.get("cotacaoCompra")
    venda_raw = latest.get("cotacaoVenda")
    ts_raw = latest.get("dataHoraCotacao")

    if compra_raw is None or venda_raw is None:
        raise ValueError(f"bcb_ptax: missing cotacaoCompra/cotacaoVenda in {latest!r}")

    try:
        buy_rate = Decimal(str(compra_raw))
        sell_rate = Decimal(str(venda_raw))
    except Exception as exc:
        raise ValueError(f"bcb_ptax: cannot parse rates: {exc}") from exc

    source_ts: datetime | None = None
    if isinstance(ts_raw, str):
        try:
            # Format: "2026-09-22 13:03:30.646171"
            source_ts = datetime.strptime(ts_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            pass

    return [
        FetchedRate(
            base_currency="USD",
            quote_currency="BRL",
            rate_type=RateType.PTAX_BUY,
            rate=buy_rate,
            source=_SOURCE_ID,
            source_timestamp=source_ts,
            fetched_at=now,
        ),
        FetchedRate(
            base_currency="USD",
            quote_currency="BRL",
            rate_type=RateType.PTAX_SELL,
            rate=sell_rate,
            source=_SOURCE_ID,
            source_timestamp=source_ts,
            fetched_at=now,
        ),
    ]


class BcbPtaxProvider:
    """BCB PTAX provider via OLINDA OData API.

    URL hardcoded to olinda.bcb.gov.br (anti-SSRF). No API key.
    """

    source_id = _SOURCE_ID

    def __init__(self, *, client: httpx.Client | None = None, days_lookback: int = _DAYS_LOOKBACK) -> None:
        self._client = client
        self._days_lookback = days_lookback

    def fetch(self) -> list[FetchedRate]:
        started = time.perf_counter()
        today = date.today()
        start = today - timedelta(days=self._days_lookback)
        url = _build_url(start, today)

        client = self._client or build_http_client()
        own_client = self._client is None
        try:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "bcb_ptax_fetch_ok",
                extra={"status": resp.status_code, "duration_ms": duration_ms},
            )
            fetched_at = datetime.now(UTC)
            return parse_ptax_json(data, fetched_at=fetched_at)
        except httpx.HTTPStatusError as exc:
            logger.warning("bcb_ptax_http_error", extra={"status": exc.response.status_code})
            raise
        except httpx.TimeoutException:
            logger.warning("bcb_ptax_timeout")
            raise
        finally:
            if own_client:
                client.close()
