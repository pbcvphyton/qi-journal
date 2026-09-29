"""Agrupamento, classificação e ranqueamento de artigos.

É a base comum dos dois editores: a edição heurística usa os clusters
diretamente e a edição por IA usa o ranqueamento para escolher (e ordenar) os
candidatos enviados ao modelo.

- :func:`cluster_articles` junta artigos sobre o mesmo fato (títulos parecidos).
- :func:`classify` escolhe a seção por palavras-chave + dicas do feed.
- :func:`score_cluster` mede a importância (fontes distintas, frescor, imagem…).
- :func:`rank_clusters` combina tudo e devolve os clusters do mais ao menos importante.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from qijournal import text
from qijournal.config import Config, SectionConfig
from qijournal.models import Article

log = logging.getLogger(__name__)

# ── Agrupamento ────────────────────────────────────────────────────────────
SUMMARY_PREFIX_CHARS = 200  # trecho do resumo comparado entre artigos
SUMMARY_SIMILAR_MIN = 0.30  # Jaccard mínimo para considerar os resumos "parecidos"
SUMMARY_BONUS = 0.15  # somado à similaridade dos títulos quando os resumos batem
MAX_REPRESENTATIVES = 8  # membros de cada cluster usados na comparação
# Regras de conteúdo (complementam a regra clássica de títulos parecidos). Usam
# radicais (5 primeiras letras: "decreto"/"decretos", "model"/"modelo",
# "tarifas"/"tariffs") e números, pesados por IDF no lote de artigos, para
# juntar o mesmo fato contado com palavras diferentes ("AGU processa bets…" /
# "União entra com ações contra bets…") e, com mais exigência, em outro idioma.
# Calibradas em tests/fixtures/bundle.json: nenhum par de fatos distintos passa.
STEM_CHARS = 5
LEAD_CHARS = 300  # título + começo do resumo
CONTENT_TITLE_MIN = 0.35  # cosseno dos radicais do título…
CONTENT_LEAD_MIN = 0.10  # …e do título + começo do resumo
LEAD_STRONG_MIN = 0.30  # começos de resumo muito parecidos bastam
CROSS_LANG_TITLE_MIN = 0.20  # idiomas diferentes: só o título conta…
MIN_SHARED_STEMS = 2  # radicais do título em comum exigidos pelas regras de conteúdo
CROSS_LANG_SHARED_STEMS = 3  # …e com pelo menos 3 radicais em comum (nomes, siglas, cognatos)

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# ── Classificação ──────────────────────────────────────────────────────────
TOPIC_BONUS = 3.0  # seção indicada pelo próprio feed
SHORT_KEYWORD_MAX = 3  # palavras-chave com até 3 letras só casam como palavra inteira
KEYWORD_SUFFIX_MAX = 3  # letras extras toleradas após a palavra-chave (plural, gênero, radical)

# ── Pontuação ──────────────────────────────────────────────────────────────
REPEATED_SOURCE_FACTOR = 0.3  # artigos extras de uma fonte já contada
FRESH_HOURS = 6.0  # até aqui não há decaimento por idade
MIN_AGE_FACTOR = 0.35  # fator na idade máxima (max_age_hours) ou além
UNDATED_AGE_FACTOR = 0.5  # artigos sem data
IMAGE_BONUS = 1.15
LONG_SUMMARY_CHARS = 200
LONG_SUMMARY_BONUS = 1.05
SHORT_TITLE_CHARS = 25
SHORT_TITLE_PENALTY = 0.6
LIVE_PENALTY = 0.5

_LIVE_RE = re.compile(r"(?<![a-z0-9])(?:ao vivo|en vivo|live)(?![a-z0-9])")


@dataclass
class Cluster:
    """Grupo de artigos sobre o mesmo fato, já classificado e pontuado."""

    articles: list[Article]  # ordenados: principal primeiro
    score: float
    section: str

    @property
    def primary(self) -> Article:
        return self.articles[0]

    @property
    def key(self) -> str:
        """Identificador estável do cluster (id do artigo principal)."""
        return self.primary.id


# ── utilidades de data ─────────────────────────────────────────────────────


def parse_iso(value: str | None) -> datetime | None:
    """Converte ISO 8601 em ``datetime`` com fuso (UTC se vier sem fuso); inválido → ``None``."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def as_utc(now: datetime) -> datetime:
    """Normaliza ``now`` para UTC (datetimes sem fuso são tratados como UTC)."""
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)


