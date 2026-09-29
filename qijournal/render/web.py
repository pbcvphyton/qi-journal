"""Página HTML da edição (autocontida) e índice do arquivo de edições.

O modelo ``Edition`` é convertido primeiro numa *view* (:class:`EditionView`)
com todos os textos já limpos (sem markdown em títulos), URLs validadas e
horários formatados; os templates só apresentam. A mesma view é usada pelo
e-mail (``render/email.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from markupsafe import Markup

from .. import text
from ..collect.market import format_change
from ..config import Config
from ..models import Edition, Quote, Story
from . import filters

log = logging.getLogger(__name__)

DEFAULT_COLORS = {
    "primary": "#1C49A5",
    "navy": "#0A2051",
    "accent": "#57D9FF",
    "alert": "#FF2F80",
    "ticker_up": "#57D9FF",
    "ticker_down": "#FF2F80",
}
RADAR_SIZE = 8
LEAD_MORE_PARAGRAPHS = 2  # texto extra da manchete quando não há imagem (ou ela falha)
DESCRIPTION_MAX = 200
EXCERPT_OVERLAP = 0.6  # similaridade a partir da qual um parágrafo repete a linha fina
ORPHAN_SECTION = ("outras", "Outras notícias")


@dataclass(frozen=True)
class SourceLink:
    name: str
    url: str | None  # None quando a URL original não é http(s)


@dataclass
class StoryView:
    """Matéria pronta para exibição (textos limpos, URLs seguras, horários locais)."""

    id: str
    anchor: str  # id do modal / âncora: "s-<id>"
    section_title: str
    color: str
    headline: str
    dek: str
    body: list[str]  # pode conter **negrito** (renderizar com md_lite)
    why: str
    image: str | None
    sources: list[SourceLink]
    time: str  # "05:07" (fuso do site)
    age: str  # "há 3 h"
    published: str | None

    @property
    def source_names(self) -> str:
        return " · ".join(s.name for s in self.sources)

    @property
    def dek_in_body(self) -> bool:
        """O 1º parágrafo já começa com a linha fina (a edição automática corta a
        primeira frase longa na linha fina e a mantém inteira no corpo)? O modal
        então mostra só o corpo, sem repetir o texto."""
        dek = text.normalize(self.dek)
        return bool(dek and self.body and text.normalize(filters.plain(self.body[0])).startswith(dek))


@dataclass
class SectionView:
    id: str
    title: str
    color: str
    stories: list[StoryView]


@dataclass
class QuoteView:
    label: str
    display: str
    change: str | None  # "+0,19%" ou None para indicadores
    css: str  # u / d / f / ""
    note: str  # dica (fonte e data de referência)


@dataclass
class WeatherView:
    city: str
    now_emoji: str
    now_desc: str
    now_temp: str
    today_min: str
    today_max: str
    tomorrow_emoji: str
    tomorrow_desc: str
    tomorrow_min: str
    tomorrow_max: str


@dataclass
class SourceGroup:
    """Situação de um veículo (que pode ter vários feeds) na coleta do dia."""

    name: str
    feeds: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.failed:
            return "ok"
        return "erro" if self.failed >= self.feeds else "parcial"


@dataclass
class BrandView:
    name: str
    wordmark: list[tuple[str, str]]  # (texto, classe css)
    tagline: str
    logo_svg: Markup | None  # SVG do repositório (confiável), embutido como está
    favicon_uri: str
    colors: dict[str, str]
    rgb: dict[str, str]


@dataclass
class EditionView:
    brand: BrandView
    date_label: str
    time_label: str
    tz_label: str
    mode_label: str
    editorial: str
    briefing: list[str]
    lead: StoryView | None
    lead_excerpt: str  # parágrafo do corpo exibido na manchete (pode conter **negrito**)
    lead_more: list[str]  # parágrafos seguintes, exibidos só se a manchete ficar sem imagem
    secondary: list[StoryView]
    highlights: list[StoryView]
    sections: list[SectionView]
    stories: list[StoryView]
    radar: list[StoryView]
    quotes: list[QuoteView]
    weather: list[WeatherView]
    sources_total: int
    sources_ok: int
    source_groups: list[SourceGroup]
    base_url: str
    repo_url: str
    edition_url: str
    description: str
    og_image: str | None


# ── marca ────────────────────────────────────────────────────────────────────


def brand_view(config: Config) -> BrandView:
    """Cores validadas, wordmark em partes e favicon como data URI."""
    brand = config.brand
    colors = {k: filters.safe_color(brand.colors.get(k), v) for k, v in DEFAULT_COLORS.items()}
    classes = ("q", "i", "journal")
    wordmark = [(part, classes[min(i, 2)]) for i, part in enumerate(brand.wordmark or []) if part]
    if not wordmark:
        wordmark = [(brand.name, "journal")]
    favicon_svg = brand.favicon_svg or filters.initial_favicon(
        wordmark[0][0].strip() or brand.name, colors["navy"], colors["accent"]
    )
    return BrandView(
        name=brand.name,
        wordmark=wordmark,
        tagline=brand.tagline,
        logo_svg=filters.clean_svg(brand.logo_svg),
        favicon_uri=filters.svg_data_uri(favicon_svg),
        colors=colors,
        rgb={k: filters.rgb_triplet(v) for k, v in colors.items()},
    )


# ── matérias ─────────────────────────────────────────────────────────────────


def _source_links(story: Story) -> list[SourceLink]:
    links: list[SourceLink] = []
    seen: set[str] = set()
    for ref in story.sources:
        name = filters.plain(ref.name)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        links.append(SourceLink(name=name, url=filters.safe_url(ref.url)))
    return links


def _story_view(story: Story, section: tuple[str, str], *, tz: str, now_iso: str) -> StoryView:
    title, color = section
    return StoryView(
        id=story.id,
        anchor=f"s-{story.id}",
        section_title=title,
        color=color,
        headline=filters.plain(story.headline),
        dek=filters.plain(story.dek),
        body=[p.strip() for p in story.body if p and filters.plain(p)],
        why=filters.plain(story.why_it_matters),
        image=filters.safe_url(story.image),
        sources=_source_links(story),
        time=filters.local_time(story.published, tz),
        age=filters.rel_age(story.published, now_iso),
        published=story.published,
    )


def _section_lookup(edition: Edition, config: Config, default_color: str) -> dict[str, tuple[str, str]]:
    """``id → (título, cor)``: prioriza a edição e completa com a configuração."""
    lookup = {s.id: (s.title, filters.safe_color(s.color, default_color)) for s in config.sections}
    for sec in edition.sections:
        fallback_title = lookup.get(sec.id, (sec.id, default_color))[0]
        lookup[sec.id] = (sec.title or fallback_title, filters.safe_color(sec.color, default_color))
    return lookup


def _published_ts(view: StoryView) -> float:
    moment = filters.parse_iso(view.published)
    return moment.timestamp() if moment else float("-inf")


def _pick(ids: list[str], by_id: dict[str, StoryView], exclude: set[str]) -> list[StoryView]:
    """Resolve ids em views, pulando os inexistentes e os já usados (``exclude`` é atualizado)."""
    out: list[StoryView] = []
    for story_id in ids:
        view = by_id.get(story_id)
        if view and story_id not in exclude:
            out.append(view)
            exclude.add(story_id)
    return out


def _build_sections(
    edition: Edition, by_id: dict[str, StoryView], lookup: dict[str, tuple[str, str]], default_color: str
) -> list[SectionView]:
    """Seções na ordem da edição, sem seções vazias; matérias órfãs vão para "Outras"."""
    placed: set[str] = set()
    sections: list[SectionView] = []
    for sec in edition.sections:
        stories = _pick(sec.story_ids, by_id, placed)
        if stories:
            title, color = lookup.get(sec.id, (sec.title, default_color))
            sections.append(SectionView(id=sec.id, title=title, color=color, stories=stories))
    orphans = [v for sid, v in by_id.items() if sid not in placed]
    if orphans:
        log.warning("%d matéria(s) sem seção na edição; exibidas em %r", len(orphans), ORPHAN_SECTION[1])
        sections.append(
            SectionView(id=ORPHAN_SECTION[0], title=ORPHAN_SECTION[1], color=default_color, stories=orphans)
        )
    return sections


# ── painel (cotações, clima, fontes) ─────────────────────────────────────────


def _quote_view(quote: Quote) -> QuoteView:
    change = format_change(quote.change_pct) if quote.change_pct is not None else None
    note = " · ".join(p for p in (quote.source or "", f"ref. {quote.as_of[:10]}" if quote.as_of else "") if p)
    return QuoteView(
        label=quote.label,
        display=quote.display,
        change=change,
        css=filters.change_class(quote.change_pct),
        note=note,
    )


def _weather_views(edition: Edition) -> list[WeatherView]:
    views = []
    for w in edition.weather:
        now_temp = filters.temp(w.current_c)
        views.append(
            WeatherView(
                city=w.city,
                now_emoji=w.current_emoji,
                now_desc=w.current_desc,
                now_temp=f"{now_temp}C" if now_temp != "–" else now_temp,
                today_min=filters.temp(w.today_min),
                today_max=filters.temp(w.today_max),
                tomorrow_emoji=w.tomorrow_emoji,
                tomorrow_desc=w.tomorrow_desc,
                tomorrow_min=filters.temp(w.tomorrow_min),
                tomorrow_max=filters.temp(w.tomorrow_max),
            )
        )
    return views


def _source_groups(edition: Edition, config: Config) -> list[SourceGroup]:
    """Agrupa os feeds por veículo e marca os que falharam na coleta."""
    failed_by_url = {f.get("url"): f for f in edition.stats.sources_failed if f.get("url")}
    groups: dict[str, SourceGroup] = {}
    matched: set[str] = set()
    for source in config.sources:
        group = groups.setdefault(source.name, SourceGroup(name=source.name))
        group.feeds += 1
        failure = failed_by_url.get(source.url)
        if failure:
            matched.add(source.url)
            group.failed += 1
            group.errors.append(str(failure.get("error") or "erro"))
    # Falhas de feeds que não estão (mais) na configuração também aparecem.
    for failure in edition.stats.sources_failed:
        if failure.get("url") in matched:
            continue
        name = str(failure.get("source_id") or failure.get("url") or "fonte")
        group = groups.setdefault(name, SourceGroup(name=name))
        group.feeds += 1
        group.failed += 1
        group.errors.append(str(failure.get("error") or "erro"))
    return list(groups.values())


def _lead_excerpt(lead: StoryView | None) -> str:
    """Primeiro parágrafo do corpo que não repete a linha fina (a edição heurística
    costuma abrir o corpo com a mesma frase do ``dek``)."""
    if lead is None:
        return ""
    dek = text.normalize(lead.dek)
    for paragraph in lead.body:
        plain = text.normalize(filters.plain(paragraph))
        if dek and (plain.startswith(dek[:80]) or text.similarity(dek, plain) >= EXCERPT_OVERLAP):
            continue
        return paragraph
    return ""


def _lead_more(lead: StoryView | None, excerpt: str) -> list[str]:
    """Parágrafos que seguem o trecho da manchete (preenchem o espaço da imagem ausente)."""
    if lead is None or not excerpt or excerpt not in lead.body:
        return []
    start = lead.body.index(excerpt) + 1
    return lead.body[start : start + LEAD_MORE_PARAGRAPHS]


def _mode_label(edition: Edition) -> str:
    if edition.mode == "ai":
        return f"Edição gerada por IA ({edition.model})" if edition.model else "Edição gerada por IA"
    return "Edição automática (sem IA)"


# ── view completa ────────────────────────────────────────────────────────────


def build_view(edition: Edition, config: Config) -> EditionView:
    """Converte a edição num modelo de apresentação validado e sem marcação."""
    brand = brand_view(config)
    tz = config.site.timezone
    default_color = brand.colors["primary"]
    lookup = _section_lookup(edition, config, default_color)

    by_id: dict[str, StoryView] = {}
    for story in edition.stories:
        if story.id in by_id:
            log.warning("Matéria com id duplicado ignorada: %s", story.id)
            continue
        view = _story_view(
            story, lookup.get(story.section, (story.section, default_color)), tz=tz, now_iso=edition.generated_at
        )
        if not view.headline:
            log.warning("Matéria sem título ignorada na renderização: %s", story.id)
            continue
        by_id[story.id] = view

    sections = _build_sections(edition, by_id, lookup, default_color)
    ordered = [v for sec in sections for v in sec.stories]

    used: set[str] = set()
    lead = by_id.get(edition.lead)
    if lead is None and ordered:
        log.warning("Manchete %r não encontrada; usando a primeira matéria", edition.lead)
        lead = ordered[0]
    if lead:
        used.add(lead.id)
    secondary = _pick(edition.secondary, by_id, used)
    highlights = _pick(edition.highlights, by_id, used)
    radar = sorted((v for v in ordered if v.published), key=_published_ts, reverse=True)[:RADAR_SIZE]

    description = filters.plain(edition.editorial) or (lead.dek if lead else "") or brand.tagline
    base_url = config.site.base_url
    return EditionView(
        brand=brand,
        date_label=edition.date_label,
        time_label=filters.local_time(edition.generated_at, tz),
        tz_label=filters.tz_label(tz, edition.generated_at),
        mode_label=_mode_label(edition),
        editorial=edition.editorial.strip() if filters.plain(edition.editorial) else "",
        briefing=[item.strip() for item in edition.briefing if filters.plain(item)],
        lead=lead,
        lead_excerpt=(excerpt := _lead_excerpt(lead)),
        lead_more=_lead_more(lead, excerpt),
        secondary=secondary,
        highlights=highlights,
        sections=sections,
        stories=ordered,
        radar=radar,
        quotes=[_quote_view(q) for q in edition.quotes],
        weather=_weather_views(edition),
        sources_total=edition.stats.sources_total,
        sources_ok=edition.stats.sources_ok,
        source_groups=_source_groups(edition, config),
        base_url=base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
        edition_url=f"{base_url}edicoes/{edition.date}.html",
        description=text.truncate(description, DESCRIPTION_MAX),
        og_image=lead.image if lead else None,
    )


# ── páginas ──────────────────────────────────────────────────────────────────


def render_edition_page(edition: Edition, config: Config, *, home_href: str, archive_href: str) -> str:
    """Página única e autocontida da edição (CSS/JS inline)."""
    view = build_view(edition, config)
    template = filters.environment().get_template("edition.html.j2")
    html = template.render(
        v=view,
        brand=view.brand,
        home_href=filters.safe_href(home_href) or "./",
        archive_href=filters.safe_href(archive_href) or "edicoes/",
    )
    log.info(
        "Página da edição %s renderizada: %d matérias, %d seções, %.0f KB",
        edition.date,
        len(view.stories),
        len(view.sections),
        len(html.encode("utf-8")) / 1024,
    )
    return html


def _month_label(iso_date: str) -> str:
    try:
        day = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return "Outras edições"
    return f"{text.MONTHS_PT[day.month - 1].capitalize()} de {day.year}"


def render_archive_index(entries: list[dict], config: Config, *, home_href: str) -> str:
    """Índice do arquivo: edições agrupadas por mês, da mais recente para a mais antiga."""
    brand = brand_view(config)
    months: list[dict[str, Any]] = []
    for entry in entries:
        href = filters.safe_href(str(entry.get("href") or ""))
        if not href:
            log.warning("Entrada do arquivo sem link válido ignorada: %r", entry.get("date"))
            continue
        label = _month_label(str(entry.get("date") or ""))
        if not months or months[-1]["label"] != label:
            months.append({"label": label, "items": []})
        months[-1]["items"].append(
            {
                "date": str(entry.get("date") or ""),
                "date_label": str(entry.get("date_label") or entry.get("date") or ""),
                "href": href,
                "headline": filters.plain(str(entry.get("lead_headline") or "")),
                "ai": entry.get("mode") == "ai",
            }
        )
    template = filters.environment().get_template("archive.html.j2")
    return template.render(
        brand=brand,
        months=months,
        total=sum(len(m["items"]) for m in months),
        home_href=filters.safe_href(home_href) or "../",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
    )
