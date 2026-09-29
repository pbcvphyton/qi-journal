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
from qijournal.edit.assemble import assemble_edition, edition_stats, plain_text, unique_story_id
from qijournal.edit.cluster import (
    Cluster,
    as_utc,
    editorial_score,
    is_service_title,
    parse_iso,
    rank_clusters,
)
from qijournal.models import Article, Bundle, Edition, SourceRef, Story

if TYPE_CHECKING:
    from qijournal.collect.enrich import PageInfo

log = logging.getLogger(__name__)

MAX_PER_SECTION = 6
MIN_PER_SECTION = 2  # reservadas por seção (quando houver candidatas), antes de completar por score
# O dia econômico brasileiro sempre aparece (Focus, juros, câmbio, Ibovespa).
MIN_BY_SECTION = {"brasil": 4, "mercados": 3}
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
LEAD_MIN_BODY = 200  # manchete precisa de texto de verdade (não só título)
BRIEFING_ITEMS = 5
MIN_TEXT_CHARS = 200  # texto do principal menor que isso: usa o de outro artigo do grupo
SOURCE_MIN_SIMILARITY = 0.1  # crédito de artigo que não se parece com nenhum outro do grupo sai
INTERTITLE_MAX = 40  # linha curta sem pontuação no meio do texto = intertítulo
EN_DUPLICATE_RARE_DF = 3  # radical "raro" entre os clusters (nomes, lugares)
EN_DUPLICATE_SHARED = 2  # cluster só em inglês que repete um fato já escolhido em português
WIRE_SIZE = 15  # itens do Radar (notícias além das matérias da edição)
WIRE_SECTIONS = ("brasil", "mercados", "juridico", "imobiliario")
WIRE_POOL_FACTOR = 3  # o Radar escolhe os mais recentes entre os 3×N mais relevantes

# Miniaturas de feed (WordPress "-300x200.jpg", "?w=150", "?fit=300%2C200", BBC "/standard/240/").
_THUMB = re.compile(
    r"-\d{2,4}x\d{2,4}\.(?:jpe?g|png|webp)(?:\?|$)|[?&](?:w|fit)=\d{2,3}(?:\b|%2C)|/standard/\d{3}/", re.I
)
_PUNCTUATION = re.compile(r"[.!?…:;]")


# ── texto ──────────────────────────────────────────────────────────────────


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _split_sentences(value: str) -> list[str]:
    """Frases do texto sem cortar em abreviações (ver :func:`text.split_sentences`)."""
    return text.split_sentences(_collapse(value))


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


def _clean(value: str) -> str:
    """Tira boilerplate e avisos de paywall (defesa para bundles antigos e páginas)."""
    from qijournal.collect.feeds import strip_boilerplate  # import tardio: módulo da coleta

    return strip_boilerplate(text.strip_paywall(value or "")).strip()


def _best_text(article: Article, info: PageInfo | None) -> str:
    """Texto mais completo disponível: resumo do feed ou corpo da página (o mais longo,
    depois de limpos); a descrição da página só quando não houver nenhum dos dois."""
    summary = _clean(article.summary)
    page_text = _clean(page_field(info, "text"))
    best = page_text if len(page_text) > len(summary) else summary
    return best or _clean(page_field(info, "description"))


def _story_text(articles: list[Article], page_info: Mapping[str, PageInfo] | None) -> str:
    """Texto da matéria: o do principal; se ele for só um "stub" (paywall, nota
    curta), o mais longo entre os artigos do grupo — primeiro no idioma do
    principal (ou pt), depois nos demais."""
    info = page_info or {}
    primary = articles[0]
    best = _best_text(primary, info.get(primary.id))
    if len(best) >= MIN_TEXT_CHARS:
        return best
    preferred = {primary.lang, "pt"}
    for group in (
        [a for a in articles[1:] if a.lang in preferred],
        [a for a in articles[1:] if a.lang not in preferred],
    ):
        longest = max((_best_text(a, info.get(a.id)) for a in group), key=len, default="")
        if len(longest) >= MIN_TEXT_CHARS:
            return longest
    return best


_Sentence = tuple[int, str]  # (linha do texto original, frase)


def _is_intertitle(line: str) -> bool:
    """Intertítulo ("Cotação", "Dívida interna"): linha curta, sem pontuação, poucas palavras."""
    return len(line) <= INTERTITLE_MAX and not _PUNCTUATION.search(line) and len(line.split()) <= 6


def _sentences(source_text: str) -> list[_Sentence]:
    """Frases do texto, lembrando de que linha (bloco) do original cada uma veio.

    Descarta frases de paywall/cadastro e intertítulos no meio do texto.
    """
    out: list[_Sentence] = []
    lines = [line.strip() for line in (source_text or "").split("\n") if line.strip()]
    for block, line in enumerate(lines):
        if block < len(lines) - 1 and _is_intertitle(line):
            continue
        out.extend((block, sentence) for sentence in _split_sentences(line) if not text.is_paywall(sentence))
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