def _timestamp(article: Article) -> float:
    dt = parse_iso(article.published)
    return dt.timestamp() if dt else 0.0


# ── principal do cluster ───────────────────────────────────────────────────


def primary_sort_key(article: Article) -> tuple:
    """Chave de preferência para o artigo principal: pt > en/es, maior peso,
    com imagem, resumo mais longo, mais recente (e id para desempate estável)."""
    return (
        0 if article.lang == "pt" else 1,
        -article.weight,
        0 if article.image else 1,
        -len(article.summary or ""),
        -_timestamp(article),
        article.id,
    )


def order_primary_first(articles: list[Article]) -> list[Article]:
    """Devolve uma cópia ordenada com o melhor artigo principal na frente."""
    return sorted(articles, key=primary_sort_key)


# ── agrupamento ────────────────────────────────────────────────────────────


def _jaccard(a: set[str], b: set[str]) -> float:
    """Mesmo cálculo de :func:`text.similarity`, sobre tokens já calculados."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _stems(value: str) -> set[str]:
    """Radicais (prefixos de :data:`STEM_CHARS` letras) dos tokens significativos."""
    return {t[:STEM_CHARS] for t in text.tokens(value)}


def _numbers(value: str) -> set[str]:
    """Números com 2+ dígitos (``5,22`` = ``5.22``; ``US$ 60`` = ``$60``), marcados com ``#``."""
    out = set()
    for match in _NUMBER_RE.findall(value or ""):
        number = match.replace(",", ".")
        if len(number.replace(".", "")) >= 2:
            out.add("#" + number)
    return out


@dataclass
class _Features:
    """Tudo o que o agrupamento compara de um artigo (calculado uma vez)."""

    lang: str
    title_tokens: set[str]  # regra clássica (Jaccard dos títulos)
    summary_tokens: set[str]
    title_stems: set[str]  # regras de conteúdo (cosseno IDF)
    lead_stems: set[str]
    title_norm: float = 0.0
    lead_norm: float = 0.0


def _features(article: Article) -> _Features:
    summary = article.summary or ""
    title_stems = _stems(article.title) | _numbers(article.title)
    lead = summary[:LEAD_CHARS]
    return _Features(
        lang=article.lang,
        title_tokens=text.tokens(article.title),
        summary_tokens=text.tokens(summary[:SUMMARY_PREFIX_CHARS]),
        title_stems=title_stems,
        lead_stems=title_stems | _stems(lead) | _numbers(lead),
    )


def _idf_weights(features: list[_Features]) -> dict[str, float]:
    """Peso² (IDF suavizado) de cada radical no lote: nomes raros pesam mais que palavras comuns."""
    n = len(features)
    df = Counter(stem for f in features for stem in f.lead_stems)
    return {stem: math.log((n + 1) / (count + 0.5)) ** 2 for stem, count in df.items()}


def _cosine(a: set[str], b: set[str], norm_a: float, norm_b: float, weight: dict[str, float]) -> float:
    if not norm_a or not norm_b:
        return 0.0
    return sum(weight[t] for t in a & b) / (norm_a * norm_b)


def _classic_score(a: _Features, b: _Features, threshold: float) -> float:
    """Jaccard dos títulos (+ bônus se o começo dos resumos também for parecido)."""
    score = _jaccard(a.title_tokens, b.title_tokens)
    if score + SUMMARY_BONUS >= threshold and _jaccard(a.summary_tokens, b.summary_tokens) >= SUMMARY_SIMILAR_MIN:
        score += SUMMARY_BONUS
    return score


def _content_score(a: _Features, b: _Features, shared_stems: int, weight: dict[str, float]) -> float | None:
    """Similaridade pelas regras de conteúdo, ou ``None`` se nenhuma regra for atendida."""
    if shared_stems < MIN_SHARED_STEMS:
        return None
    title = _cosine(a.title_stems, b.title_stems, a.title_norm, b.title_norm, weight)
    if a.lang != b.lang:
        # resumos em idiomas diferentes quase não se parecem; exige mais do título
        ok = shared_stems >= CROSS_LANG_SHARED_STEMS and title >= CROSS_LANG_TITLE_MIN
        return title if ok else None
    lead = _cosine(a.lead_stems, b.lead_stems, a.lead_norm, b.lead_norm, weight)
    if (title >= CONTENT_TITLE_MIN and lead >= CONTENT_LEAD_MIN) or lead >= LEAD_STRONG_MIN:
        return (title + lead) / 2
    return None


