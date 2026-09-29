"""Testes de cotações e indicadores (qijournal.collect.market) — sem rede, com respostas reais."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qijournal import net
from qijournal.collect.market import (
    BCB_SGS_URL,
    COINGECKO_URL,
    YAHOO_URL,
    accum12,
    collect_market,
    format_change,
    format_value,
)
from qijournal.config import load_config

MARKET = Path(__file__).parent / "fixtures" / "coleta" / "market"
NOW = datetime(2026, 9, 29, 8, 7, tzinfo=timezone.utc)

IGPM_MONTHLY = [0.42, -0.36, 0.27, -0.01, 0.41, -0.73, 0.52, 2.73, 0.84, -0.50, -1.16, -0.22]
INCC_MONTHLY = [0.17, 0.30, 0.27, 0.21, 0.72, 0.28, 0.54, 1.00, 0.88, 0.78, 0.61, 0.66]


def yahoo(symbol_encoded: str) -> str:
    return YAHOO_URL.format(symbol=symbol_encoded)


def sgs(series: int, n: int) -> str:
    return BCB_SGS_URL.format(series=series, n=n)


# URLs exatas esperadas → fixture (respostas reais das APIs, via sonda no GitHub Actions)
ROUTES: dict[str, str] = {
    yahoo("USDBRL%3DX"): "yahoo_usdbrl.json",
    yahoo("EURBRL%3DX"): "yahoo_eurbrl.json",
    yahoo("CNYBRL%3DX"): "yahoo_cnybrl.json",
    yahoo("%5EBVSP"): "yahoo_bvsp.json",
    yahoo("IFIX.SA"): "yahoo_ifix.json",
    yahoo("%5EGSPC"): "yahoo_gspc.json",
    yahoo("BZ%3DF"): "yahoo_bzf.json",
    yahoo("GC%3DF"): "yahoo_gcf.json",
    yahoo("BTC-USD"): "yahoo_btc_error.json",
    COINGECKO_URL.format(coin="bitcoin"): "coingecko_bitcoin.json",
    sgs(432, 2): "bcb_432_selic.json",
    sgs(13522, 2): "bcb_13522_ipca12m.json",
    sgs(189, 12): "bcb_189_igpm.json",
    sgs(192, 12): "bcb_192_incc.json",
    sgs(1, 2): "bcb_1_ptax.json",
    sgs(21619, 2): "bcb_21619_euro.json",
}


class FakeFetch:
    """Fetch injetável: URL → nome de fixture, bytes ou exceção; registra URLs chamadas."""

    def __init__(self, overrides: dict[str, object] | None = None):
        self.routes: dict[str, object] = {**ROUTES, **(overrides or {})}
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, **kwargs) -> net.Response:
        with self._lock:
            self.calls.append((url, kwargs))
        result = self.routes.get(url)
        if result is None:
            raise net.FetchError(url, "HTTP 404", status=404)
        if isinstance(result, BaseException):
            raise result
        content = (MARKET / result).read_bytes() if isinstance(result, str) else result
        return net.Response(url=url, status=200, content=content, headers={"content-type": "application/json"})

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


@pytest.fixture(scope="module")
def market_entries() -> list[dict]:
    return load_config(env={}).market


def entry(market_entries: list[dict], quote_id: str) -> dict:
    return next(e for e in market_entries if e["id"] == quote_id)


# ── formatação ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, fmt, decimals, expected",
    [
        (5.2226, "brl", 2, "R$ 5,22"),
        (0.7321, "brl", 2, "R$ 0,73"),
        (83061, "usd", 0, "US$ 83.061"),
        (99.27, "usd", 2, "US$ 99,27"),
        (4164.2, "usd", 0, "US$ 4.164"),
        (182991.12, "pts", 0, "182.991 pts"),
        (1234567.891, "pts", 2, "1.234.567,89 pts"),
        (4.22, "pct", 2, "4,22%"),
        (-0.32, "pct", 2, "-0,32%"),
        (13.75, "pct", 2, "13,75%"),
        (7.5, "desconhecido", 1, "7,5"),
    ],
)
def test_format_value(value: float, fmt: str, decimals: int, expected: str) -> None:
    assert format_value(value, fmt, decimals) == expected


@pytest.mark.parametrize(
    "pct, expected",
    [
        (0.1938, "+0,19%"),
        (-1.16, "-1,16%"),
        (0.0, "0,00%"),
        (0.004, "0,00%"),
        (-0.004, "0,00%"),
        (-0.005001, "-0,01%"),
        (1234.5, "+1.234,50%"),
    ],
)
def test_format_change(pct: float, expected: str) -> None:
    assert format_change(pct) == expected


def test_accum12_compounds_last_twelve_months() -> None:
    expected = 1.0
    for monthly in IGPM_MONTHLY:
        expected *= 1 + monthly / 100
    assert accum12(IGPM_MONTHLY) == pytest.approx((expected - 1) * 100)
    assert accum12(IGPM_MONTHLY) == pytest.approx(2.1782, abs=1e-4)
    # com 13 valores, usa só os 12 mais recentes
    assert accum12([50.0] + IGPM_MONTHLY) == pytest.approx(accum12(IGPM_MONTHLY))
    assert accum12([0.0] * 12) == 0.0
    assert accum12([1.0] * 12) == pytest.approx(12.6825, abs=1e-4)


def test_accum12_requires_twelve_values() -> None:
    with pytest.raises(ValueError):
        accum12(IGPM_MONTHLY[:11])


# ── collect_market com a configuração real ───────────────────────────────────


def test_collect_market_full_config(market_entries: list[dict]) -> None:
    fetch = FakeFetch()
    quotes = collect_market(market_entries, fetch=fetch, now=NOW)

    assert [q.id for q in quotes] == [e["id"] for e in market_entries]  # ordem da configuração
    by_id = {q.id: q for q in quotes}
    assert {q.id: q.display for q in quotes} == {
        "usd": "R$ 5,22", "eur": "R$ 6,11", "cny": "R$ 0,73", "ibov": "182.991 pts", "ifix": "3.718 pts",
        "spx": "7.684 pts", "brent": "US$ 99,27", "gold": "US$ 4.164", "btc": "US$ 83.061",
        "selic": "13,75%", "ipca12m": "4,22%", "igpm12m": "2,18%", "incc12m": "6,61%",
    }
    for quote in quotes:
        spec = entry(market_entries, quote.id)
        assert quote.label == spec["label"] and quote.kind == spec["kind"]
        assert quote.display == format_value(quote.value, spec["format"], spec["decimals"])

    usd = by_id["usd"]
    assert usd.value == 5.2226 and usd.change_pct == pytest.approx(0.1938)
    assert usd.source == "Yahoo Finance"
    assert usd.as_of == "2026-09-29T02:00:03+00:00"  # regularMarketTime (epoch) em ISO UTC
    assert by_id["ibov"].change_pct == pytest.approx(-0.265)

    # sem regularMarketChangePercent: variação calculada dos dois últimos fechamentos não nulos
    assert by_id["cny"].change_pct == pytest.approx((0.7321 / 0.7302 - 1) * 100, abs=1e-4)
    assert by_id["ifix"].change_pct == pytest.approx((3718.35 / 3711.9 - 1) * 100, abs=1e-4)

    # BTC: Yahoo responde erro → fallback CoinGecko
    btc = by_id["btc"]
    assert (btc.source, btc.value, btc.kind) == ("CoinGecko", 83061, "crypto")
    assert btc.change_pct == pytest.approx(-0.6858, abs=1e-4)
    assert btc.as_of == NOW.isoformat()

    # indicadores do BCB: sem variação, as_of mensal ou diário
    selic = by_id["selic"]
    assert (selic.value, selic.change_pct, selic.source) == (13.75, None, "Banco Central (SGS)")
    assert selic.as_of == "2026-09-29"  # série diária com datas futuras (vigência) → limitada a hoje
    assert (by_id["ipca12m"].value, by_id["ipca12m"].as_of) == (4.22, "2026-08")
    assert by_id["igpm12m"].value == pytest.approx(accum12(IGPM_MONTHLY))
    assert by_id["incc12m"].value == pytest.approx(accum12(INCC_MONTHLY))
    assert all(by_id[i].change_pct is None for i in ("selic", "ipca12m", "igpm12m", "incc12m"))

    # URLs corretas (símbolos codificados) e fallback de câmbio não acionado
    assert yahoo("%5EBVSP") in fetch.urls and yahoo("BZ%3DF") in fetch.urls
    assert sgs(1, 2) not in fetch.urls and sgs(21619, 2) not in fetch.urls
    assert all(kwargs.get("timeout") and "retries" in kwargs for _, kwargs in fetch.calls)


def test_yahoo_failure_uses_bcb_fallback_for_fx(market_entries: list[dict]) -> None:
    fetch = FakeFetch({
        yahoo("USDBRL%3DX"): net.FetchError(yahoo("USDBRL%3DX"), "HTTP 429", status=429),
        yahoo("EURBRL%3DX"): b"<html>Too Many Requests</html>",
    })
    quotes = collect_market([entry(market_entries, "usd"), entry(market_entries, "eur")], fetch=fetch, now=NOW)
    usd, eur = quotes
    assert (usd.id, usd.label, usd.kind, usd.source) == ("usd", "Dólar", "fx", "Banco Central (SGS)")
    assert usd.value == 5.2132 and usd.display == "R$ 5,21"
    assert usd.change_pct == pytest.approx((5.2132 / 5.1991 - 1) * 100, abs=1e-4)
    assert usd.as_of == "2026-09-28"
    assert (eur.value, eur.display, eur.source) == (6.1045, "R$ 6,10", "Banco Central (SGS)")


def test_failed_entries_are_omitted_without_affecting_others(market_entries: list[dict]) -> None:
    fetch = FakeFetch({
        yahoo("CNYBRL%3DX"): RuntimeError("bug inesperado"),  # sem fallback → omitido
        yahoo("BTC-USD"): net.FetchError(yahoo("BTC-USD"), "timeout"),
        COINGECKO_URL.format(coin="bitcoin"): b"{}",  # fallback também falha
        sgs(189, 12): json.dumps([{"data": "01/08/2026", "valor": "-0.22"}]).encode(),  # < 12 meses
        sgs(432, 2): b'{"error": "Value(s) not found"}',
    })
    quotes = collect_market(market_entries, fetch=fetch, now=NOW)
    ids = [q.id for q in quotes]
    assert ids == ["usd", "eur", "ibov", "ifix", "spx", "brent", "gold", "ipca12m", "incc12m"]


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"null",
        b'{"chart": {"result": [], "error": null}}',
        b'{"chart": {"result": [{"meta": {"regularMarketPrice": null}, "indicators": {"quote": [{"close": []}]}}]}}',
        b'{"chart": {"result": [{"meta": {"regularMarketPrice": -3}}]}}',
        b'{"chart": {"result": [{"meta": {"regularMarketPrice": "NaN"}}]}}',
    ],
)
def test_invalid_yahoo_payloads_are_omitted(market_entries: list[dict], payload: bytes) -> None:
    fetch = FakeFetch({yahoo("%5EGSPC"): payload})
    assert collect_market([entry(market_entries, "spx")], fetch=fetch, now=NOW) == []


def test_yahoo_price_falls_back_to_last_close() -> None:
    payload = {"chart": {"result": [{"meta": {"regularMarketTime": 1790626740},
                                     "indicators": {"quote": [{"close": [100.0, None, 102.0]}]}}], "error": None}}
    fetch = FakeFetch({yahoo("%5ETEST"): json.dumps(payload).encode()})
    spec = {"id": "t", "label": "Teste", "kind": "index", "provider": "yahoo", "symbol": "^TEST",
            "format": "pts", "decimals": 1}
    [quote] = collect_market([spec], fetch=fetch, now=NOW)
    assert (quote.value, quote.display) == (102.0, "102,0 pts")
    assert quote.change_pct == pytest.approx(2.0)


def test_indicator_kinds_never_have_change(market_entries: list[dict]) -> None:
    spec = {"id": "cdi", "label": "CDI", "kind": "rate", "provider": "bcb_sgs", "series": 1, "transform": "last",
            "format": "pct", "decimals": 2}
    [quote] = collect_market([spec], fetch=FakeFetch(), now=NOW)
    assert quote.change_pct is None and quote.display == "5,21%"


@pytest.mark.parametrize(
    "spec",
    [
        {"id": "x", "label": "X", "provider": "desconhecido", "format": "pts", "decimals": 0},
        {"id": "x", "label": "X", "provider": "bcb_sgs", "series": 189, "transform": "media", "format": "pct",
         "decimals": 2},
        {"id": "x", "label": "X", "provider": "yahoo", "format": "pts", "decimals": 0},  # sem símbolo
    ],
)
def test_invalid_specs_are_omitted(spec: dict) -> None:
    assert collect_market([spec], fetch=FakeFetch(), now=NOW) == []


def test_empty_entries_and_naive_now() -> None:
    assert collect_market([], fetch=FakeFetch(), now=NOW) == []
    spec = {"id": "btc", "label": "Bitcoin", "kind": "crypto", "provider": "coingecko", "coin": "bitcoin",
            "format": "usd", "decimals": 0}
    [quote] = collect_market([spec], fetch=FakeFetch(), now=NOW.replace(tzinfo=None))
    assert quote.as_of == NOW.isoformat()


# ── variação alinhada ao preço exibido ───────────────────────────────────────


def _yahoo_payload(closes: list, timestamps: list[int], price: float | None, **meta) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {"regularMarketPrice": price, "exchangeTimezoneName": "America/Sao_Paulo", **meta},
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ],
            "error": None,
        }
    }


DAY = 86_400
T0 = 1_790_049_600  # 2026-09-22 03:00 UTC (meia-noite em São Paulo)


def _yahoo_change(payload: dict) -> float | None:
    from qijournal.collect.market import _from_yahoo

    class Fetch:
        def __call__(self, url, **kwargs):
            return net.Response(url=url, status=200, content=json.dumps(payload).encode(), headers={})

    return _from_yahoo({"symbol": "USDBRL=X"}, Fetch(), NOW)[1]


def test_yahoo_change_ignores_todays_null_candle():
    # candle de hoje sem fechamento: variação = preço de agora × último fechamento (5,22), não a de ontem
    payload = _yahoo_payload([5.10, 5.14, 5.22, None], [T0, T0 + DAY, T0 + 2 * DAY, T0 + 3 * DAY], 5.30)
    assert _yahoo_change(payload) == pytest.approx((5.30 / 5.22 - 1) * 100)
    assert round(_yahoo_change(payload), 2) == 1.53


def test_yahoo_change_with_duplicated_last_row():
    # a última linha repetida (mesmo dia) não vira variação 0%
    payload = _yahoo_payload([5.10, 5.14, 5.22, 5.22], [T0, T0 + DAY, T0 + 2 * DAY, T0 + 2 * DAY + 600], 5.22)
    assert _yahoo_change(payload) == pytest.approx((5.22 / 5.14 - 1) * 100)


def test_yahoo_change_without_timestamps_keeps_the_old_rule():
    payload = _yahoo_payload([5.10, 5.14, 5.22], [], 5.22)
    assert _yahoo_change(payload) == pytest.approx((5.22 / 5.14 - 1) * 100)