def _title_stems(article: Article) -> set[str]:
    return {t[:5] for t in text.tokens(article.title)}


def _title_similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def sources_for(articles: list[Article]) -> list[SourceRef]:
    """Créditos: nome + link de cada fonte, sem repetir nome (principal primeiro).

    Quando a mesma fonte tem vários artigos no grupo, o link é o do artigo mais
    "central" (título mais parecido com os dos demais membros, em qualquer
    idioma). Depois, um crédito cujo título não se parece com nenhum dos outros
    créditos (< :data:`SOURCE_MIN_SIMILARITY`) sai: um agrupamento imperfeito
    não manda o leitor para uma matéria de outro assunto.
    """
    if not articles:
        return []
    stems = [_title_stems(a) for a in articles]

    def similarity(i: int, j: int) -> float:
        return _title_similarity(stems[i], stems[j])

    centrality = [
        sum(similarity(i, j) for j, other in enumerate(articles) if j != i and other.source_id != article.source_id)
        for i, article in enumerate(articles)
    ]
    best: dict[str, int] = {}
    for i, article in enumerate(articles):
        name = (article.source_name or article.source_id).strip()
        if not name:
            continue
        current = best.get(name)
        if current is None or (current != 0 and centrality[i] > centrality[current]):
            best[name] = i  # o principal (índice 0) nunca é trocado
    chosen = sorted(best.values())

    def on_topic(i: int) -> bool:
        others = [similarity(i, j) for j in chosen if j != i and articles[j].source_id != articles[i].source_id]
        return i == 0 or not others or max(others) >= SOURCE_MIN_SIMILARITY

    return [
        SourceRef(name=(articles[i].source_name or articles[i].source_id).strip(), url=articles[i].url)
        for i in chosen
        if on_topic(i)
    ]


def image_for(articles: list[Article], page_info: Mapping[str, PageInfo] | None) -> str | None:
    """Melhor imagem do grupo.

    Ordem: imagem do feed do principal (se não for miniatura) → ``og:image`` do
    principal → imagem de feed (não miniatura) de outro artigo → ``og:image`` de
    outro artigo → qualquer miniatura, como último recurso. Miniaturas de feed
    (150–300 px) esticadas ficam borradas no card e no destaque.
    """
    if not articles:
        return None
    info = page_info or {}

    def page_image(article: Article) -> str:
        image = page_field(info.get(article.id), "image")
        return image if image.startswith(("http://", "https://")) else ""

    primary = articles[0]
    if primary.image and not _THUMB.search(primary.image):
        return primary.image
    if page_image(primary):
        return page_image(primary)
    for article in articles:
        if article.image and not _THUMB.search(article.image):
            return article.image
    for article in articles:
        if page_image(article):
            return page_image(article)
    return next((a.image for a in articles if a.image), None)


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
    source_text = _story_text(articles, page_info)

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
        lang=primary.lang or "pt",
    )


# ── seleção das matérias ───────────────────────────────────────────────────


def _rare_stems(clusters: list[Cluster]) -> list[set[str]]:
    """Radicais do título + começo do resumo (3 primeiros artigos) que aparecem em
    no máximo :data:`EN_DUPLICATE_RARE_DF` clusters: nomes, lugares, números."""
    stems: list[set[str]] = []
    for cluster in clusters:
        found: set[str] = set()
        for article in cluster.articles[:3]:
            found |= {t[:5] for t in text.tokens(f"{article.title} {(article.summary or '')[:200]}")}
        stems.append(found)
    df = Counter(stem for found in stems for stem in found)
    return [{stem for stem in found if df[stem] <= EN_DUPLICATE_RARE_DF} for found in stems]


