"""Validação do bundle de exemplo (tests/fixtures/bundle.json) usado pelo CI e pelos testes de edição/render."""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from qijournal import text
from qijournal.collect.feeds import article_id, canonical_url, is_usable_image_url
from qijournal.collect.market import format_value
from qijournal.config import load_config
from qijournal.models import Bundle

BUNDLE_PATH = Path(__file__).parent / "fixtures" / "bundle.json"


@pytest.fixture(scope="module")
def bundle() -> Bundle:
    return Bundle.from_dict(json.loads(BUNDLE_PATH.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


def test_bundle_roundtrip(bundle: Bundle) -> None:
    raw = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    assert bundle.to_dict() == raw
    assert bundle.collected_at == "2026-09-29T08:07:00+00:00"


def test_articles_volume_and_diversity(bundle: Bundle) -> None:
    articles = bundle.articles
    assert len(articles) >= 70
    assert len({a.source_id for a in articles}) >= 12
    langs = Counter(a.lang for a in articles)
    assert langs["pt"] >= 40 and langs["en"] >= 20 and langs["es"] >= 2
    with_image = sum(1 for a in articles if a.image)
    assert 0.5 * len(articles) < with_image < len(articles)


def test_articles_are_consistent_with_collector(bundle: Bundle, config) -> None:
    sources_by_url = {s.url: s for s in config.sources}
    assert len({a.id for a in bundle.articles}) == len(bundle.articles)
    for a in bundle.articles:
        assert a.url == canonical_url(a.url) and a.id == article_id(a.url)
        source = sources_by_url[a.feed_url]
        assert (a.source_id, a.source_name, a.lang, a.weight, a.topics) == (
            source.id, source.name, source.lang, source.weight, source.topics)
        assert not any(p.lower() in a.url.lower() for p in config.edition.exclude_url_patterns)
        assert a.image is None or is_usable_image_url(a.image)


def test_articles_text_is_plain_and_sized(bundle: Bundle) -> None:
    for a in bundle.articles:
        assert a.title and a.title == " ".join(a.title.split())
        assert 200 <= len(a.summary) <= 1500, a.title
        for field in (a.title, a.summary):
            assert not re.search(r"<\s*/?\s*[a-zA-Z][^<>]*>", field)
            assert "**" not in field and "&amp;" not in field and "&quot;" not in field


def test_article_dates_relative_to_collection(bundle: Bundle) -> None:
    collected = datetime.fromisoformat(bundle.collected_at)
    ages = [collected - datetime.fromisoformat(a.published) for a in bundle.articles]
    assert all(timedelta(hours=0.5) <= age <= timedelta(hours=28) for age in ages)
    assert min(ages) < timedelta(hours=2) and max(ages) > timedelta(hours=20)


def test_all_sections_have_material(bundle: Bundle, config) -> None:
    """Cada seção tem artigos, seja pela dica de tópico da fonte, seja por palavras-chave."""
    for section in config.sections:
        keywords = [text.normalize(k) for k in section.keywords]
        hits = [
            a for a in bundle.articles
            if section.id in a.topics
            or any(re.search(rf"\b{re.escape(k)}", text.normalize(f"{a.title} {a.summary}")) for k in keywords)
        ]
        assert len(hits) >= 5, section.id


def test_same_fact_covered_by_several_outlets(bundle: Bundle) -> None:
    """Há fatos cobertos por veículos diferentes com títulos parecidos (casos de agrupamento)."""
    similar = [
        (a.source_id, b.source_id)
        for a, b in itertools.combinations(bundle.articles, 2)
        if a.source_id != b.source_id and text.similarity(a.title, b.title) >= 0.42
    ]
    assert len(similar) >= 8
    openai = [a for a in bundle.articles if "OpenAI" in a.title]
    assert len({a.source_id for a in openai}) >= 4 and {a.lang for a in openai} == {"pt", "en"}


def test_quotes_follow_config_and_formatting(bundle: Bundle, config) -> None:
    assert [q.id for q in bundle.quotes] == [e["id"] for e in config.market]
    for quote in bundle.quotes:
        spec = next(e for e in config.market if e["id"] == quote.id)
        assert quote.label == spec["label"] and quote.kind == spec["kind"]
        assert quote.display == format_value(quote.value, spec["format"], spec["decimals"])
        if quote.kind in ("rate", "inflation"):
            assert quote.change_pct is None
        else:
            assert isinstance(quote.change_pct, float)
        assert quote.source in {"Yahoo Finance", "CoinGecko", "Banco Central (SGS)"}


def test_weather_for_config_cities(bundle: Bundle, config) -> None:
    assert [w.city for w in bundle.weather] == [c.name for c in config.weather]
    for w in bundle.weather:
        assert w.current_emoji and w.tomorrow_emoji and w.today_min <= w.today_max


def test_source_statuses(bundle: Bundle, config) -> None:
    assert [s.url for s in bundle.sources] == [s.url for s in config.sources]
    failed = [s for s in bundle.sources if not s.ok]
    assert 3 <= len(failed) <= 8
    assert all(s.error and s.items == 0 for s in failed)
    assert any(s.error == "HTTP 403" for s in failed)
    per_feed = Counter(a.feed_url for a in bundle.articles)
    for status in bundle.sources:
        if status.ok:
            assert status.error is None and status.items == per_feed[status.url] >= 1
        assert per_feed[status.url] == 0 or status.ok
