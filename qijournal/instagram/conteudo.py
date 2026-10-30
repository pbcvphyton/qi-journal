"""Conteúdo de cada publicação do Instagram, montado só com campos da edição do dia.

Nada aqui chama IA: textos, legendas e textos alternativos saem do JSON da
edição por template, para não criar um novo ponto em que fatos possam ser
inventados. Cada formato devolve uma :class:`Peca` (slides + legenda); a
renderização fica em :mod:`.gerar`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from qijournal import text as qtext
from qijournal.models import Coverage, CoverageOutlet, Edition, Quote, Story

from . import estatistica
from .selecao import ordered_stories

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
DIAS_CURTOS = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]
MESES_CURTOS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]

# Cotações dos slides de mercado, na ordem de exibição.
MERCADO_IDS = ["usd", "eur", "ibov", "spx", "brent", "gold", "btc", "selic", "ipca12m"]
STORY_MERCADO_IDS = ["usd", "ibov", "spx", "brent", "btc", "selic"]

HASHTAG_SECAO = {
    "brasil": "#economia",
    "mercados": "#mercadofinanceiro",
    "juridico": "#direito",
    "politica": "#politica",
    "mundo": "#geopolitica",
    "tecnologia": "#tecnologia",
    "esporte": "#esporte",
    "natureza": "#meioambiente",
    "variedades": "#cultura",
}
CAPTION_LIMIT = 2200
ALT_LIMIT = 1000

# Período eleitoral: pesquisa de intenção de voto não sai (Res. TSE 23.600/2019
# exige dados de registro que a edição não tem) e política não vira vitrine.
_PESQUISA = re.compile(
    r"\b(pesquisas?|datafolha|quaest|atlasintel|atlas|ipec|ipespe|paran[aá] pesquisas|poderdata|futura|nexus|"
    r"real time big data|inten[cç][aã]o de voto|inten[cç][oõ]es de voto|empate t[eé]cnico|lideran[cç]a num[eé]rica|"
    r"pontos percentuais|margem de erro)\b",
    re.I,
)


@dataclass
class Slide:
    template: str  # nome do template (sem extensão) em qijournal/instagram/templates
    ctx: dict[str, Any]
    alt: str = ""  # texto alternativo (só imagem e item de carrossel)


@dataclass
class Peca:
    """Uma publicação pronta para renderizar: um carrossel, uma imagem ou uma sequência de stories."""

    slot: str  # "manha", "stories", "raiox", "terminal"...
    kind: str  # "carousel" | "image" | "story"
    title: str
    slides: list[Slide]
    caption: str = ""
    story_ids: list[str] = field(default_factory=list)  # matérias usadas (para não repetir no dia)
    serie: str = ""  # série numerada (Post.serie): "em1min", "numero", "raiox" (Placar do dia também), "mercado"
    numero_serie: int = 0  # preenchido pelo gerar com Fila.numero_para
    chave: str = ""  # do Número do dia (Numero.chave)
    entidades: list[str] = field(default_factory=list)  # do Raio-X (selecao.entidades)
    formula: str = ""  # molde do gancho (selecao.Gancho.formula)


# ── Utilidades de texto e números ─────────────────────────────────────────


def plain(value: str | None) -> str:
    return qtext.strip_markdown(value or "").strip()


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def _words(value: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", _norm(plain(value)))}


def pct(value: float | None) -> str:
    """Variação com sinal e vírgula: ``0.1051`` → ``"+0,11%"``; ``-0.171`` → ``"−0,17%"``."""
    if value is None:
        return ""
    rounded = round(value, 2)
    sign = "+" if rounded > 0 else "−" if rounded < 0 else ""
    return f"{sign}{abs(rounded):.2f}%".replace(".", ",")


def trend(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 2)
    return "up" if rounded > 0 else "down" if rounded < 0 else "flat"


def short_date(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}"


def kicker_date(day: date) -> str:
    """``TER 20 OUT``."""
    return f"{DIAS_CURTOS[day.weekday()]} {day.day:02d} {MESES_CURTOS[day.month - 1]}"


def truncate_sentence(value: str, limit: int) -> str:
    """Corta em fim de frase (ou de palavra) sem passar de ``limit`` caracteres."""
    value = value.strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= limit * 0.5:
        return cut[: end + 1].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def num(value: int | None) -> str:
    return qtext.format_number_pt(int(value or 0), 0)


def more_than(value: int | None, step: int) -> str:
    """``1581, 100`` → ``"mais de 1.500"``; abaixo de ``step``, o próprio número."""
    value = int(value or 0)
    if value < step:
        return num(value)
    floor = value // step * step
    return f"mais de {num(floor)}" if floor < value else num(value)


_DQUOTES = re.compile(r'"([^"\n]+)"')
_SQUOTES = re.compile(r"(^|[\s(\[{—–*-])'([^'\n]+)'(?=[\s.,;:!?)\]}—–*-]|$)")
_EMPHASIS = re.compile(r"\*\*(.+?)\*\*")
EMPHASIS_MAX_CHARS = 40
EMPHASIS_MAX_WORDS = 5
_NBSP_AFTER = re.compile(r"(?<![\w])(R\$|US\$|€|nº|n\.º) ")
_NBSP_NUMBER = re.compile(r"(?<=\d) (?=(?:mil|milhões|milhão|bilhões|bilhão|trilhões|trilhão|bi|mi|pts|pontos|minutos?|horas?|dias?|anos?|meses|mês|veículos|notícias|matérias|%)\b)")


def smart_quotes(value: str) -> str:
    """Aspas retas → tipográficas (“…” e ‘…’); apóstrofos ficam como estão."""
    value = _DQUOTES.sub("“\\1”", value)
    return _SQUOTES.sub("\\1‘\\2’", value)


def tidy(value: str) -> str:
    """Texto de slide: aspas tipográficas, ênfase (**…**) só em trechos curtos e espaço
    inseparável entre número e unidade ("1 minuto", "R$ 42,5", "827 milhões")."""
    value = smart_quotes(value)

    def emphasis(m: re.Match) -> str:
        inner = m.group(1)
        short = len(inner) <= EMPHASIS_MAX_CHARS and len(inner.split()) <= EMPHASIS_MAX_WORDS
        return m.group(0) if short else inner

    value = _EMPHASIS.sub(emphasis, value)
    value = _NBSP_AFTER.sub(lambda m: m.group(1) + "\u00a0", value)
    return _NBSP_NUMBER.sub("\u00a0", value)


def polish(ctx: Any) -> Any:
    """Aplica :func:`tidy` a todos os textos do contexto de um slide (recursivo)."""
    if isinstance(ctx, str):
        return tidy(ctx)
    if isinstance(ctx, dict):
        return {k: (v if k in ("color", "id", "trend", "stance") else polish(v)) for k, v in ctx.items()}
    if isinstance(ctx, list):
        return [polish(v) for v in ctx]
    return ctx


# ── Leitura da edição ─────────────────────────────────────────────────────


def edition_day(edition: Edition) -> date:
    return date.fromisoformat(edition.date)


def section_info(edition: Edition, section_id: str) -> dict[str, str]:
    for s in edition.sections:
        if s.id == section_id:
            return {"id": s.id, "title": s.title, "color": s.color}
    return {"id": section_id, "title": section_id.title(), "color": "#394A7A"}


def source_names(story: Story) -> list[str]:
    seen: list[str] = []
    for src in story.sources:
        if src.name and src.name not in seen:
            seen.append(src.name)
    return seen


def sources_line(names: list[str], shown: int = 3) -> str:
    if not names:
        return ""
    head = ", ".join(names[:shown])
    return f"{head} +{len(names) - shown}" if len(names) > shown else head


def match_story(edition: Edition, item: str) -> Story | None:
    """Matéria do item do 'Em 1 minuto' (o briefing não traz a seção): mais palavras em comum."""
    words = _words(item)
    best, best_score = None, 0
    for story in edition.stories:
        score = len(words & _words(f"{story.headline} {story.dek}"))
        if score > best_score:
            best, best_score = story, score
    return best if best_score >= 3 else None


def quote_items(edition: Edition, ids: Iterable[str]) -> list[dict[str, Any]]:
    """Cotações para os slides: valor, variação com sinal e seta e a referência (fechamento de ontem etc.)."""
    by_id = {q.id: q for q in edition.quotes}
    day = edition_day(edition)
    tz = ZoneInfo("America/Sao_Paulo")
    items = []
    for qid in ids:
        q: Quote | None = by_id.get(qid)
        if q is None or not q.display:
            continue
        ref = ""
        if q.kind in ("fx", "index", "commodity", "crypto") and q.as_of:
            try:
                as_of = datetime.fromisoformat(q.as_of)
                if as_of.tzinfo is not None:
                    as_of_day = as_of.astimezone(tz).date()
                    if as_of_day < day:
                        ref = f"fech. {short_date(as_of_day)}"
            except ValueError:
                pass
        elif q.kind == "inflation" and q.as_of:
            year, _, month = q.as_of.partition("-")
            if month.isdigit():
                ref = f"{MESES_CURTOS[int(month) - 1].lower()}/{year[2:]}"
        items.append(
            {
                "id": q.id,
                "label": q.label,
                "value": q.display,
                "change": pct(q.change_pct),
                "trend": trend(q.change_pct),
                "ref": ref,
            }
        )
    return items


def quote_sentence(items: list[dict[str, Any]], ids: Iterable[str]) -> str:
    by_id = {i["id"]: i for i in items}
    parts = []
    for qid in ids:
        i = by_id.get(qid)
        if not i:
            continue
        extra = ", ".join(x for x in (i["change"], i["ref"]) if x)
        parts.append(f"{i['label']} {i['value']}" + (f" ({extra})" if extra else ""))
    return " · ".join(parts)


# ── Período eleitoral ─────────────────────────────────────────────────────


def is_poll(value: str) -> bool:
    return bool(_PESQUISA.search(plain(value)))


def is_electoral(story: Story | None, value: str = "", nomes: Iterable[str] = estatistica.NOMES_ELEICAO) -> bool:
    """:func:`.estatistica.is_electoral` sobre ``value`` e a matéria inteira (com a seção dela)."""
    text = " ".join(t for t in (plain(value), estatistica.story_text(story) if story is not None else "") if t)
    return estatistica.is_electoral(text, story.section if story is not None else None, nomes)


def is_political(story: Story | None, value: str = "", nomes: Iterable[str] = estatistica.NOMES_ELEICAO) -> bool:
    """Política, pesquisa ou texto eleitoral: no período eleitoral, fica fora das capas e do Raio-X."""
    return (story is not None and story.section == "politica") or is_poll(value) or is_electoral(story, value, nomes)


# ── Legendas ──────────────────────────────────────────────────────────────


def hashtags(fmt_tag: str, sections: Iterable[str], *, election: bool) -> str:
    tags = ["#triangula", fmt_tag]
    for sid in sections:
        tag = HASHTAG_SECAO.get(sid)
        if not tag or tag in tags or (election and sid == "politica"):
            continue
        tags.append(tag)
        if len(tags) == 4:
            break
    return " ".join(tags)


def finish_caption(parts: list[str]) -> str:
    caption = smart_quotes("\n\n".join(p.strip() for p in parts if p and p.strip()))
    if len(caption) > CAPTION_LIMIT:
        caption = caption[: CAPTION_LIMIT - 1].rsplit("\n", 1)[0]
    return caption


def alt(value: str) -> str:
    value = re.sub(r"\s+", " ", plain(value)).strip()
    return truncate_sentence(value, ALT_LIMIT)


# ── Formatos ──────────────────────────────────────────────────────────────


def _briefing_items(edition: Edition, *, election: bool, limit: int = 5) -> list[dict[str, Any]]:
    """Itens do 'Em 1 minuto' com a matéria correspondente; no período eleitoral, sem
    pesquisa e com no máximo um item de política (trocados pelas próximas matérias)."""
    items: list[dict[str, Any]] = []
    used: set[str] = set()
    politics = 0
    lead = edition.story(edition.lead) if edition.lead in {s.id for s in edition.stories} else None
    candidates = [(b, match_story(edition, b)) for b in edition.briefing if plain(b)]
    # O item da manchete vai primeiro (é o slide que o Instagram reapresenta).
    candidates.sort(key=lambda bs: 0 if lead is not None and bs[1] is lead else 1)
    for raw, story in candidates:
        if len(items) == limit:
            break
        if election and (is_poll(raw) or is_electoral(story, raw)):
            continue
        if election and story is not None and story.section == "politica":
            if politics:
                continue
            politics += 1
        if story is not None and story.id in used:
            continue
        items.append({"text": raw, "story": story})
        if story is not None:
            used.add(story.id)
    for story in ordered_stories(edition):  # completa com as próximas matérias
        if len(items) == limit:
            break
        if story.id in used or (election and (is_political(story, f"{story.headline} {story.dek}"))):
            continue
        # Só a linha fina (uma frase, sem a manchete colada na frente).
        items.append({"text": plain(story.dek) or plain(story.headline), "story": story})
        used.add(story.id)
    return items


def em_1_minuto(edition: Edition, brand: dict[str, Any], *, election: bool) -> Peca | None:
    """Carrossel da manhã: capa, 5 fatos, mercado e fechamento (8 slides)."""
    if edition.mode != "ai" or not edition.briefing:
        return None
    day = edition_day(edition)
    lead = edition.story(edition.lead)
    election_lead = election and is_political(lead, f"{lead.headline} {lead.dek}")
    items = _briefing_items(edition, election=election)
    if len(items) < 3:
        return None
    if election_lead:  # a capa não estampa pesquisa nem política no período eleitoral
        lead = next((i["story"] for i in items if i["story"] is not None and not is_political(i["story"])), lead)
        # O 1º fato é o da capa (é o slide que o Instagram reapresenta a quem não deslizou).
        items.sort(key=lambda i: 0 if i["story"] is lead else 1)
    stats = edition.stats
    outlets = edition.rationale.outlets if edition.rationale else 0
    total = len(items)
    slides = [
        Slide(
            "capa",
            {
                "kicker": f"EM 1 MINUTO · {kicker_date(day)}",
                "headline": plain(lead.headline),
                "dek": plain(lead.dek),
                "section": section_info(edition, lead.section),
                "teaser": f"{total} fatos + mercado · arraste",
            },
            alt=f"Em 1 minuto, {edition.date_label}. Manchete: {plain(lead.headline)}. {plain(lead.dek)}",
        )
    ]
    for n, item in enumerate(items, 1):
        story = item["story"]
        names = source_names(story) if story else []
        slides.append(
            Slide(
                "fato",
                {
                    "n": n,
                    "total": total,
                    "text": item["text"],
                    "section": section_info(edition, story.section) if story else None,
                    "sources": sources_line(names),
                    "outlets": len(names),
                },
                alt=f"Fato {n} de {total}: {plain(item['text'])}" + (f" Fontes: {', '.join(names)}." if names else ""),
            )
        )
    quotes = quote_items(edition, MERCADO_IDS)
    if quotes:
        slides.append(
            Slide(
                "mercado",
                {"title": "Mercado", "quotes": quotes, "weather": edition.weather[:1], "date": short_date(day)},
                alt="Mercado: " + quote_sentence(quotes, MERCADO_IDS) + ".",
            )
        )
    slides.append(
        Slide(
            "fechamento",
            {
                "articles": num(stats.articles_collected),
                "outlets": outlets,
                "stories": len(edition.stories),
                "compared": sum(1 for s in edition.stories if s.coverage) + len(edition.compared),
                "cta": "Salve · Envie · Siga",
            },
            alt=f"Hoje lemos {num(stats.articles_collected)} notícias de {outlets} veículos. "
            f"A edição completa, com {len(edition.stories)} matérias e como cada veículo cobriu cada assunto, está no link da bio.",
        )
    )
    sections = [i["story"].section for i in items if i["story"] is not None]
    hook = f"{plain(lead.headline)} — e mais {total - 1} fatos para começar a {DIAS[day.weekday()]}"
    market = quote_sentence(quotes, ["usd", "ibov", "brent", "btc", "selic"])
    caption = finish_caption(
        [
            hook,
            "\n".join(f"▸ {plain(i['text'])}" for i in items),
            f"Mercado: {market}." if market else "",
            f"Resumo automático por IA a partir de {num(stats.articles_collected)} notícias de {outlets} veículos.\n"
            "Salve e mande no grupo de quem precisa começar o dia informado. "
            f"Edição completa, com {len(edition.stories)} matérias e como cada veículo cobriu cada assunto: link na bio.",
            hashtags("#noticias", sections, election=election),
        ]
    )
    return Peca(
        slot="manha",
        kind="carousel",
        title="Em 1 minuto",
        slides=slides,
        caption=caption,
        story_ids=[i["story"].id for i in items if i["story"] is not None],
    )


def terminal(edition: Edition, brand: dict[str, Any]) -> Peca | None:
    """Post único com os indicadores (dias de edição automática, sem IA)."""
    quotes = quote_items(edition, [q.id for q in edition.quotes])
    if len(quotes) < 4:
        return None
    day = edition_day(edition)
    hook = f"Terminal da manhã · {short_date(day)}: " + quote_sentence(quotes, ["usd", "ibov"])
    caption = finish_caption(
        [
            hook,
            "\n".join(f"▸ {q['label']}: {q['value']}" + (f" ({', '.join(x for x in (q['change'], q['ref']) if x)})" if q["change"] or q["ref"] else "") for q in quotes),
            "Fontes: Yahoo Finance e Banco Central. Edição de hoje: link na bio.",
            "#triangula #mercadofinanceiro #economia #investimentos",
        ]
    )
    return Peca(
        slot="manha",
        kind="image",
        title="Terminal da manhã",
        slides=[
            Slide(
                "terminal",
                {"kicker": f"TERMINAL DA MANHÃ · {kicker_date(day)}", "quotes": quotes, "weather": edition.weather[:1]},
                alt="Terminal da manhã: " + quote_sentence(quotes, [q["id"] for q in quotes]) + ".",
            )
        ],
        caption=caption,
    )


def stories_manha(edition: Edition, brand: dict[str, Any], *, election: bool) -> Peca | None:
    """Sequência de stories da manhã: bom dia + clima, manchete, mercado e chamada para o feed."""
    day = edition_day(edition)
    slides: list[Slide] = []
    weather = edition.weather[0] if edition.weather else None
    slides.append(
        Slide("story_bomdia", {"weekday": DIAS[day.weekday()], "date": short_date(day), "weather": weather})
    )
    lead = edition.story(edition.lead)
    if edition.mode == "ai" and not (election and is_political(lead, f"{lead.headline} {lead.dek}")):
        slides.append(
            Slide(
                "story_manchete",
                {
                    "section": section_info(edition, lead.section),
                    "headline": plain(lead.headline),
                    "why": plain(lead.why_it_matters or lead.dek),
                    "sources": sources_line(source_names(lead)),
                },
            )
        )
    quotes = quote_items(edition, STORY_MERCADO_IDS)
    if quotes:
        slides.append(Slide("story_mercado", {"quotes": quotes, "date": short_date(day)}))
    stats = edition.stats
    outlets = edition.rationale.outlets if edition.rationale else 0
    if edition.mode == "ai":
        slides.append(
            Slide(
                "story_feed",
                {"articles": num(stats.articles_collected), "outlets": outlets, "stories": len(edition.stories)},
            )
        )
    if len(slides) < 2:
        return None
    return Peca(slot="stories", kind="story", title="Stories da manhã", slides=slides)


def raiox_score(story: Story) -> float:
    c = story.coverage
    assert c is not None
    a, b = c.count("a"), c.count("b")
    return 2 * story.importance + 4 * min(a, b) + 0.5 * (a + b)


def raiox_story(edition: Edition, *, election: bool, exclude: Iterable[str] = ()) -> Story | None:
    """A matéria do Raio-X: cobertura dividida (lado A e lado B com veículos), ≥4 veículos
    com enquadramento e ≥3 fontes; no período eleitoral, nada de política nem pesquisa."""
    excluded = set(exclude)
    best: tuple[float, int, Story] | None = None
    for i, story in enumerate(ordered_stories(edition)):
        c = story.coverage
        if story.id in excluded or c is None or not c.has_debate:
            continue
        if c.count("a") < 1 or c.count("b") < 1:
            continue
        if sum(1 for o in c.outlets if o.framing.strip()) < 4 or len(source_names(story)) < 3:
            continue
        if election and is_political(story, f"{story.headline} {story.dek} {c.topic} {c.side_a} {c.side_b}"):
            continue
        key = (raiox_score(story), -i, story)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else None


def _outlet_ctx(o: CoverageOutlet) -> dict[str, str]:
    return {"name": o.name, "framing": plain(o.framing), "stance": o.stance}


def raio_x(edition: Edition, brand: dict[str, Any], *, election: bool, exclude: Iterable[str] = ()) -> Peca | None:
    """Carrossel da noite: como a imprensa cobriu o assunto que mais a dividiu (7 slides)."""
    if edition.mode != "ai":
        return None
    story = raiox_story(edition, election=election, exclude=exclude)
    if story is None:
        return None
    c: Coverage = story.coverage  # type: ignore[assignment]
    day = edition_day(edition)
    side_a = [o for o in c.outlets if o.stance == "a"]
    side_b = [o for o in c.outlets if o.stance == "b"]
    neutral = [o for o in c.outlets if o.stance not in ("a", "b")]
    section = section_info(edition, story.section)
    counts = {"a": len(side_a), "b": len(side_b), "n": len(neutral), "total": len(c.outlets)}
    common = {
        "section": section,
        "counts": counts,
        "side_a": plain(c.side_a),
        "side_b": plain(c.side_b),
        "headline": plain(story.headline),
        "a": [o.name for o in side_a],
        "b": [o.name for o in side_b],
        "n": [o.name for o in neutral],
    }
    slides = [
        Slide(
            "raiox_capa",
            {**common, "kicker": f"RAIO-X DA COBERTURA · {kicker_date(day)}", "headline": plain(story.headline)},
            alt=f"Raio-X da cobertura: {plain(story.headline)}. {counts['total']} veículos; "
            f"lado A: {plain(c.side_a)} ({counts['a']}); lado B: {plain(c.side_b)} ({counts['b']}); neutros: {counts['n']}.",
        ),
        Slide(
            "raiox_fato",
            {**common, "headline": plain(story.headline), "dek": plain(story.dek)},
            alt=f"O fato: {plain(story.dek)}",
        ),
    ]
    for side, outlets, label in (("a", side_a, c.side_a), ("b", side_b, c.side_b)):
        slides.append(
            Slide(
                "raiox_lado",
                {**common, "side": side, "label": plain(label), "outlets": [_outlet_ctx(o) for o in outlets]},
                alt=f"Lado {side.upper()}: {plain(label)}. "
                + " ".join(f"{o.name}: {plain(o.framing)}" for o in outlets),
            )
        )
    slides.append(
        Slide(
            "raiox_espectro",
            {
                **common,
                "a": [o.name for o in side_a],
                "b": [o.name for o in side_b],
                "n": [o.name for o in neutral],
            },
            alt=f"O espectro: lado A ({', '.join(o.name for o in side_a)}); neutros ({', '.join(o.name for o in neutral) or 'nenhum'}); "
            f"lado B ({', '.join(o.name for o in side_b)}).",
        )
    )
    slides.append(
        Slide(
            "raiox_conclusao",
            {**common, "conclusion": plain(c.conclusion), "why": plain(story.why_it_matters or "")},
            alt=f"Conclusão: {plain(c.conclusion)}" + (f" Por que importa: {plain(story.why_it_matters)}" if story.why_it_matters else ""),
        )
    )
    slides.append(
        Slide(
            "raiox_metodo",
            {**common},
            alt="Como fazemos: a IA compara os textos publicados e classifica a ênfase de cada veículo. "
            "Enquadramento não é erro nem mentira. Leu só uma versão? Mande para quem leu a outra. Edição completa no link da bio.",
        )
    )
    hook = f"Mesmo fato, 2 enquadramentos: {plain(c.topic) or plain(story.headline)}"
    caption = finish_caption(
        [
            hook,
            "\n".join(
                [
                    f"▸ O fato: {plain(story.dek)}",
                    f"▸ Lado A — {plain(c.side_a)}: {', '.join(o.name for o in side_a)}.",
                    f"▸ Lado B — {plain(c.side_b)}: {', '.join(o.name for o in side_b)}.",
                    *([f"▸ Sem tomar lado: {', '.join(o.name for o in neutral)}."] if neutral else []),
                    f"▸ Conclusão: {plain(c.conclusion)}",
                    *([f"▸ Por que importa: {plain(story.why_it_matters)}"] if story.why_it_matters else []),
                ]
            ),
            "Análise automática por IA dos textos de cada veículo. Enquadramento é a escolha do que destacar, não erro.\n"
            "Leu só uma versão? Mande para quem leu a outra. Os assuntos comparados hoje estão na edição completa: link na bio.",
            hashtags("#jornalismo", [story.section], election=election),
        ]
    )
    return Peca(slot="raiox", kind="carousel", title="Raio-X da cobertura", slides=slides, caption=caption, story_ids=[story.id])


def comece_aqui(edition: Edition, brand: dict[str, Any]) -> Peca:
    """Carrossel de apresentação (o primeiro post do perfil, para fixar): sai uma única vez."""
    stats = edition.stats
    outlets = edition.rationale.outlets if edition.rationale else 0
    # O post fica fixado no perfil: números arredondados, não a contagem do dia.
    articles = more_than(stats.articles_collected, 100)
    texts = [
        f"**Como funciona.** Toda manhã, um robô coleta as notícias de {more_than(stats.sources_total, 10)} fontes, "
        f"de {more_than(outlets, 10)} veículos. "
        "Uma IA compila as matérias do dia por editoria e compara como cada veículo cobriu cada assunto.",
        "**O que sai aqui.** Toda manhã, o **Em 1 minuto**: os fatos do dia e o mercado. "
        "À noite, o **Raio-X da cobertura**: o assunto em que a imprensa mais se dividiu.",
        "**Lado A x lado B.** Mesmo fato, enquadramentos diferentes. O Raio-X mostra o que cada veículo destacou. "
        "Enquadramento é a escolha do que destacar, não erro nem mentira.",
        "**O que não fazemos.** Sem opinião, sem foto de jornal, sem pesquisa eleitoral e sem publicidade política. "
        "Tudo é resumo automático por IA, sempre com as fontes.",
    ]
    total = len(texts)
    slides = [
        Slide(
            "capa",
            {
                "kicker": "COMECE AQUI",
                "headline": f"Todo dia, {articles} notícias. 1 minuto para você.",  # "mais de 1.500"
                "dek": "A Triangula lê a imprensa inteira e mostra o que aconteceu, quanto o mercado andou "
                "e como cada veículo contou a mesma história.",
                "section": {"id": "triangula", "title": "Triangula", "color": "#FF5A1F"},
                "teaser": "Como funciona · arraste",
            },
            alt=f"Comece aqui. Todo dia, {articles} notícias. 1 minuto para você.",
        )
    ]
    for n, value in enumerate(texts, 1):
        slides.append(
            Slide("fato", {"n": n, "total": total, "text": value, "section": None, "sources": "", "outlets": 0}, alt=plain(value))
        )
    slides.append(
        Slide(
            "fechamento",
            {
                "articles": articles,
                "outlets": more_than(outlets, 10),
                "stories": len(edition.stories),
                "compared": sum(1 for s in edition.stories if s.coverage) + len(edition.compared),
                "cta": "Salve · Envie · Siga",
            },
            alt=f"Todo dia, lemos {articles} notícias de {more_than(outlets, 10)} veículos. A edição completa, "
            "com o que cada veículo destacou em cada assunto, está no link da bio. Salve, envie, siga.",
        )
    )
    caption = finish_caption(
        [
            "Comece aqui: o jornal que lê a imprensa inteira por você",
            f"Toda manhã, a Triangula lê {articles} notícias de {more_than(outlets, 10)} veículos e entrega três coisas: "
            "o que aconteceu, quanto o mercado andou e como cada veículo contou a mesma história.",
            "▸ Toda manhã: Em 1 minuto, os fatos do dia e o mercado.\n"
            "▸ À noite: Raio-X da cobertura, o lado A x lado B da imprensa.\n"
            "▸ Conta automática: resumo por IA, sempre com as fontes. Sem opinião e sem foto de jornal.",
            "Siga e ative as notificações. Edição completa do dia: link na bio.",
            "#triangula #noticias #jornalismo #mercadofinanceiro",
        ]
    )
    return Peca(slot="lancamento", kind="carousel", title="Comece aqui", slides=slides, caption=caption)
