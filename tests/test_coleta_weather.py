"""Testes do clima (qijournal.collect.weather) — sem rede, com respostas reais do Open-Meteo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qijournal import net
from qijournal.collect.weather import WMO_CODES, collect_weather, forecast_url, parse_forecast, wmo_to_emoji_desc
from qijournal.config import CityConfig, load_config
from qijournal.models import CityWeather

WEATHER = Path(__file__).parent / "fixtures" / "coleta" / "weather"
SAO_PAULO = CityConfig(name="São Paulo", lat=-23.55, lon=-46.63, timezone="America/Sao_Paulo")
MONTEVIDEO = CityConfig(name="Montevidéu", lat=-34.90, lon=-56.16, timezone="America/Montevideo")


def fixture(name: str) -> dict:
    return json.loads((WEATHER / name).read_text(encoding="utf-8"))


class FakeFetch:
    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> net.Response:
        self.calls.append((url, kwargs))
        result = self.routes.get(url)
        if result is None:
            raise net.FetchError(url, "HTTP 404", status=404)
        if isinstance(result, BaseException):
            raise result
        content = result if isinstance(result, bytes) else json.dumps(result).encode()
        return net.Response(url=url, status=200, content=content, headers={"content-type": "application/json"})


# ── tabela WMO ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code, emoji, desc",
    [
        (0, "☀️", "Céu limpo"),
        (1, "🌤️", "Predominantemente limpo"),
        (2, "⛅", "Parcialmente nublado"),
        (3, "☁️", "Nublado"),
        (45, "🌫️", "Nevoeiro"),
        (48, "🌫️", "Nevoeiro com geada"),
        (51, "🌦️", "Garoa fraca"),
        (53, "🌦️", "Garoa moderada"),
        (57, "🌦️", "Garoa congelante intensa"),
        (61, "🌧️", "Chuva fraca"),
        (65, "🌧️", "Chuva forte"),
        (67, "🌧️", "Chuva congelante forte"),
        (71, "🌨️", "Neve fraca"),
        (77, "🌨️", "Grãos de neve"),
        (80, "🌦️", "Pancadas de chuva fracas"),
        (82, "🌦️", "Pancadas de chuva fortes"),
        (85, "🌨️", "Pancadas de neve fracas"),
        (86, "🌨️", "Pancadas de neve fortes"),
        (95, "⛈️", "Trovoadas"),
        (96, "⛈️", "Trovoadas com granizo fraco"),
        (99, "⛈️", "Trovoadas com granizo forte"),
    ],
)
def test_wmo_codes(code: int, emoji: str, desc: str) -> None:
    assert wmo_to_emoji_desc(code) == (emoji, desc)


def test_wmo_table_is_complete() -> None:
    expected = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86,
                95, 96, 99}
    assert set(WMO_CODES) == expected
    assert all(emoji and desc for emoji, desc in WMO_CODES.values())


@pytest.mark.parametrize("code", [None, 42, -1, 100, True, "abc"])
def test_wmo_unknown_codes(code: object) -> None:
    assert wmo_to_emoji_desc(code) == ("🌡️", "Sem dados")


def test_wmo_accepts_float_and_numeric_string() -> None:
    assert wmo_to_emoji_desc(3.0) == ("☁️", "Nublado")
    assert wmo_to_emoji_desc("95") == ("⛈️", "Trovoadas")


# ── URL e parsing ────────────────────────────────────────────────────────────


def test_forecast_url() -> None:
    assert forecast_url(SAO_PAULO) == (
        "https://api.open-meteo.com/v1/forecast?latitude=-23.55&longitude=-46.63"
        "&current=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min"
        "&timezone=America%2FSao_Paulo&forecast_days=2"
    )
    assert "latitude=-34.9&longitude=-56.16" in forecast_url(MONTEVIDEO)
    assert "timezone=America%2FMontevideo" in forecast_url(MONTEVIDEO)


def test_parse_forecast_today_and_tomorrow() -> None:
    weather = parse_forecast("São Paulo", fixture("openmeteo_sao_paulo.json"))
    assert weather == CityWeather(
        city="São Paulo", current_c=18.9, current_emoji="☁️", current_desc="Nublado",
        today_min=18.8, today_max=32.6, tomorrow_min=19.3, tomorrow_max=29.8,
        tomorrow_emoji="🌦️", tomorrow_desc="Pancadas de chuva fracas",
    )


def test_parse_forecast_missing_current_code_uses_today_code() -> None:
    data = fixture("openmeteo_sao_paulo.json")
    data["current"].pop("weather_code")
    weather = parse_forecast("São Paulo", data)
    assert (weather.current_emoji, weather.current_desc) == ("⛈️", "Trovoadas")


def test_parse_forecast_without_tomorrow() -> None:
    data = fixture("openmeteo_montevideo.json")
    for key in ("weather_code", "temperature_2m_max", "temperature_2m_min"):
        data["daily"][key] = data["daily"][key][:1]
    weather = parse_forecast("Montevidéu", data)
    assert weather.today_max == 16.8
    assert (weather.tomorrow_min, weather.tomorrow_max) == (None, None)
    assert (weather.tomorrow_emoji, weather.tomorrow_desc) == ("🌡️", "Sem dados")


def test_parse_forecast_null_values_and_rounding() -> None:
    data = {"current": {"temperature_2m": 21.4567, "weather_code": None},
            "daily": {"weather_code": [None, 61], "temperature_2m_max": [None, 25.04],
                      "temperature_2m_min": [None, 15.96]}}
    weather = parse_forecast("Rio", data)
    assert weather.current_c == 21.5
    assert (weather.current_emoji, weather.current_desc) == ("🌡️", "Sem dados")
    assert (weather.today_min, weather.today_max) == (None, None)
    assert (weather.tomorrow_min, weather.tomorrow_max, weather.tomorrow_desc) == (16.0, 25.0, "Chuva fraca")


@pytest.mark.parametrize("data", [{}, {"current": {}, "daily": {}}, {"daily": {"temperature_2m_max": []}}])
def test_parse_forecast_without_temperatures_raises(data: dict) -> None:
    with pytest.raises(ValueError):
        parse_forecast("X", data)


# ── collect_weather ──────────────────────────────────────────────────────────


def test_collect_weather_config_cities_in_order() -> None:
    cities = load_config(env={}).weather
    fetch = FakeFetch({
        forecast_url(cities[0]): fixture("openmeteo_sao_paulo.json"),
        forecast_url(cities[1]): fixture("openmeteo_montevideo.json"),
    })
    weather = collect_weather(cities, fetch=fetch)
    assert [w.city for w in weather] == ["São Paulo", "Montevidéu"]
    mvd = weather[1]
    assert (mvd.current_c, mvd.current_emoji, mvd.current_desc) == (13.9, "🌦️", "Garoa moderada")
    assert (mvd.tomorrow_min, mvd.tomorrow_max, mvd.tomorrow_emoji) == (11.6, 18.9, "⛅")
    assert all(kwargs.get("timeout") and "retries" in kwargs for _, kwargs in fetch.calls)


@pytest.mark.parametrize(
    "failure",
    [
        net.FetchError("https://api.open-meteo.com/", "HTTP 502", status=502),
        RuntimeError("falha inesperada"),
        b"<html>erro</html>",
        b"[1, 2, 3]",
        {"error": True, "reason": "Latitude must be in range of -90 to 90"},
    ],
)
def test_failed_city_is_omitted(failure: object) -> None:
    fetch = FakeFetch({forecast_url(SAO_PAULO): failure, forecast_url(MONTEVIDEO): fixture("openmeteo_montevideo.json")})
    weather = collect_weather([SAO_PAULO, MONTEVIDEO], fetch=fetch)
    assert [w.city for w in weather] == ["Montevidéu"]


def test_collect_weather_empty() -> None:
    assert collect_weather([], fetch=FakeFetch({})) == []