def select_clusters(clusters: list[Cluster], config: Config) -> list[Cluster]:
    """Escolhe até ``target_stories`` clusters com diversidade.

    A ordem é a de :func:`~qijournal.edit.cluster.editorial_score` (peso da seção
    para o leitor brasileiro e fração de artigos em português).

    1. garante os melhores clusters de cada seção que tenha candidatos: primeiro
       o melhor de cada uma, depois o segundo… até :data:`MIN_PER_SECTION` por
       seção (:data:`MIN_BY_SECTION` para Brasil e Mercados);
    2. completa por score respeitando :data:`MAX_PER_SECTION` por seção;
    3. se ainda faltar, completa ignorando o teto.

    Um cluster só em inglês que repete um fato já escolhido em português (dois
    ou mais radicais raros em comum: "Fairford" + "RAF") fica de fora. Devolve
    em ordem de score editorial decrescente.
    """
    target = max(1, config.edition.target_stories)
    scores = {c.key: editorial_score(c) for c in clusters}
    clusters = sorted(clusters, key=lambda c: (-scores[c.key], c.key))
    rare = dict(zip((c.key for c in clusters), _rare_stems(clusters), strict=True))
    chosen: list[Cluster] = []
    chosen_keys: set[str] = set()
    per_section: Counter[str] = Counter()

    def repeats_chosen(cluster: Cluster) -> bool:
        if any(a.lang == "pt" for a in cluster.articles):
            return False
        return any(
            len(rare[cluster.key] & rare[c.key]) >= EN_DUPLICATE_SHARED
            for c in chosen
            if any(a.lang == "pt" for a in c.articles)
        )

    def add(cluster: Cluster) -> None:
        chosen.append(cluster)
        chosen_keys.add(cluster.key)
        per_section[cluster.section] += 1

    by_section: dict[str, list[Cluster]] = {}
    for cluster in clusters:  # já em ordem de score editorial
        by_section.setdefault(cluster.section, []).append(cluster)
    tiers = max([MIN_PER_SECTION, *MIN_BY_SECTION.values()])
    for rank in range(tiers):  # 1º de cada seção, depois o 2º de cada seção…
        tier = [
            group[rank]
            for section, group in by_section.items()
            if len(group) > rank and rank < MIN_BY_SECTION.get(section, MIN_PER_SECTION)
        ]
        for cluster in sorted(tier, key=lambda c: (-scores[c.key], c.key)):
            if len(chosen) >= target:
                break
            if not repeats_chosen(cluster):
                add(cluster)

    for respect_cap in (True, False):
        for cluster in clusters:
            if len(chosen) >= target:
                break
            if cluster.key in chosen_keys or repeats_chosen(cluster):
                continue
            if respect_cap and per_section[cluster.section] >= MAX_PER_SECTION:
                continue
            add(cluster)

    return sorted(chosen, key=lambda c: (-scores[c.key], c.key))


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


def _lead_ok(story: Story) -> bool:
    """Candidata a manchete: não é serviço/opinião e tem texto de verdade."""
    body = sum(len(p) for p in story.body)
    return not is_service_title(story.headline) and max(body, len(story.dek)) >= LEAD_MIN_BODY


def _choose_lead(stories: list[Story], clusters: list[Cluster]) -> Story:
    """Manchete entre as mais bem pontuadas (score editorial): pt com imagem →
    pt → a primeira, sempre pulando conteúdo de serviço e matérias sem texto."""
    window = list(zip(stories, clusters, strict=True))[:LEAD_WINDOW]
    rules = (
        lambda story, cluster: cluster.primary.lang == "pt" and bool(story.image) and _lead_ok(story),
        lambda story, cluster: cluster.primary.lang == "pt" and _lead_ok(story),
        lambda story, cluster: _lead_ok(story),
    )
    for rule in rules:
        eligible = [(story, cluster) for story, cluster in window if rule(story, cluster)]
        if eligible:
            return max(eligible, key=lambda pair: editorial_score(pair[1]))[0]
    return stories[0]


def _briefing(edition: Edition) -> list[str]:
    """"Em 1 minuto" da edição automática: a melhor matéria de cada seção que não
    está na manchete, nas chamadas nem nos destaques (que o leitor já vê)."""
    shown = {edition.lead, *edition.secondary, *edition.highlights}
    items: list[str] = []
    for section in edition.sections:
        story_id = next((sid for sid in section.story_ids if sid not in shown), None)
        if story_id:
            items.append(edition.story(story_id).headline)
        if len(items) >= BRIEFING_ITEMS:
            break
    return items


def wire_items(clusters: list[Cluster], used_article_ids: set[str], *, limit: int = WIRE_SIZE) -> list[dict]:
    """Radar: notícias recentes que não viraram matéria (Brasil, Mercados, Jurídico e
    Imobiliário, em português), sem conteúdo de serviço, uma por cluster (o artigo
    principal). Entre as mais relevantes (score editorial), as mais recentes."""
    eligible = [
        cluster
        for cluster in clusters
        if cluster.section in WIRE_SECTIONS
        and cluster.primary.published
        and cluster.primary.lang == "pt"
        and not is_service_title(cluster.primary.title)
        and not any(a.id in used_article_ids for a in cluster.articles)
    ]
    eligible.sort(key=lambda c: (-editorial_score(c), c.key))
    items: list[dict] = []
    for cluster in eligible[: limit * WIRE_POOL_FACTOR]:
        primary = cluster.primary
        title = plain_text(primary.title, HEADLINE_MAX)
        if not title:
            continue
        items.append(
            {
                "title": title,
                "url": primary.url,
                "source": (primary.source_name or primary.source_id).strip(),
                "published": primary.published,
                "section": cluster.section,
            }
        )

    def moment(item: dict) -> float:
        dt = parse_iso(item["published"])
        return dt.timestamp() if dt else 0.0

    return sorted(items, key=moment, reverse=True)[:limit]


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
    ranked = rank_clusters(bundle.articles, config, now=now)
    clusters = select_clusters(ranked, config)
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
    edition.briefing = _briefing(edition)
    edition.wire = wire_items(ranked, {a.id for c in clusters for a in c.articles})
    log.info(
        "Edição automática: %d matérias em %d seções (manchete: %s)",
        len(stories),
        len(edition.sections),
        lead.headline,
    )
    return edition
