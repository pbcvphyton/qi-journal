"""E-mail diário: assunto, HTML (tabelas + CSS inline) e versão em texto puro.

O HTML segue as restrições de clientes de e-mail (Gmail, Outlook, Apple Mail):
sem JavaScript, sem SVG, sem web fonts obrigatórias, largura de 640px e
tamanho total bem abaixo do corte de ~102 KB do Gmail.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from .. import text
from ..config import Config
from ..models import Edition
from . import filters
from .web import EditionView, StoryView, build_view

log = logging.getLogger(__name__)

DEFAULT_SUBJECT = "{brand} — {date_label}"
PREHEADER_MAX = 150
TEXT_WIDTH = 72
MAX_EMAIL_BYTES = 90 * 1024


@dataclass
class EmailSection:
    title: str
    color: str
    stories: list[StoryView]


def story_url(page_url: str, story_id: str) -> str:
    """Link para a matéria na página da edição (o modal abre pela âncora ``#s-<id>``).

    ``render_email`` usa a cópia arquivada (``edicoes/AAAA-MM-DD.html``): a capa
    muda no dia seguinte, e o link de um e-mail antigo continuaria abrindo a
    matéria certa.
    """
    return f"{page_url}#s-{quote(story_id, safe='-_.~')}"


def _subject(edition: Edition, config: Config) -> str:
    try:
        short_date = text.pt_short_date(date.fromisoformat(edition.date))
    except ValueError:
        short_date = edition.date
    values = {"brand": config.brand.name, "date_label": edition.date_label, "date": short_date}
    try:
        subject = config.email.subject_template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("subject_template inválido (%s); usando o padrão", exc)
        subject = DEFAULT_SUBJECT.format(**values)
    # Assunto é cabeçalho de e-mail: nunca pode conter quebras de linha.
    return " ".join(subject.split())


def _select_sections(view: EditionView, limit: int) -> list[EmailSection]:
    """Até ``limit`` matérias (além da manchete), priorizando chamadas e destaques,
    agrupadas por seção na ordem da edição."""
    lead_id = view.lead.id if view.lead else None
    priority = [*view.secondary, *view.highlights, *view.stories]
    chosen: list[str] = []
    for story in priority:
        if len(chosen) >= max(limit, 0):
            break
        if story.id != lead_id and story.id not in chosen:
            chosen.append(story.id)
    selected = set(chosen)
    groups: list[EmailSection] = []
    for section in view.sections:
        stories = [s for s in section.stories if s.id in selected]
        if stories:
            groups.append(EmailSection(title=section.title, color=section.color, stories=stories))
    return groups


def _preheader(view: EditionView) -> str:
    source = filters.plain(view.editorial) or (view.lead.dek if view.lead else "") or view.brand.tagline
    return text.truncate(source, PREHEADER_MAX)


# ── texto puro ───────────────────────────────────────────────────────────────


def _wrap(value: str, indent: str = "", first: str | None = None) -> list[str]:
    return textwrap.wrap(
        value,
        width=TEXT_WIDTH,
        initial_indent=indent if first is None else first,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [first or indent]


def _heading(title: str) -> list[str]:
    title = title.upper()
    return ["", title, "-" * len(title)]


def _story_lines(story: StoryView, base_url: str, bullet: str) -> list[str]:
    indent = " " * len(bullet)
    lines = _wrap(story.headline, indent, first=bullet)
    if story.dek:
        lines += _wrap(story.dek, indent)
    if story.sources:
        lines += _wrap(f"Fontes: {', '.join(s.name for s in story.sources)}", indent)
    lines.append(f"{indent}{story_url(base_url, story.id)}")
    return lines


def _quote_text(view: EditionView) -> str:
    parts = []
    for q in view.quotes:
        parts.append(f"{q.label} {q.display}" + (f" ({q.change})" if q.change else ""))
    return " · ".join(parts)


def _weather_text(view: EditionView) -> list[str]:
    lines = []
    for w in view.weather:
        now = f", {w.now_desc.lower()}" if w.now_desc else ""
        tomorrow = f"{w.tomorrow_desc.lower()}, " if w.tomorrow_desc else ""
        lines += _wrap(
            f"{w.city}: {w.now_temp}{now} (mín. {w.today_min} / máx. {w.today_max}) · "
            f"amanhã: {tomorrow}{w.tomorrow_min} a {w.tomorrow_max}"
        )
    return lines


def _render_text(view: EditionView, sections: list[EmailSection], footer: dict[str, str]) -> str:
    base = view.base_url
    page = view.edition_url  # links das matérias: cópia arquivada (estável)
    dateline = view.date_label + (f" · {view.time_label} {view.tz_label}" if view.time_label else "")
    lines = [view.brand.name.upper(), dateline]
    if view.quotes:
        lines += _heading("Mercados") + _wrap(_quote_text(view))
    if view.weather:
        lines += _heading("Clima") + _weather_text(view)
    if view.editorial:
        lines += _heading("Editorial") + _wrap(filters.plain(view.editorial))
    if view.briefing:
        lines += _heading("Em 1 minuto")
        for item in view.briefing:
            lines += _wrap(filters.plain(item), "  ", first="• ")
    if view.lead:
        lead = view.lead
        lines += _heading("Manchete") + _wrap(lead.headline)
        if lead.dek:
            lines += _wrap(lead.dek)
        if lead.sources:
            lines += _wrap(f"Fontes: {', '.join(s.name for s in lead.sources)}")
        lines.append(f"Ler na edição: {story_url(page, lead.id)}")
    for section in sections:
        lines += _heading(section.title)
        for story in section.stories:
            lines += _story_lines(story, page, "• ") + [""]
        lines.pop()
    lines += [
        "",
        "=" * TEXT_WIDTH,
        f"Abrir edição completa: {base}",
        f"Edições anteriores: {footer['archive_url']}",
    ]
    if view.repo_url:
        lines.append(f"Código-fonte: {view.repo_url}")
    lines += _wrap(footer["generated"])
    return "\n".join(lines).strip() + "\n"


# ── API ──────────────────────────────────────────────────────────────────────


def render_email(edition: Edition, config: Config) -> tuple[str, str, str]:
    """Gera ``(assunto, html, texto)`` do e-mail da edição."""
    view = build_view(edition, config)
    subject = _subject(edition, config)
    sections = _select_sections(view, config.email.max_stories)
    time_part = f" em {view.time_label} ({view.tz_label})" if view.time_label else ""
    footer = {
        "archive_url": f"{view.base_url}edicoes/",
        "generated": f"Gerado automaticamente{time_part} · {view.mode_label}",
    }
    template = filters.environment().get_template("email.html.j2")
    html = template.render(
        v=view,
        brand=view.brand,
        subject=subject,
        preheader=_preheader(view),
        sections=sections,
        story_url=lambda story: story_url(view.edition_url, story.id),
        footer=footer,
    )
    plain_text = _render_text(view, sections, footer)
    size = len(html.encode("utf-8"))
    if size > MAX_EMAIL_BYTES:
        log.warning(
            "HTML do e-mail com %.0f KB (acima de %d KB: o Gmail pode cortar)", size / 1024, MAX_EMAIL_BYTES // 1024
        )
    log.info(
        "E-mail renderizado: %d matérias em %d seções, %.0f KB",
        sum(len(s.stories) for s in sections) + (1 if view.lead else 0),
        len(sections),
        size / 1024,
    )
    return subject, html, plain_text
