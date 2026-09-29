"""Objetos de teste para o pipeline: edições, bundles e renderizadores falsos.

Os testes de pipeline substituem (monkeypatch) o editor e os renderizadores por
estas versões simples, para exercitar só a orquestração: arquivos, arquivo
histórico, e-mail e relatórios.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from xml.sax.saxutils import escape

from qijournal.config import Config, load_config
from qijournal.models import (
    Article,
    Bundle,
    CityWeather,
    Edition,
    EditionStats,
    Quote,
    Section,
    SourceRef,
    SourceStatus,
    Story,
)
from qijournal.text import pt_date_label

NOW = datetime(2026, 9, 29, 8, 7, tzinfo=UTC)  # 05:07 em Brasília
COLLECTED_AT = "2026-09-29T08:07:00+00:00"

LATEST_KEYS = {
    "date",
    "date_label",
    "generated_at",
    "mode",
    "model",
    "url",
    "edition_url",
    "subject",
    "email_html",
    "email_text",
    "email_sent",
    "email_sent_at",
    "email_channel",
    "lead_headline",
    "stories",
    "sources_ok",
    "sources_total",
}


def make_config(**edition_overrides: Any) -> Config:
    """Configuração real do repositório, sem influência do ambiente do processo."""
    config = load_config(env={})
    if edition_overrides:
        config.edition = dataclasses.replace(config.edition, **edition_overrides)
    return config


def make_story(index: int, *, section: str = "brasil", headline: str | None = None) -> Story:
    return Story(
        id=f"materia-{index}",
        section=section,
        headline=headline or f"Manchete número {index} sobre a economia",
        dek=f"Linha fina da matéria {index}.",
        body=[f"Primeiro parágrafo da matéria {index} com **destaque**.", "Segundo parágrafo."],
        sources=[SourceRef(name="Valor Econômico", url=f"https://valor.globo.com/materia-{index}")],
        article_ids=[f"art{index}"],
        importance=5 if index == 1 else 3,
        why_it_matters="Afeta o custo do crédito.",
        image=None,
        published="2026-09-29T06:00:00+00:00",
    )


def make_edition(
    date: str = "2026-09-29",
    *,
    lead_headline: str = "Copom mantém a Selic e sinaliza cautela",
    mode: str = "heuristic",
    model: str | None = None,
    stories: int = 4,
    stats: EditionStats | None = None,
    generated_at: str = COLLECTED_AT,
) -> Edition:
    """Edição mínima e válida (a manchete é ``materia-1``)."""
    items = [make_story(1, headline=lead_headline)] + [
        make_story(i, section="mercados" if i % 2 else "mundo") for i in range(2, stories + 1)
    ]
    sections: dict[str, list[str]] = {}
    for story in items:
        sections.setdefault(story.section, []).append(story.id)
    return Edition(
        date=date,
        date_label=pt_date_label(datetime.fromisoformat(date)),
        generated_at=generated_at,
        mode=mode,
        model=model,
        editorial="",
        briefing=[s.headline for s in items[:3]],
        lead=items[0].id,
        secondary=[s.id for s in items[1:4]],
        highlights=[],
        sections=[Section(id=sid, title=sid.title(), color="#1C49A5", story_ids=ids) for sid, ids in sections.items()],
        stories=items,
        quotes=[],
        weather=[],
        stats=stats or EditionStats(sources_total=6, sources_ok=5, articles_collected=20, articles_considered=20),
    )


def make_article(index: int, *, source_id: str = "valor", url: str | None = None) -> Article:
    return Article(
        id=f"art{index:03d}",
        url=url or f"https://valor.globo.com/brasil/noticia/{index}.ghtml",
        title=f"Notícia número {index} sobre juros e inflação",
        summary=f"Resumo da notícia {index}.",
        source_id=source_id,
        source_name=source_id.title(),
        lang="pt",
        published=(NOW - timedelta(hours=index % 12)).isoformat(),
        topics=["brasil"],
        feed_url=f"https://{source_id}.example/rss",
    )


def make_bundle(
    *, articles: int = 20, sources_ok: int = 5, sources_failed: int = 1, collected_at: str = COLLECTED_AT
) -> Bundle:
    statuses = [
        SourceStatus(
            source_id=f"fonte{i}", source_name=f"Fonte {i}", url=f"https://fonte{i}.example/rss", ok=True, items=4
        )
        for i in range(sources_ok)
    ] + [
        SourceStatus(
            source_id=f"quebrada{i}",
            source_name=f"Quebrada {i}",
            url=f"https://quebrada{i}.example/rss",
            ok=False,
            error="HTTP 403",
        )
        for i in range(sources_failed)
    ]
    return Bundle(
        collected_at=collected_at,
        articles=[make_article(i) for i in range(articles)],
        quotes=[Quote(id="usd", label="Dólar", value=5.22, display="R$ 5,22", change_pct=0.19, kind="fx")],
        weather=[
            CityWeather(
                city="São Paulo",
                current_c=19.9,
                current_emoji="☁️",
                current_desc="nublado",
                today_min=17.9,
                today_max=31.6,
                tomorrow_min=18.8,
                tomorrow_max=32.6,
                tomorrow_emoji="⛈️",
                tomorrow_desc="trovoadas",
            )
        ],
        sources=statuses,
    )


class FakeRender:
    """Substitui ``render_edition_page``/``render_archive_index``/``render_email``."""

    def __init__(self) -> None:
        self.pages: list[tuple[str, str, str]] = []  # (data, home_href, archive_href)
        self.archives: list[list[dict[str, Any]]] = []
        self.emails: list[str] = []

    def edition_page(self, edition: Edition, config: Config, *, home_href: str, archive_href: str) -> str:
        self.pages.append((edition.date, home_href, archive_href))
        headline = edition.story(edition.lead).headline
        return f"<html><h1>{headline}</h1><a href='{home_href}'>capa</a><a href='{archive_href}'>arquivo</a></html>"

    def archive_index(self, entries: list[dict[str, Any]], config: Config, *, home_href: str) -> str:
        self.archives.append(entries)
        items = "".join(f"<li>{e['date']} {e['lead_headline']}</li>" for e in entries)
        return f"<html><a href='{home_href}'>capa</a><ul>{items}</ul></html>"

    def email(self, edition: Edition, config: Config) -> tuple[str, str, str]:
        self.emails.append(edition.date)
        subject = f"{config.brand.name} — {edition.date_label}"
        return subject, f"<html>e-mail {edition.date}</html>", f"e-mail {edition.date}"


def rss_feed(items: list[tuple[str, str, datetime | None]]) -> bytes:
    """RSS 2.0 mínimo com ``(título, link, data de publicação)`` por item."""
    entries = []
    for title, link, published in items:
        date = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
        entries.append(
            f"<item><title>{escape(title)}</title><link>{escape(link)}</link>"
            f"<description>Resumo de {escape(title)}.</description>{date}</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed</title>'
        f"<link>https://exemplo.com.br/</link><description>Feed de teste</description>{''.join(entries)}"
        "</channel></rss>"
    ).encode()
