"""Live validation of exchange providers (network). Not part of default suite."""

from __future__ import annotations

import json
import urllib.request

from scout_api.modules.exchange.domain import RateType
from scout_api.modules.exchange.providers.bcb_ptax import parse_ptax_json
from scout_api.modules.exchange.providers.bcp_referential import parse_bcp_html
from scout_api.modules.exchange.providers.valor_data import ValorDataProvider

UA = {"User-Agent": "Mozilla/5.0 (compatible; ScoutApiV2/0.1)"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main() -> None:
    print("--- LIVE FX VALIDATION ---")
    rates = ValorDataProvider().fetch()
    for r in rates:
        print(r.rate_type.value, r.rate, r.source)
    assert any(r.rate_type == RateType.TOURISM_SELL for r in rates)

    html = _get("https://www.bcp.gov.py/webapps/web/cotizacion/monedas").decode(
        "utf-8", "replace"
    )
    for r in parse_bcp_html(html):
        print("bcp", r.base_currency, r.quote_currency, r.rate)

    url = (
        "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
        "CotacaoDolarPeriodo(dataInicial=@i,dataFinalCotacao=@f)"
        "?@i='09-15-2026'&@f='09-22-2026'&$format=json"
    )
    data = json.loads(_get(url))
    print("ptax", [(r.rate_type.value, str(r.rate)) for r in parse_ptax_json(data)])
    print("OK")


if __name__ == "__main__":
    main()
