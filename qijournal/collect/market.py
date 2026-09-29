"""Cotações e indicadores do ticker (Yahoo Finance, CoinGecko e SGS do Banco Central).

Cada entrada de ``config/site.yaml → market`` é independente: se o provedor
principal falhar, tenta-se o ``fallback`` (quando existir); se ambos falharem,
a entrada é omitida com um aviso no log. :func:`collect_market` nunca levanta
exceção e preserva a ordem da configuração.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import quote

from .. import net, text
from ..models import Quote
from ..net import Fetcher

log = logging.getLogger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd&include_24hr_change=true"
)
BCB_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados/ultimos/{n}?formato=json"

SOURCE_YAHOO = "Yahoo Finance"
SOURCE_COINGECKO = "CoinGecko"
SOURCE_BCB = "Banco Central (SGS)"

REQUEST_TIMEOUT = 15.0
REQUEST_RETRIES = 2
NO_CHANGE_KINDS = {"rate", "inflation"}  # indicadores sem variação diária
_SPEC_BASE_KEYS = ("id", "label", "kind", "format", "decimals")


# ── Formatação ───────────────────────────────────────────────────────────────


def format_value(value: float, fmt: str, decimals: int) -> str:
    """Formata no padrão brasileiro conforme ``fmt``.

    ``brl`` → ``"R$ 5,22"``; ``usd`` → ``"US$ 83.061"``; ``pts`` → ``"182.991 pts"``;
    ``pct`` → ``"4,22%"``. Formato desconhecido → só o número.
    """
    number = text.format_number_pt(value, decimals)
    if fmt == "brl":
        return f"R$ {number}"
    if fmt == "usd":
        return f"US$ {number}"
    if fmt == "pts":
        return f"{number} pts"
    if fmt == "pct":
        return f"{number}%"
    return number


def format_change(pct: float) -> str:
    """Variação percentual com sinal: ``"+0,19%"``, ``"-1,16%"`` ou ``"0,00%"``."""
    rounded = round(pct, 2)
    if rounded == 0:
        return "0,00%"
    sign = "+" if rounded > 0 else "-"
    return f"{sign}{text.format_number_pt(abs(rounded), 2)}%"


def accum12(monthly_pcts: list[float]) -> float:
    """Acumulado dos últimos 12 meses a partir de variações mensais em %.

    ``(Π(1 + m/100) - 1) * 100`` sobre os 12 valores mais recentes (fim da lista).
    Levanta ``ValueError`` com menos de 12 valores.
    """
    if len(monthly_pcts) < 12:
        raise ValueError(f"acumulado de 12 meses exige 12 valores (recebidos {len(monthly_pcts)})")
    factor = 1.0
    for monthly in monthly_pcts[-12:]:
        factor *= 1 + float(monthly) / 100
    return (factor - 1) * 100


# ── Auxiliares ───────────────────────────────────────────────────────────────


def _to_float(value: Any) -> float | None:
    """Número finito ou ``None`` (aceita strings com ponto ou vírgula decimal)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct_change(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return (current / previous - 1) * 100


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _get_json(fetch: Fetcher, url: str) -> Any:
    return fetch(url, timeout=REQUEST_TIMEOUT, retries=REQUEST_RETRIES).json()


def _get_object(fetch: Fetcher, url: str, provider: str) -> dict[str, Any]:
    data = _get_json(fetch, url)
    if not isinstance(data, dict):
        raise ValueError(f"resposta inesperada do {provider} (esperado objeto JSON)")
    return data


# ── Provedores ───────────────────────────────────────────────────────────────
# Cada provedor devolve (valor, variação % ou None, as_of) ou levanta exceção.

_Result = tuple[float, float | None, str]


def _from_yahoo(spec: dict[str, Any], fetch: Fetcher, now: datetime) -> _Result:
    symbol = str(spec["symbol"])
    data = _get_object(fetch, YAHOO_URL.format(symbol=quote(symbol, safe="")), SOURCE_YAHOO)
    chart = data.get("chart") or {}
    if chart.get("error"):
        error = chart["error"]
        detail = error.get("description") if isinstance(error, dict) else error
        raise ValueError(f"Yahoo recusou {symbol}: {detail}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Yahoo sem resultado para {symbol}")
    result = results[0]
    meta = result.get("meta") or {}

    quotes = (result.get("indicators") or {}).get("quote") or [{}]
    closes = [c for c in (_to_float(v) for v in quotes[0].get("close") or []) if c is not None]
    price = _to_float(meta.get("regularMarketPrice"))
    if price is None and closes:
        price = closes[-1]
    if price is None or price <= 0:
        raise ValueError(f"Yahoo sem preço válido para {symbol}")

    change = _to_float(meta.get("regularMarketChangePercent"))
    if change is None and len(closes) >= 2:
        change = _pct_change(closes[-1], closes[-2])

    market_time = _to_float(meta.get("regularMarketTime"))
    if market_time:
        as_of = datetime.fromtimestamp(int(market_time), tz=timezone.utc).isoformat()
    else:
        as_of = now.replace(microsecond=0).isoformat()
    return price, change, as_of


def _from_coingecko(spec: dict[str, Any], fetch: Fetcher, now: datetime) -> _Result:
    coin = str(spec["coin"])
    data = _get_object(fetch, COINGECKO_URL.format(coin=quote(coin, safe="")), SOURCE_COINGECKO)
    node = data.get(coin) or {}
    price = _to_float(node.get("usd"))
    if price is None or price <= 0:
        raise ValueError(f"CoinGecko sem preço para {coin}")
    return price, _to_float(node.get("usd_24h_change")), now.replace(microsecond=0).isoformat()


def _parse_sgs_rows(rows: Any) -> list[tuple[date, float]]:
    """``[{"data": "01/08/2026", "valor": "4.22"}, ...]`` → pontos ordenados por data."""
    if not isinstance(rows, list):
        raise ValueError("resposta do SGS não é uma lista")
    points: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _to_float(row.get("valor"))
        try:
            day, month, year = (int(part) for part in str(row.get("data", "")).split("/"))
            when = date(year, month, day)
        except ValueError:
            continue
        if value is not None:
            points.append((when, value))
    points.sort(key=lambda point: point[0])
    return points


def _sgs_as_of(points: list[tuple[date, float]], now: datetime) -> str:
    """Série mensal (todas as datas no dia 1) → ``"AAAA-MM"``; diária → ``"AAAA-MM-DD"``.

    Séries diárias como a meta Selic trazem datas futuras (vigência); a data de
    referência é limitada ao dia de ``now``.
    """
    last = points[-1][0]
    if all(when.day == 1 for when, _ in points):
        return f"{last.year:04d}-{last.month:02d}"
    return min(last, now.date()).isoformat()


def _from_bcb_sgs(spec: dict[str, Any], fetch: Fetcher, now: datetime) -> _Result:
    series = int(spec["series"])
    transform = spec.get("transform", "last")
    if transform not in ("last", "accum12"):
        raise ValueError(f"transform desconhecido para a série {series}: {transform}")
    n = 12 if transform == "accum12" else 2
    points = _parse_sgs_rows(_get_json(fetch, BCB_SGS_URL.format(series=series, n=n)))
    if not points:
        raise ValueError(f"SGS {series} sem valores")
    values = [value for _, value in points]
    if transform == "accum12":
        if len(values) < 12:
            raise ValueError(f"SGS {series}: esperados 12 valores mensais, recebidos {len(values)}")
        return accum12(values), None, _sgs_as_of(points, now)
    change = _pct_change(values[-1], values[-2]) if len(values) >= 2 else None
    return values[-1], change, _sgs_as_of(points, now)


_PROVIDERS: dict[str, tuple[Callable[[dict[str, Any], Fetcher, datetime], _Result], str]] = {
    "yahoo": (_from_yahoo, SOURCE_YAHOO),
    "coingecko": (_from_coingecko, SOURCE_COINGECKO),
    "bcb_sgs": (_from_bcb_sgs, SOURCE_BCB),
}


# ── Coleta ───────────────────────────────────────────────────────────────────


def _quote_from_spec(spec: dict[str, Any], fetch: Fetcher, now: datetime) -> Quote:
    provider = spec.get("provider")
    if provider not in _PROVIDERS:
        raise ValueError(f"provedor desconhecido: {provider}")
    handler, source = _PROVIDERS[provider]
    value, change, as_of = handler(spec, fetch, now)
    kind = spec.get("kind") or "quote"
    if kind in NO_CHANGE_KINDS:
        change = None
    return Quote(
        id=str(spec["id"]),
        label=str(spec.get("label") or spec["id"]),
        value=value,
        display=format_value(value, str(spec.get("format", "")), int(spec.get("decimals", 2))),
        change_pct=round(change, 4) if change is not None else None,
        kind=kind,
        as_of=as_of,
        source=source,
    )


def _attempts(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Especificação principal e, se houver, a do fallback (herdando id/rótulo/tipo/formato)."""
    attempts = [entry]
    fallback = entry.get("fallback")
    if isinstance(fallback, dict):
        base = {key: entry[key] for key in _SPEC_BASE_KEYS if key in entry}
        attempts.append({**base, **fallback})
    return attempts


def _collect_entry(entry: dict[str, Any], fetch: Fetcher, now: datetime) -> Quote | None:
    entry_id = entry.get("id", "?")
    for number, spec in enumerate(_attempts(entry)):
        try:
            quote_ = _quote_from_spec(spec, fetch, now)
        except Exception as exc:  # noqa: BLE001 — uma cotação com problema nunca derruba as demais
            log.warning("cotação %s via %s falhou: %s", entry_id, spec.get("provider"), exc)
            continue
        if number:
            log.info("cotação %s obtida pelo fallback (%s)", entry_id, spec.get("provider"))
        return quote_
    log.warning("cotação %s omitida: todos os provedores falharam", entry_id)
    return None


def collect_market(entries: list[dict], *, fetch: Fetcher = net.fetch, now: datetime) -> list[Quote]:
    """Coleta as cotações da configuração, em paralelo, na ordem da configuração."""
    now = _as_utc(now)
    results = net.parallel_map(lambda entry: _collect_entry(entry, fetch, now), entries, max_workers=8)
    quotes = [quote_ for quote_ in results if quote_ is not None]
    log.info("mercado: %d/%d cotações", len(quotes), len(entries))
    return quotes
