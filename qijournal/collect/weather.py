"""Clima de hoje e amanhã por cidade, via Open-Meteo (sem chave de API)."""

from __future__ import annotations

import logging
import math
from typing import Any
from urllib.parse import quote

from .. import net
from ..config import CityConfig
from ..models import CityWeather
from ..net import Fetcher

log = logging.getLogger(__name__)

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,weather_code"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    "&timezone={tz}&forecast_days=2"
)
REQUEST_TIMEOUT = 15.0
REQUEST_RETRIES = 2

UNKNOWN_WEATHER = ("🌡️", "Sem dados")

# Códigos WMO usados pelo Open-Meteo → (emoji, descrição em pt-BR).
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("☀️", "Céu limpo"),
    1: ("🌤️", "Predominantemente limpo"),
    2: ("⛅", "Parcialmente nublado"),
    3: ("☁️", "Nublado"),
    45: ("🌫️", "Nevoeiro"),
    48: ("🌫️", "Nevoeiro com geada"),
    51: ("🌦️", "Garoa fraca"),
    53: ("🌦️", "Garoa moderada"),
    55: ("🌦️", "Garoa intensa"),
    56: ("🌦️", "Garoa congelante fraca"),
    57: ("🌦️", "Garoa congelante intensa"),
    61: ("🌧️", "Chuva fraca"),
    63: ("🌧️", "Chuva moderada"),
    65: ("🌧️", "Chuva forte"),
    66: ("🌧️", "Chuva congelante fraca"),
    67: ("🌧️", "Chuva congelante forte"),
    71: ("🌨️", "Neve fraca"),
    73: ("🌨️", "Neve moderada"),
    75: ("🌨️", "Neve forte"),
    77: ("🌨️", "Grãos de neve"),
    80: ("🌦️", "Pancadas de chuva fracas"),
    81: ("🌦️", "Pancadas de chuva moderadas"),
    82: ("🌦️", "Pancadas de chuva fortes"),
    85: ("🌨️", "Pancadas de neve fracas"),
    86: ("🌨️", "Pancadas de neve fortes"),
    95: ("⛈️", "Trovoadas"),
    96: ("⛈️", "Trovoadas com granizo fraco"),
    99: ("⛈️", "Trovoadas com granizo forte"),
}


def wmo_to_emoji_desc(code: int | None) -> tuple[str, str]:
    """Converte um código de tempo WMO em ``(emoji, descrição pt-BR)``.

    ``None`` ou código desconhecido → ``("🌡️", "Sem dados")``.
    """
    if code is None or isinstance(code, bool):
        return UNKNOWN_WEATHER
    try:
        return WMO_CODES.get(int(code), UNKNOWN_WEATHER)
    except (TypeError, ValueError):
        return UNKNOWN_WEATHER


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1) if math.isfinite(number) else None


def _code(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _at(values: Any, index: int) -> Any:
    if isinstance(values, list) and len(values) > index:
        return values[index]
    return None


def _format_coord(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def forecast_url(city: CityConfig) -> str:
    """URL da previsão do Open-Meteo para a cidade (fuso da cidade, 2 dias)."""
    return OPEN_METEO_URL.format(
        lat=_format_coord(city.lat), lon=_format_coord(city.lon), tz=quote(city.timezone, safe="")
    )


def parse_forecast(city_name: str, data: dict[str, Any]) -> CityWeather:
    """Converte a resposta do Open-Meteo (índice 0 = hoje, 1 = amanhã) em :class:`CityWeather`.

    Levanta ``ValueError`` se não houver temperatura atual nem mínima/máxima de hoje.
    """
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    current_c = _number(current.get("temperature_2m"))
    today_min = _number(_at(daily.get("temperature_2m_min"), 0))
    today_max = _number(_at(daily.get("temperature_2m_max"), 0))
    if current_c is None and today_min is None and today_max is None:
        raise ValueError(f"previsão sem temperaturas para {city_name}")

    current_code = _code(current.get("weather_code"))
    if current_code is None:
        current_code = _code(_at(daily.get("weather_code"), 0))
    current_emoji, current_desc = wmo_to_emoji_desc(current_code)
    tomorrow_emoji, tomorrow_desc = wmo_to_emoji_desc(_code(_at(daily.get("weather_code"), 1)))
    return CityWeather(
        city=city_name,
        current_c=current_c,
        current_emoji=current_emoji,
        current_desc=current_desc,
        today_min=today_min,
        today_max=today_max,
        tomorrow_min=_number(_at(daily.get("temperature_2m_min"), 1)),
        tomorrow_max=_number(_at(daily.get("temperature_2m_max"), 1)),
        tomorrow_emoji=tomorrow_emoji,
        tomorrow_desc=tomorrow_desc,
    )


def _city_weather(city: CityConfig, fetch: Fetcher) -> CityWeather | None:
    try:
        data = fetch(forecast_url(city), timeout=REQUEST_TIMEOUT, retries=REQUEST_RETRIES).json()
        if not isinstance(data, dict):
            raise ValueError("resposta do Open-Meteo não é um objeto JSON")
        return parse_forecast(city.name, data)
    except Exception as exc:  # noqa: BLE001 — cidade com problema é apenas omitida
        log.warning("clima de %s omitido: %s", city.name, exc)
        return None


def collect_weather(cities: list[CityConfig], *, fetch: Fetcher = net.fetch) -> list[CityWeather]:
    """Clima de cada cidade, na ordem da configuração; cidades com falha são omitidas."""
    results = net.parallel_map(lambda city: _city_weather(city, fetch), cities, max_workers=4)
    weather = [w for w in results if w is not None]
    log.info("clima: %d/%d cidades", len(weather), len(cities))
    return weather
