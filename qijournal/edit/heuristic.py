"""Edição automática (sem IA).

Usa o ranqueamento de :mod:`qijournal.edit.cluster` para escolher as matérias
com diversidade entre seções e monta cada matéria a partir do texto dos feeds
(e, quando houver, do enriquecimento das páginas). As funções
:func:`story_from_articles`, :func:`sources_for` e :func:`image_for` também
são usadas pela edição por IA para preencher matérias que o modelo não redigiu.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from qijournal import text
from qijournal.config import Config
from qijournal.edit.assemble import assemble_edition, edition_stats, plain_text, top_headlines, unique_story_id
from qijournal.edit.cluster import Cluster, as_utc, rank_clusters
from qijournal.models import Article, Bundle, Edition, SourceRef, Story

if TYPE_CHECKING:
    from qijournal.collect.enrich import PageInfo

log = logging.getLogger(__name__)

MAX_PER_SECTION = 6
MIN_PER_SECTION = 2  # reservadas por seção (quando houver candidatas), antes de completar por score
HEADLINE_MAX = 140
DEK_MAX = 220
PARAGRAPH_MAX = 600
PARAGRAPH_MIN_TARGET = 280  # textos curtos viram um parágrafo só
BLOCK_BREAK_MIN = 200  # quebra de linha do original vira parágrafo se já houver corpo
ORPHAN_MAX = 140  # último parágrafo menor que isso é juntado ao anterior (se couber)
MAX_PARAGRAPHS = 3
TITLE_REPEAT_SIMILARITY = 0.6
TITLE_PREFIX_RATIO = 0.6  # frase que é começo do título só conta como repetição se cobrir boa parte dele
LEAD_WINDOW = 5  # a manchete é escolhida entre as N mais bem pontuadas
BRIEFING_ITEMS = 5

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"“'(]?[A-ZÁÉÍÓÚÂÊÔÃÕÇ])")


# ── texto ──────────────────────────────────────────────────────────────────


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _split_sentences(value: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(_collapse(value)) if s.strip()]


def _repeats_title(headline: str, sentence: str) -> bool:
    """A frase só repete o título (idêntica, prefixo ou muito parecida)?"""
    a, b = text.normalize(headline), text.normalize(sentence)
    if not a or not b:
        return False
    if b.startswith(a) or (a.startswith(b) and len(b) >= TITLE_PREFIX_RATIO * len(a)):
        return True
    return text.similarity(headline, sentence) >= TITLE_REPEAT_SIMILARITY


def page_field(info: PageInfo | None, name: str) -> str:
    """Campo de texto de um ``PageInfo`` (ou objeto equivalente), limpo; ausente → ``""``."""
    value = getattr(info, name, None) if info is not None else None
    return value.strip() if isinstance(value, str) else ""


def _best_text(article: Article, info: PageInfo | None) -> str:
    """Texto mais completo disponível: resumo do feed ou corpo da página (o mais longo);
    a descrição da página só quando não houver nenhum dos dois."""
    summary = (article.summary or "").strip()
    page_text = page_field(info, "text")
    best = page_text if len(page_text) > len(summary) else summary
    return best or page_field(info, "description")


_Sentence = tuple[int, str]  # (linha do texto original, frase)


def _sentences(source_text: str) -> list[_Sentence]:
    """Frases do texto, lembrando de que linha (bloco) do original cada uma veio."""
    out: list[_Sentence] = []
    for block, line in enumerate((source_text or "").split("\n")):
        out.extend((block, sentence) for sentence in _split_sentences(line))
    return out


def dek_and_body(headline: str, source_text: str) -> tuple[str, list[str]]:
    """Linha fina e corpo (parágrafos) a partir do texto da matéria.

    Frases iniciais que só repetem o título são descartadas. A linha fina reúne
    as primeiras frases inteiras que cabem em :data:`DEK_MAX`, reservando ao
    menos uma frase para o corpo; se a primeira frase não couber, a linha fina é
    ela truncada e o corpo mantém o texto inteiro. Texto de uma frase só vira
    linha fina e corpo.
    """
    sentences = _sentences(source_text)
    while sentences and _repeats_title(headline, sentences[0][1]):
        sentences.pop(0)
    if not sentences:
        return "", []

    candidates = sentences[:-1] if len(sentences) > 1 else sentences
    count = length = 0
    for _, sentence in candidates:
        new_length = length + (1 if count else 0) + len(sentence)
        if new_length > DEK_MAX:
            break
        count, length = count + 1, new_length
    if count:
        dek_raw = " ".join(sentence for _, sentence in sentences[:count])
        body_sentences = sentences[count:] or sentences
    else:
        dek_raw = text.truncate(sentences[0][1], DEK_MAX)
        body_sentences = sentences

    dek = plain_text(dek_raw, DEK_MAX)
    if _repeats_title(headline, dek):
        dek = ""
    return dek, _paragraphs(body_sentences)


def _paragraphs(sentences: list[_Sentence]) -> list[str]:
    """Agrupa frases em até :data:`MAX_PARAGRAPHS` parágrafos de até :data:`PARAGRAPH_MAX` chars.

    Mira ~2 parágrafos (alvo = metade do texto, entre 280 e 600 chars), sempre
    em fim de frase; uma troca de linha do original também fecha o parágrafo
    corrente quando ele já tem corpo. O que não couber é descartado (frases
    inteiras). O corpo heurístico é texto puro (sem negrito).
    """
    if not sentences:
        return []
    total = sum(len(sentence) + 1 for _, sentence in sentences)
    target = min(PARAGRAPH_MAX, max(PARAGRAPH_MIN_TARGET, total // 2 + 1))

    paragraphs: list[str] = []
    current = ""
    current_block = sentences[0][0]
    for block, sentence in sentences:
        if current and block != current_block and len(current) >= BLOCK_BREAK_MIN:
            paragraphs.append(current)
            current = ""
        current_block = block
        sentence = text.truncate(sentence, PARAGRAPH_MAX)
        if current and len(current) + 1 + len(sentence) > target:
            paragraphs.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        # evita um último parágrafo "órfão" de uma frase curta
        if paragraphs and len(current) < ORPHAN_MAX and len(paragraphs[-1]) + 1 + len(current) <= PARAGRAPH_MAX:
            paragraphs[-1] = f"{paragraphs[-1]} {current}"
        else:
            paragraphs.append(current)
    cleaned = (text.strip_markdown(p) for p in paragraphs[:MAX_PARAGRAPHS])
    return [p for p in cleaned if p]


# ── montagem de uma matéria ────────────────────────────────────────────────


def sources_for(articles: list[Article]) -> list[SourceRef]:
    """Créditos: nome + link de cada fonte, sem repetir nome (na ordem dos artigos)."""
    refs: list[SourceRef] = []
    seen: set[str] = set()
    for article in articles:
        name = (article.source_name or article.source_id).strip()
        if name and name not in seen:
            seen.add(name)
            refs.append(SourceRef(name=name, url=article.url))
    return refs


def image_for(articles: list[Article], page_info: Mapping[str, PageInfo] | None) -> str | None:
    """Imagem do principal → de outro artigo do grupo → do enriquecimento das páginas."""
    for article in articles:
        if article.image:
            return article.image
    for article in articles:
        image = page_field((page_info or {}).get(article.id), "image")
        if image.startswith(("http://", "https://")):
            return image
    return None


def story_from_articles(
    articles: list[Article],
    *,
    section: str,
    importance: int,
    page_info: Mapping[str, PageInfo] | None,
    taken: set[str],
) -> Story:
    """Matéria heurística a partir de um grupo de artigos (principal primeiro).

    - ``headline``: título original do principal, limpo (≤ 140 chars; artigos
      em inglês/espanhol mantêm o título original);
    - ``dek``/``body``: ver :func:`dek_and_body` (linha fina ≤ 220 chars;
      1-3 parágrafos de texto puro de até 600 chars);
    - ``sources``/``image``/``published`` a partir dos artigos.
    """
    if not articles:
        raise ValueError("matéria sem artigos")
    primary = articles[0]
    info = (page_info or {}).get(primary.id)
    source_text = _best_text(primary, info)

    headline = plain_text(primary.title, HEADLINE_MAX)
    if not headline:  # título só com marcação/HTML: usa a primeira frase do texto
        first = _split_sentences(source_text)
        headline = plain_text(first[0], HEADLINE_MAX) if first else ""
    if not headline:
        headline = plain_text(primary.source_name) or "Sem título"

    dek, body = dek_and_body(headline, source_text)

    return Story(
        id=unique_story_id(headline, taken),
        section=section,
        headline=headline,
        dek=dek,
        body=body,
        sources=sources_for(articles),
        article_ids=[a.id for a in articles],
        importance=importance,
        why_it_matters=None,
        image=image_for(articles, page_info),
        published=primary.published,
    )


# ── seleção das matérias ───────────────────────────────────────────────────


def select_clusters(clusters: list[Cluster], config: Config) -> list[Cluster]:
    """Escolhe até ``target_stories`` clusters com diversidade.

    1. garante o melhor cluster de cada seção que tenha candidatos e, em
       seguida, o segundo melhor (até :data:`MIN_PER_SECTION` por seção);
    2. completa por score respeitando :data:`MAX_PER_SECTION` por seção;
    3. se ainda faltar, completa ignorando o teto.
    Devolve em ordem de score decrescente.
    """
    target = max(1, config.edition.target_stories)
    chosen: list[Cluster] = []
    chosen_keys: set[str] = set()
    per_section: Counter[str] = Counter()

    def add(cluster: Cluster) -> None:
        chosen.append(cluster)
        chosen_keys.add(cluster.key)
        per_section[cluster.section] += 1

    by_section: dict[str, list[Cluster]] = {}
    for cluster in clusters:  # clusters já vêm por score decrescente
        by_section.setdefault(cluster.section, []).append(cluster)
    for rank in range(MIN_PER_SECTION):  # 1º de cada seção, depois o 2º de cada seção…
        tier = [group[rank] for group in by_section.values() if len(group) > rank]
        for cluster in sorted(tier, key=lambda c: (-c.score, c.key)):
            if len(chosen) >= target:
                break
            add(cluster)

    for respect_cap in (True, False):
        for cluster in clusters:
            if len(chosen) >= target:
                break
            if cluster.key in chosen_keys:
                continue
            if respect_cap and per_section[cluster.section] >= MAX_PER_SECTION:
                continue
            add(cluster)

    return sorted(chosen, key=lambda c: (-c.score, c.key))


def choose_clusters(bundle: Bundle, config: Config, *, now: datetime) -> list[Cluster]:
    """Clusters que a edição heurística usará (ranqueamento + seleção)."""
    return select_clusters(rank_clusters(bundle.articles, config, now=now), config)


def importance_for_rank(rank: int, total: int) -> int:
    """Importância 1-5 por posição no ranking: 3 primeiras → 5, depois por quantis."""
    if rank < 3:
        return 5
    fraction = rank / max(total, 1)
    if fraction < 0.30:
        return 4
    if fraction < 0.60:
        return 3
    if fraction < 0.85:
        return 2
    return 1


def _choose_lead(stories: list[Story], clusters: list[Cluster]) -> Story:
    """Manchete entre as mais bem pontuadas: pt com imagem → pt → a primeira."""
    window = list(zip(stories, clusters, strict=True))[:LEAD_WINDOW]
    for story, cluster in window:
        if cluster.primary.lang == "pt" and story.image:
            return story
    for story, cluster in window:
        if cluster.primary.lang == "pt":
            return story
    return stories[0]


def build_heuristic_edition(
    bundle: Bundle,
    config: Config,
    *,
    now: datetime,
    page_info: dict[str, PageInfo] | None = None,
) -> Edition:
    """Edição completa sem IA (``mode="heuristic"``).

    Levanta ``ValueError`` se o bundle não tiver artigos.
    """
    now = as_utc(now)
    clusters = choose_clusters(bundle, config, now=now)
    if not clusters:
        raise ValueError("nenhum artigo disponível para montar a edição")

    taken: set[str] = set()
    stories = [
        story_from_articles(
            cluster.articles,
            section=cluster.section,
            importance=importance_for_rank(rank, len(clusters)),
            page_info=page_info,
            taken=taken,
        )
        for rank, cluster in enumerate(clusters)
    ]
    lead = _choose_lead(stories, clusters)
    lead.importance = 5

    edition = assemble_edition(
        stories,
        bundle=bundle,
        config=config,
        now=now,
        mode="heuristic",
        model=None,
        editorial="",
        briefing=[],
        lead_id=lead.id,
        stats=edition_stats(bundle, articles_considered=len(bundle.articles)),
    )
    edition.briefing = top_headlines(edition, BRIEFING_ITEMS)
    log.info(
        "Edição automática: %d matérias em %d seções (manchete: %s)",
        len(stories),
        len(edition.sections),
        lead.headline,
    )
    return edition
