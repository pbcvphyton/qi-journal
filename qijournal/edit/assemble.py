"""Montagem da :class:`Edition` a partir das matérias (comum à IA e à heurística).

Distribui as matérias nas seções, escolhe manchete, chamadas secundárias e
destaques, copia cotações/clima do bundle e valida o resultado final — uma
edição que chega ao renderizador sempre respeita os contratos de ``models.py``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from qijournal import text
from qijournal.config import Config
from qijournal.edit.cluster import as_utc, parse_iso
from qijournal.models import Bundle, Edition, EditionStats, Section, Story

log = logging.getLogger(__name__)

FORBIDDEN_PLAIN_CHARS = "*#<>"  # nunca aparecem em headline/dek
SECONDARY_WINDOW_FACTOR = 3  # secundárias: diversidade buscada entre as N×3 mais importantes
STORY_ID_MAX = 48

_FORBIDDEN_TRANSLATION = {ord(c): " " for c in FORBIDDEN_PLAIN_CHARS}


def plain_text(value: str | None, max_chars: int | None = None) -> str:
    """Texto puro seguro para título/linha fina: sem HTML (inclusive o conteúdo de
    ``<script>``/``<style>``), sem markdown e sem os caracteres ``* # < >``;
    espaços colapsados; opcionalmente truncado."""
    s = text.html_to_text(value or "")
    s = text.strip_markdown(s)
    s = s.translate(_FORBIDDEN_TRANSLATION)
    s = re.sub(r"\s+", " ", s).strip()
    if max_chars is not None:
        s = text.truncate(s, max_chars)
    return s


def unique_story_id(headline: str, taken: set[str]) -> str:
    """Slug do título (até 48 chars) único dentro de ``taken``: ``slug``, ``slug-2``, ``slug-3``…

    O id devolvido é acrescentado a ``taken``, para que chamadas sucessivas com
    o mesmo conjunto nunca repitam ids.
    """
    base = text.slugify(headline, STORY_ID_MAX)
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def top_headlines(edition: Edition, limit: int) -> list[str]:
    """Títulos da manchete, das secundárias e dos destaques, nessa ordem (até ``limit``)."""
    ids = [edition.lead, *edition.secondary, *edition.highlights]
    return [edition.story(sid).headline for sid in ids[:limit]]


def edition_stats(
    bundle: Bundle,
    *,
    articles_considered: int,
    llm_input_tokens: int | None = None,
    llm_output_tokens: int | None = None,
) -> EditionStats:
    """Estatísticas da edição a partir do status das fontes do bundle."""
    failed = [
        {"source_id": s.source_id, "url": s.url, "error": s.error or "erro desconhecido"}
        for s in bundle.sources
        if not s.ok
    ]
    return EditionStats(
        sources_total=len(bundle.sources),
        sources_ok=sum(1 for s in bundle.sources if s.ok),
        sources_failed=failed,
        articles_collected=len(bundle.articles),
        articles_considered=articles_considered,
        llm_input_tokens=llm_input_tokens,
        llm_output_tokens=llm_output_tokens,
    )


# ── validação ──────────────────────────────────────────────────────────────


def _validate_stories(stories: list[Story], config: Config) -> None:
    """Levanta ``ValueError`` se alguma matéria violar os contratos da edição."""
    if not stories:
        raise ValueError("a edição precisa de pelo menos uma matéria")
    section_ids = set(config.section_ids)
    seen: set[str] = set()
    for story in stories:
        if not story.id:
            raise ValueError("matéria sem id")
        if story.id in seen:
            raise ValueError(f"id de matéria repetido: {story.id!r}")
        seen.add(story.id)
        if story.section not in section_ids:
            raise ValueError(f"seção desconhecida {story.section!r} na matéria {story.id!r}")
        if not story.headline or not story.headline.strip():
            raise ValueError(f"matéria {story.id!r} sem título")
        for field_name in ("headline", "dek"):
            value = getattr(story, field_name) or ""
            bad = sorted({c for c in value if c in FORBIDDEN_PLAIN_CHARS})
            if bad:
                raise ValueError(f"{field_name} da matéria {story.id!r} contém marcação proibida: {''.join(bad)}")
        if not isinstance(story.importance, int) or not 1 <= story.importance <= 5:
            raise ValueError(f"importância inválida na matéria {story.id!r}: {story.importance!r}")


# ── escolha de manchete, secundárias e destaques ───────────────────────────


def _pick_lead(stories: list[Story], order: dict[str, int], lead_id: str | None) -> Story:
    if lead_id is not None:
        for story in stories:
            if story.id == lead_id:
                return story
        log.warning("Manchete indicada (%s) não existe entre as matérias; escolhendo automaticamente", lead_id)
    return min(stories, key=lambda s: (-s.importance, s.image is None, order[s.id]))


def _pick_secondary(ranked: list[Story], lead: Story, count: int) -> list[Story]:
    """Próximas ``count`` por importância, preferindo seções diferentes da
    manchete e entre si (a diversidade só é buscada entre as mais importantes)."""
    candidates = [s for s in ranked if s.id != lead.id]
    window = candidates[: max(count * SECONDARY_WINDOW_FACTOR, count)]
    chosen: list[Story] = []
    used_sections = {lead.section}
    for story in window:
        if len(chosen) == count:
            break
        if story.section not in used_sections:
            chosen.append(story)
            used_sections.add(story.section)
    for story in candidates:
        if len(chosen) == count:
            break
        if story not in chosen:
            chosen.append(story)
    # mantém a ordem de importância na exibição
    rank = {s.id: i for i, s in enumerate(ranked)}
    return sorted(chosen, key=lambda s: rank[s.id])


def _published_ts(story: Story) -> float:
    dt = parse_iso(story.published)
    return dt.timestamp() if dt else float("-inf")


def assemble_edition(
    stories: list[Story],
    *,
    bundle: Bundle,
    config: Config,
    now: datetime,
    mode: str,
    model: str | None,
    editorial: str,
    briefing: list[str],
    lead_id: str | None = None,
    stats: EditionStats,
) -> Edition:
    """Monta e valida a edição.

    - ``date``/``date_label``: data local de ``now`` no fuso do site;
    - seções na ordem da configuração (só as não vazias), matérias por
      importância e depois por data de publicação (mais recente primeiro);
    - manchete = ``lead_id`` se existir, senão a mais importante (com imagem,
      de preferência); secundárias e destaques na sequência.

    Levanta ``ValueError`` se ids se repetirem, a manchete não existir ou algum
    título/linha fina estiver vazio ou contiver ``* # < >``.
    """
    _validate_stories(stories, config)
    now_utc = as_utc(now)
    local = now_utc.astimezone(ZoneInfo(config.site.timezone))

    order = {s.id: i for i, s in enumerate(stories)}
    ranked = sorted(stories, key=lambda s: (-s.importance, order[s.id]))
    lead = _pick_lead(stories, order, lead_id)
    secondary = _pick_secondary(ranked, lead, config.edition.secondary_count)
    used = {lead.id, *(s.id for s in secondary)}
    highlights = [s for s in ranked if s.id not in used][: config.edition.highlights_count]

    sections: list[Section] = []
    for section_config in config.sections:
        members = [s for s in stories if s.section == section_config.id]
        if not members:
            continue
        members.sort(key=lambda s: (-s.importance, -_published_ts(s), order[s.id]))
        sections.append(
            Section(
                id=section_config.id,
                title=section_config.title,
                color=section_config.color,
                story_ids=[s.id for s in members],
            )
        )

    return Edition(
        date=local.date().isoformat(),
        date_label=text.pt_date_label(local),
        generated_at=now_utc.isoformat(timespec="seconds"),
        mode=mode,
        model=model,
        editorial=editorial,
        briefing=list(briefing),
        lead=lead.id,
        secondary=[s.id for s in secondary],
        highlights=[s.id for s in highlights],
        sections=sections,
        stories=list(stories),
        quotes=list(bundle.quotes),
        weather=list(bundle.weather),
        stats=stats,
    )