def cluster_articles(articles: list[Article], *, threshold: float = 0.42) -> list[list[Article]]:
    """Agrupa (de forma gulosa) artigos que relatam o mesmo fato.

    Um artigo entra no cluster do membro mais parecido quando uma destas regras
    é atendida; senão abre um cluster novo:

    - **clássica**: Jaccard dos títulos (:func:`text.tokens`), com bônus quando o
      começo dos resumos também é parecido, atinge ``threshold``;
    - **conteúdo** (mesmo idioma): ao menos :data:`MIN_SHARED_STEMS` radicais do
      título em comum e cosseno IDF dos radicais do título ≥
      :data:`CONTENT_TITLE_MIN` com o de título + começo do resumo ≥
      :data:`CONTENT_LEAD_MIN` — ou este último ≥ :data:`LEAD_STRONG_MIN`;
    - **outro idioma**: :data:`CROSS_LANG_SHARED_STEMS` radicais do título em comum
      (nomes, siglas, números, cognatos) e cosseno do título ≥
      :data:`CROSS_LANG_TITLE_MIN`.

    Um índice invertido de radicais do título conta as interseções sem comparar
    todos contra todos, e só os primeiros :data:`MAX_REPRESENTATIVES` membros de
    cada cluster são comparados (limita o custo e o encadeamento de assuntos
    vizinhos). Cada cluster volta com o artigo principal na frente (ver
    :func:`primary_sort_key`).
    """
    ordered = sorted(articles, key=primary_sort_key)
    features = [_features(a) for a in ordered]
    weight = _idf_weights(features)
    for f in features:
        f.title_norm = math.sqrt(sum(weight[t] for t in f.title_stems))
        f.lead_norm = math.sqrt(sum(weight[t] for t in f.lead_stems))

    clusters: list[list[Article]] = []
    member_cluster: list[int] = []  # membro → cluster
    member_features: list[_Features] = []
    postings: dict[str, list[int]] = defaultdict(list)  # radical do título → membros

    for article, feat in zip(ordered, features, strict=True):
        overlap: Counter[int] = Counter()
        for stem in feat.title_stems:
            overlap.update(postings.get(stem, ()))

        best: tuple[float, int] | None = None  # (score, -cluster): maior score; empate → cluster mais antigo
        for member, shared in overlap.items():
            other = member_features[member]
            score: float | None = _classic_score(feat, other, threshold)
            if score < threshold:
                score = _content_score(feat, other, shared, weight)
            if score is None:
                continue
            candidate = (score, -member_cluster[member])
            if best is None or candidate > best:
                best = candidate

        if best is not None:
            target = -best[1]
            clusters[target].append(article)
        else:
            target = len(clusters)
            clusters.append([article])
        if len(clusters[target]) <= MAX_REPRESENTATIVES:
            member_cluster.append(target)
            member_features.append(feat)
            for stem in feat.title_stems:
                postings[stem].append(len(member_features) - 1)

    return [order_primary_first(group) for group in clusters]


# ── classificação ──────────────────────────────────────────────────────────


def _keyword_regex(keyword: str) -> str | None:
    """Trecho de regex de uma palavra-chave sobre texto já normalizado.

    - com espaço: frase (as palavras em sequência);
    - até 3 letras: só palavra inteira ("ia", "pt", "fed", "b3", "s&p");
    - demais: início de palavra, tolerando até 3 letras extras
      ("imobiliari" → "imobiliarios"; "bolsa" não casa com "bolsonaro").
    """
    norm = text.normalize(keyword)
    if not norm:
        return None
    body = re.escape(norm)
    if len(norm.replace(" ", "")) <= SHORT_KEYWORD_MAX:
        return body
    return rf"{body}[a-z0-9]{{0,{KEYWORD_SUFFIX_MAX}}}"


