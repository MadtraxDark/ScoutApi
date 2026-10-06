"""Unit tests for exchange-rate parsers (offline fixtures)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scout_api.modules.exchange.domain import RateType
from scout_api.modules.exchange.money import parse_br_decimal, quantize_brl_money
from scout_api.modules.exchange.providers.bcb_ptax import parse_ptax_json
from scout_api.modules.exchange.providers.bcb_sml import parse_sml_html
from scout_api.modules.exchange.providers.bcp_referential import parse_bcp_html
from scout_api.modules.exchange.providers.valor_data import (
    parse_valor_html,
    parse_valorinveste_tourism_html,
)

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)


def test_parse_br_decimal_comma() -> None:
    assert parse_br_decimal("5,3034") == Decimal("5.3034")
    assert parse_br_decimal("1.161,47") == Decimal("1161.47")


def test_quantize_brl_money() -> None:
    assert quantize_brl_money(Decimal("5300.006")) == Decimal("5300.01")
    assert quantize_brl_money(Decimal("5300.004")) == Decimal("5300.00")


def test_valor_parses_tourism_sell() -> None:
    html = (FIX / "valor_moedas.html").read_text(encoding="utf-8")
    rates = parse_valor_html(html, fetched_at=NOW)
    by_type = {r.rate_type: r for r in rates if r.base_currency == "USD"}
    assert RateType.TOURISM_SELL in by_type
    assert by_type[RateType.TOURISM_SELL].rate == Decimal("5.3034")
    assert by_type[RateType.TOURISM_BUY].rate == Decimal("5.1234")
    assert by_type[RateType.TOURISM_SELL].quote_currency == "BRL"


def test_valorinveste_tourism_ticker() -> None:
    html = (FIX / "valorinveste_turismo.html").read_text(encoding="utf-8")
    rates = parse_valorinveste_tourism_html(html, fetched_at=NOW)
    assert len(rates) == 1
    assert rates[0].rate_type == RateType.TOURISM_SELL
    assert rates[0].rate == Decimal("5.30")


def test_valor_missing_turismo_fails() -> None:
    html = (FIX / "valor_missing_turismo.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Turismo|USDBRLT|ticker|row"):
        parse_valor_html(html, fetched_at=NOW)


def test_bcb_ptax_json() -> None:
    data = json.loads((FIX / "bcb_ptax.json").read_text(encoding="utf-8"))
    rates = parse_ptax_json(data, fetched_at=NOW)
    assert len(rates) == 2
    sell = next(r for r in rates if r.rate_type == RateType.PTAX_SELL)
    assert sell.rate == Decimal("5.1161")


def test_bcp_usd_and_pyg_brl() -> None:
    html = (FIX / "bcp_cotizacion.html").read_text(encoding="utf-8")
    rates = parse_bcp_html(html, fetched_at=NOW)
    usd_pyg = next(r for r in rates if r.base_currency == "USD")
    pyg_brl = next(r for r in rates if r.base_currency == "PYG")
    assert usd_pyg.quote_currency == "PYG"
    assert usd_pyg.rate == Decimal("5935.01")
    assert pyg_brl.quote_currency == "BRL"
    # 1 / 1161.47
    assert abs(pyg_brl.rate - (Decimal("1") / Decimal("1161.47"))) < Decimal(
        "0.0000001"
    )


def test_bcb_sml_pyg_brl() -> None:
    html = (FIX / "bcb_sml.html").read_text(encoding="utf-8")
    rates = parse_sml_html(html, fetched_at=NOW)
    assert len(rates) == 1
    assert rates[0].base_currency == "PYG"
    assert rates[0].quote_currency == "BRL"
    assert rates[0].rate_type == RateType.OFFICIAL
    assert rates[0].rate == Decimal("0.00085935")