@lru_cache(maxsize=256)
def _section_pattern(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    """Uma regex por seção (alternância das palavras-chave, frases longas primeiro)."""
    parts = {part for part in (_keyword_regex(k) for k in keywords) if part}
    if not parts:
        return None
    alternation = "|".join(sorted(parts, key=lambda p: (-len(p), p)))
    return re.compile(rf"(?<![a-z0-9&])(?:{alternation})(?![a-z0-9&])")


def _keyword_hits(keywords: list[str], normalized_text: str) -> int:
    """Número de ocorrências (sem sobreposição) das palavras-chave no texto."""
    pattern = _section_pattern(tuple(keywords))
    return len(pattern.findall(normalized_text)) if pattern else 0


def classify(texts: str, topics: list[str], sections: list[SectionConfig]) -> str:
    """Escolhe a seção de um texto (título + resumo).

    Pontua cada seção pelo número de ocorrências de suas palavras-chave em
    ``text.normalize(texts)`` e soma :data:`TOPIC_BONUS` às seções indicadas em
    ``topics`` (dicas do feed). Empate → a primeira dica de ``topics`` entre as
    empatadas → a primeira seção (ordem da configuração).
    """
    if not sections:
        raise ValueError("nenhuma seção configurada")
    normalized = text.normalize(texts)
    topic_set = set(topics)
    scores = [
        _keyword_hits(section.keywords, normalized) + (TOPIC_BONUS if section.id in topic_set else 0.0)
        for section in sections
    ]
    best = max(scores)
    tied = [section.id for section, score in zip(sections, scores, strict=True) if score == best]
    for topic in topics:
        if topic in tied:
            return topic
    return tied[0]


# ── pontuação ──────────────────────────────────────────────────────────────


def _age_factor(published: datetime | None, now: datetime, max_age_hours: int) -> float:
    """1,0 até :data:`FRESH_HOURS`; cai linearmente até :data:`MIN_AGE_FACTOR` em ``max_age_hours``."""
    if published is None:
        return UNDATED_AGE_FACTOR
    age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
    if age_hours <= FRESH_HOURS:
        return 1.0
    span = max_age_hours - FRESH_HOURS
    if span <= 0:
        return MIN_AGE_FACTOR
    fraction = min(1.0, (age_hours - FRESH_HOURS) / span)
    return 1.0 - fraction * (1.0 - MIN_AGE_FACTOR)


def score_cluster(articles: list[Article], *, now: datetime, max_age_hours: int) -> float:
    """Importância de um grupo de artigos (quanto maior, mais relevante).

    Soma o peso das fontes distintas (artigos extras da mesma fonte valem
    :data:`REPEATED_SOURCE_FACTOR` do peso) e multiplica pelo fator de idade do
    artigo mais recente, pelo bônus de imagem e de resumo longo; penaliza
    títulos curtíssimos e coberturas "ao vivo".
    """
    if not articles:
        return 0.0
    now = as_utc(now)

    base = 0.0
    seen_sources: set[str] = set()
    for article in sorted(articles, key=lambda a: (-a.weight, a.id)):
        if article.source_id in seen_sources:
            base += REPEATED_SOURCE_FACTOR * article.weight
        else:
            seen_sources.add(article.source_id)
            base += article.weight

    dates = [dt for dt in (parse_iso(a.published) for a in articles) if dt is not None]
    score = base * _age_factor(max(dates) if dates else None, now, max_age_hours)

    if any(a.image for a in articles):
        score *= IMAGE_BONUS
    if max(len(a.summary or "") for a in articles) >= LONG_SUMMARY_CHARS:
        score *= LONG_SUMMARY_BONUS

    primary = min(articles, key=primary_sort_key)
    title = (primary.title or "").strip()
    if len(title) < SHORT_TITLE_CHARS:
        score *= SHORT_TITLE_PENALTY
    if _LIVE_RE.search(text.normalize(title)):
        score *= LIVE_PENALTY
    return round(score, 6)


def _cluster_topics(articles: list[Article]) -> list[str]:
    """Dicas de seção do cluster, sem repetição, começando pelas do principal."""
    topics: list[str] = []
    for article in articles:
        for topic in article.topics:
            if topic not in topics:
                topics.append(topic)
    return topics


def rank_clusters(articles: list[Article], config: Config, *, now: datetime) -> list[Cluster]:
    """Agrupa, classifica e pontua os artigos; devolve clusters por score decrescente."""
    now = as_utc(now)
    clusters: list[Cluster] = []
    for group in cluster_articles(articles):
        primary = group[0]
        classify_text = " ".join([primary.title, primary.summary or ""] + [a.title for a in group[1:]])
        clusters.append(
            Cluster(
                articles=group,
                score=score_cluster(group, now=now, max_age_hours=config.edition.max_age_hours),
                section=classify(classify_text, _cluster_topics(group), config.sections),
            )
        )
    clusters.sort(key=lambda c: (-c.score, c.key))
    log.debug("%d artigos agrupados em %d clusters", len(articles), len(clusters))
    return clusters
