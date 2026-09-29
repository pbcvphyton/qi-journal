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
LEAD_STRONG_TITLE_MIN = 0.20  # …desde que o título também tenha algo em comum (ou 3+ radicais)
LEAD_STRONG_SHARED_STEMS = 3
CROSS_LANG_TITLE_MIN = 0.20  # idiomas diferentes: só o título conta…
MIN_SHARED_STEMS = 2  # radicais do título em comum exigidos pelas regras de conteúdo
CROSS_LANG_SHARED_STEMS = 3  # …e com pelo menos 3 radicais em comum (nomes, siglas, cognatos)
# Anti-encadeamento: pelas regras de conteúdo, o artigo também precisa lembrar o
# PRINCIPAL do cluster (não só um membro qualquer), senão "A~B~C" junta A e C.
PRIMARY_TITLE_MIN = 0.20
# Fusão de clusters: um artigo que casa com dois clusters (score ≥ MERGE_LINK_MIN)
# une os dois, enquanto o resultado couber em MERGE_MAX_SIZE artigos.
MERGE_LINK_MIN = 0.35
MERGE_MAX_SIZE = 25
# Segunda passada entre idiomas (pt × en): radicais raros em comum no título e começo.
CROSS_CLUSTER_RARE_DF = 6  # radical "raro": aparece em até 6 clusters
CROSS_CLUSTER_RARE_SHARED = 3  # radicais raros em comum (título + começo do resumo)
CROSS_CLUSTER_TITLE_SHARED = 2  # radicais em comum nos títulos

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Palavras de serviço que não identificam um fato ("veja", "quando", "dia"...).
_CLUSTER_NOISE = {"veja", "quando", "dia", "data", "confira", "saiba", "entenda", "opinion", "quem", "sao", "opiniao"}

# ── Classificação ──────────────────────────────────────────────────────────
TOPIC_BONUS = 3.0  # seção indicada pelo próprio feed
SHORT_KEYWORD_MAX = 3  # palavras-chave com até 3 letras só casam como palavra inteira
KEYWORD_SUFFIX_MAX = 3  # letras extras toleradas após a palavra-chave (plural, gênero, radical)

# ── Pontuação ──────────────────────────────────────────────────────────────
REPEATED_SOURCE_FACTOR = 0.3  # artigos extras de uma fonte já contada…
MAX_REPEATS_PER_SOURCE = 1  # …mas só o primeiro extra de cada fonte soma (séries "veja a lista" não sobem)
FRESH_HOURS = 6.0  # até aqui não há decaimento por idade
MIN_AGE_FACTOR = 0.35  # fator na idade máxima (max_age_hours) ou além
UNDATED_AGE_FACTOR = 0.5  # artigos sem data
IMAGE_BONUS = 1.15
LONG_SUMMARY_CHARS = 200
LONG_SUMMARY_BONUS = 1.05
SHORT_TITLE_CHARS = 25
SHORT_TITLE_PENALTY = 0.6
LIVE_PENALTY = 0.5
SERVICE_PENALTY = 0.2

_LIVE_RE = re.compile(r"(?<![a-z0-9])(?:ao vivo|en vivo|live)(?![a-z0-9])")
# Conteúdo de serviço/opinião (título normalizado): continua candidato, mas com
# score bem menor (listas de candidatos, loterias, "quinto dia útil", horóscopo...).
SERVICE_RE = re.compile(
    r"\bveja (?:a |o |os |as )?(?:lista|numero|nome|quem|data|os candidatos)|\bcandidatos a deputad"
    r"|\bquem sao os candidatos\b|\bquando (?:e|cai) o (?:dia|quinto)|\bquinto dia util\b|\bmega ?sena\b"
    r"|\blotofacil\b|\blotomania\b|\btimemania\b|\bdia de sorte\b|\bconfira o resultado\b"
    r"|\bresultado d[ao] (?:concurso|mega|lotofacil|quina|lotomania|timemania)|\bhoroscopo\b"
    r"|\b(?:como|onde) assistir\b|\bcomo declarar\b|\bgabarito\b|^opinion\b|\bopiniao\b"
)

# ── Peso editorial (heurística) ────────────────────────────────────────────
# Um jornal financeiro brasileiro: fatos de economia, mercado e jurídico do
# Brasil pesam mais que o fato global mais repercutido, e a versão em
# português pesa mais que a cobertura só em inglês.
SECTION_WEIGHT = {
    "brasil": 1.5,
    "mercados": 1.5,
    "juridico": 1.2,
    "imobiliario": 1.2,
    "politica": 1.0,
    "tecnologia": 1.0,
    "mundo": 0.85,
}


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


PRIMARY_SUMMARY_MIN = 400  # resumo "suficiente" para ser o principal


def primary_sort_key(article: Article) -> tuple:
    """Chave de preferência para o artigo principal: pt > en/es, maior peso,
    com imagem, resumo suficiente (≥ 400 caracteres), mais recente, resumo mais
    longo (e id para desempate estável).

    A data vem antes do tamanho do resumo: entre duas versões boas da mesma
    fonte, a mais nova (o fechamento, não a leitura do início da tarde) vence.
    """
    return (
        0 if article.lang == "pt" else 1,
        -article.weight,
        0 if article.image else 1,
        0 if len(article.summary or "") >= PRIMARY_SUMMARY_MIN else 1,
        -_timestamp(article),
        -len(article.summary or ""),
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
    """Radicais (prefixos de :data:`STEM_CHARS` letras) dos tokens significativos.

    Números puros ficam de fora (já entram por :func:`_numbers`, sem contar em
    dobro) e também palavras de serviço que não identificam um fato.
    """
    return {t[:STEM_CHARS] for t in text.tokens(value) if not t.isdigit() and t not in _CLUSTER_NOISE}


def is_service_title(title: str) -> bool:
    """Título de conteúdo de serviço ou opinião (ver :data:`SERVICE_RE`)?"""
    return bool(SERVICE_RE.search(text.normalize(title)))


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
    lower_stems: set[str]  # radicais de palavras em minúscula no título (não são nomes próprios)
    title_case: bool  # título em "Title Case" (NYT, WSJ): a caixa não distingue nomes próprios
    summary_tokens: set[str]
    title_stems: set[str]  # regras de conteúdo (cosseno IDF)
    lead_stems: set[str]
    title_norm: float = 0.0
    lead_norm: float = 0.0


TITLE_CASE_SHARE = 0.7


def _is_title_case(title: str) -> bool:
    words = [w for w in re.findall(r"[^\W\d_]{4,}", title or "")]
    return bool(words) and sum(1 for w in words if w[0].isupper()) / len(words) >= TITLE_CASE_SHARE


def _shares_common_word(a: _Features, b: _Features) -> bool:
    """Algo em comum nos títulos além de nomes próprios: um radical em minúscula em
    um dos títulos, ou um número. "Pope Leo criticises Nvidia's Jensen Huang" e
    "Pope Leo Praises Europe's Democracy" só têm o protagonista em comum."""
    if a.title_case and b.title_case:
        return True  # a caixa não informa nada nos dois títulos
    shared = a.title_stems & b.title_stems
    return any(s.startswith("#") or s in a.lower_stems or s in b.lower_stems for s in shared)


def _features(article: Article) -> _Features:
    summary = article.summary or ""
    title_stems = _stems(article.title) | _numbers(article.title)
    lead = summary[:LEAD_CHARS]
    return _Features(
        lang=article.lang,
        title_tokens=text.tokens(article.title),
        lower_stems=_stems(" ".join(w for w in (article.title or "").split() if w[:1].islower())),
        title_case=_is_title_case(article.title),
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
        # resumos em idiomas diferentes quase não se parecem; exige mais do título,
        # e que nem tudo em comum seja nome próprio ("Jensen Huang" + "Nvidia" em
        # notícias diferentes; ver _shares_common_word).
        ok = shared_stems >= CROSS_LANG_SHARED_STEMS and title >= CROSS_LANG_TITLE_MIN and _shares_common_word(a, b)
        return title if ok else None
    lead = _cosine(a.lead_stems, b.lead_stems, a.lead_norm, b.lead_norm, weight)
    strong_lead = lead >= LEAD_STRONG_MIN and (
        shared_stems >= LEAD_STRONG_SHARED_STEMS or title >= LEAD_STRONG_TITLE_MIN
    )
    if ((title >= CONTENT_TITLE_MIN and lead >= CONTENT_LEAD_MIN) or strong_lead) and _shares_common_word(a, b):
        return (title + lead) / 2
    return None


def _title_cosine(a: _Features, b: _Features, weight: dict[str, float]) -> float:
    return _cosine(a.title_stems, b.title_stems, a.title_norm, b.title_norm, weight)


def _primary_for(primaries: dict[str, _Features], feat: _Features) -> _Features:
    """Principal do cluster no idioma do artigo (ou o principal geral, o 1º inserido)."""
    return primaries.get(feat.lang) or next(iter(primaries.values()))


class _UnionFind:
    """Conjuntos disjuntos de clusters (fusões), com compressão de caminho."""

    def __init__(self) -> None:
        self.parent: list[int] = []

    def add(self) -> int:
        self.parent.append(len(self.parent))
        return len(self.parent) - 1

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root


def cluster_articles(articles: list[Article], *, threshold: float = 0.42) -> list[list[Article]]:
    """Agrupa (de forma gulosa) artigos que relatam o mesmo fato.

    Um artigo entra no cluster do membro mais parecido quando uma destas regras
    é atendida; senão abre um cluster novo:

    - **clássica**: Jaccard dos títulos (:func:`text.tokens`), com bônus quando o
      começo dos resumos também é parecido, atinge ``threshold``;
    - **conteúdo** (mesmo idioma): ao menos :data:`MIN_SHARED_STEMS` radicais do
      título em comum e cosseno IDF dos radicais do título ≥
      :data:`CONTENT_TITLE_MIN` com o de título + começo do resumo ≥
      :data:`CONTENT_LEAD_MIN` — ou este último ≥ :data:`LEAD_STRONG_MIN` (com
      algo em comum também no título);
    - **outro idioma**: :data:`CROSS_LANG_SHARED_STEMS` radicais do título em comum
      (nomes, siglas, números, cognatos) e cosseno do título ≥
      :data:`CROSS_LANG_TITLE_MIN`.

    Pelas regras de conteúdo, o artigo também precisa lembrar o principal do
    cluster (:data:`PRIMARY_TITLE_MIN`), o que impede o encadeamento de assuntos
    vizinhos. Quando um artigo casa com dois clusters (score ≥
    :data:`MERGE_LINK_MIN`), os dois são unidos (até :data:`MERGE_MAX_SIZE`
    artigos); no fim, uma segunda passada une clusters de idiomas diferentes que
    compartilham nomes raros (ver :func:`_merge_cross_language`).

    Um índice invertido de radicais do título conta as interseções sem comparar
    todos contra todos, e só os primeiros :data:`MAX_REPRESENTATIVES` membros de
    cada cluster são comparados. Cada cluster volta com o artigo principal na
    frente (ver :func:`primary_sort_key`).
    """
    ordered = sorted(articles, key=primary_sort_key)
    features = [_features(a) for a in ordered]
    weight = _idf_weights(features)
    for f in features:
        f.title_norm = math.sqrt(sum(weight[t] for t in f.title_stems))
        f.lead_norm = math.sqrt(sum(weight[t] for t in f.lead_stems))

    clusters: list[list[int]] = []  # índices em ``ordered``
    # principal de cada cluster por idioma (1º artigo daquele idioma, pela ordenação):
    # títulos em idiomas diferentes naturalmente se parecem menos.
    primaries: list[dict[str, _Features]] = []
    representatives: list[int] = []  # membros comparáveis por cluster
    sets = _UnionFind()
    member_cluster: list[int] = []  # membro → cluster (id original; resolver com find)
    member_features: list[_Features] = []
    postings: dict[str, list[int]] = defaultdict(list)  # radical do título → membros

    for index, feat in enumerate(features):
        overlap: Counter[int] = Counter()
        for stem in feat.title_stems:
            overlap.update(postings.get(stem, ()))

        matches: dict[int, float] = {}  # raiz do cluster → melhor score
        for member, shared in overlap.items():
            other = member_features[member]
            root = sets.find(member_cluster[member])
            score: float | None = _classic_score(feat, other, threshold)
            if score < threshold:
                score = _content_score(feat, other, shared, weight)
                primary = _primary_for(primaries[root], feat)
                if score is not None and _title_cosine(feat, primary, weight) < PRIMARY_TITLE_MIN:
                    score = None  # parecido com um membro, mas não com o fato do cluster
            if score is not None and score > matches.get(root, -1.0):
                matches[root] = score

        if matches:
            # maior score; empate → cluster mais antigo
            target = max(matches, key=lambda root: (matches[root], -root))
            clusters[target].append(index)
            for root, score in sorted(matches.items()):
                target = sets.find(target)
                if score < MERGE_LINK_MIN or sets.find(root) == target:
                    continue
                if len(clusters[target]) + len(clusters[root]) > MERGE_MAX_SIZE:
                    continue
                keep, gone = min(target, root), max(target, root)
                sets.parent[gone] = keep
                for lang, primary in primaries[gone].items():
                    primaries[keep].setdefault(lang, primary)
                clusters[keep].extend(clusters[gone])
                clusters[gone] = []
                representatives[keep] += representatives[gone]
                target = keep
            target = sets.find(target)
            primaries[target].setdefault(feat.lang, feat)
        else:
            target = sets.add()
            clusters.append([index])
            primaries.append({feat.lang: feat})
            representatives.append(0)
        if representatives[target] < MAX_REPRESENTATIVES:
            representatives[target] += 1
            member_cluster.append(target)
            member_features.append(feat)
            for stem in feat.title_stems:
                postings[stem].append(len(member_features) - 1)

    groups = [sorted(group) for group in clusters if group]
    groups = _merge_cross_language(groups, ordered)
    return [order_primary_first([ordered[i] for i in group]) for group in groups]


_PROPER_TOKEN = re.compile(r"[A-Za-zÀ-ÿ0-9][\w'’.-]*")


def _anchor_stems(value: str) -> set[str]:
    """Radicais de nomes próprios, siglas e números de um texto (palavras com maiúscula
    fora do início ou com dígito): "OpenAI", "RAF", "Fairford", "150"."""
    out: set[str] = set()
    for position, word in enumerate(_PROPER_TOKEN.findall(value or "")):
        if any(ch.isdigit() for ch in word):
            out |= {f"#{n.replace(',', '.')}" for n in _NUMBER_RE.findall(word)}
        elif word[0].isupper() and (position > 0 or sum(ch.isupper() for ch in word) > 1):
            out |= _stems(word)
    return out


def _merge_cross_language(groups: list[list[int]], ordered: list[Article]) -> list[list[int]]:
    """Une clusters de idiomas diferentes sobre o mesmo fato (pt × en).

    Exige, entre os dois clusters: :data:`CROSS_CLUSTER_TITLE_SHARED` radicais
    em comum nos títulos, :data:`CROSS_CLUSTER_RARE_SHARED` radicais raros
    (df ≤ :data:`CROSS_CLUSTER_RARE_DF` clusters) em comum no título + começo do
    resumo, e ao menos um desses raros sendo nome próprio, sigla ou número nos
    dois lados e aparecendo num título ("Fairford", "Astra"). Verbos e
    substantivos genéricos em comum ("bid", "rejects") não bastam.
    """
    if len(groups) < 2:
        return groups
    title_sets: list[set[str]] = []
    lead_sets: list[set[str]] = []
    anchors: list[set[str]] = []
    lowers: list[set[str]] = []  # radicais em minúscula nos títulos (palavras comuns, não nomes)
    langs: list[str] = []
    for group in groups:
        members = [ordered[i] for i in group[:MAX_REPRESENTATIVES]]
        titles: set[str] = set()
        leads: set[str] = set()
        anchor: set[str] = set()
        lower: set[str] = set()
        for article in members:
            title_stems = _stems(article.title) | _numbers(article.title)
            lead = (article.summary or "")[:LEAD_CHARS]
            titles |= title_stems
            leads |= title_stems | _stems(lead) | _numbers(lead)
            anchor |= _anchor_stems(article.title) | _anchor_stems(lead)
            lower |= _stems(" ".join(w for w in (article.title or "").split() if w[:1].islower()))
        title_sets.append(titles)
        lead_sets.append(leads)
        anchors.append(anchor)
        lowers.append(lower)
        langs.append(Counter(a.lang for a in members).most_common(1)[0][0])
    df = Counter(stem for leads in lead_sets for stem in leads)

    index: dict[str, list[int]] = defaultdict(list)  # radical raro → clusters (listas curtas)
    for position, leads in enumerate(lead_sets):
        for stem in leads:
            if df[stem] <= CROSS_CLUSTER_RARE_DF:
                index[stem].append(position)

    sets = _UnionFind()
    for _ in groups:
        sets.add()
    size = [len(group) for group in groups]
    for i in range(len(groups)):
        candidates: Counter[int] = Counter()
        for stem in lead_sets[i]:
            for j in index.get(stem, ()):
                if j > i and langs[j] != langs[i]:
                    candidates[j] += 1
        for j, count in sorted(candidates.items()):
            if count < CROSS_CLUSTER_RARE_SHARED:
                continue
            shared_titles = title_sets[i] & title_sets[j]
            if len(shared_titles) < CROSS_CLUSTER_TITLE_SHARED:
                continue
            # algo em comum além de nomes próprios ("Jensen Huang" + "Nvidia" em fatos diferentes)
            if not any(s.startswith("#") or s in lowers[i] or s in lowers[j] for s in shared_titles):
                continue
            rare = {s for s in lead_sets[i] & lead_sets[j] if df[s] <= CROSS_CLUSTER_RARE_DF}
            if len(rare) < CROSS_CLUSTER_RARE_SHARED:
                continue
            anchored = {s for s in rare & anchors[i] & anchors[j] if s in title_sets[i] | title_sets[j]}
            if not anchored:
                continue
            a, b = sets.find(i), sets.find(j)
            if a == b or size[a] + size[b] > MERGE_MAX_SIZE:
                continue
            keep, gone = min(a, b), max(a, b)
            sets.parent[gone] = keep
            size[keep] += size[gone]
            log.debug("clusters unidos entre idiomas por %s", sorted(anchored))

    merged: dict[int, list[int]] = {}
    for position, group in enumerate(groups):
        merged.setdefault(sets.find(position), []).extend(group)
    return [sorted(group) for _, group in sorted(merged.items())]


# ── classificação ──────────────────────────────────────────────────────────


def _keyword_regex(keyword: str) -> str | None:
    """Trecho de regex de uma palavra-chave sobre texto já normalizado.

    - com espaço: frase (as palavras em sequência);
    - até 3 letras: só palavra inteira ("ia", "pt", "fed", "b3", "s&p");
    - terminada em ``=``: só palavra inteira ("real=" não casa com "realiza");
    - demais: início de palavra, tolerando até 3 letras extras
      ("imobiliari" → "imobiliarios"; "bolsa" não casa com "bolsonaro").
    """
    exact = keyword.rstrip().endswith("=")
    norm = text.normalize(keyword.rstrip().rstrip("="))
    if not norm:
        return None
    body = re.escape(norm)
    if exact or len(norm.replace(" ", "")) <= SHORT_KEYWORD_MAX:
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


def classify(
    texts: str,
    topics: list[str],
    sections: list[SectionConfig],
    topic_weights: dict[str, float] | None = None,
) -> str:
    """Escolhe a seção de um texto (título + resumo).

    Pontua cada seção pelo número de ocorrências de suas palavras-chave em
    ``text.normalize(texts)`` e soma :data:`TOPIC_BONUS` às seções indicadas em
    ``topics`` (dicas do feed) — multiplicado pelo peso da dica em
    ``topic_weights`` quando informado (fração dos artigos do cluster com a
    dica). Empate → a primeira dica de ``topics`` entre as empatadas → a
    primeira seção (ordem da configuração).
    """
    if not sections:
        raise ValueError("nenhuma seção configurada")
    normalized = text.normalize(texts)
    weights = topic_weights if topic_weights is not None else {topic: 1.0 for topic in topics}
    scores = [
        _keyword_hits(section.keywords, normalized) + TOPIC_BONUS * weights.get(section.id, 0.0)
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

    Soma o peso das fontes distintas (o primeiro artigo extra de uma mesma fonte
    vale :data:`REPEATED_SOURCE_FACTOR` do peso; os seguintes, nada) e
    multiplica pelo fator de idade do artigo mais recente, pelo bônus de imagem
    e de resumo longo; penaliza títulos curtíssimos, coberturas "ao vivo" e
    conteúdo de serviço/opinião (:data:`SERVICE_RE`).
    """
    if not articles:
        return 0.0
    now = as_utc(now)

    base = 0.0
    per_source: Counter[str] = Counter()
    for article in sorted(articles, key=lambda a: (-a.weight, a.id)):
        per_source[article.source_id] += 1
        seen = per_source[article.source_id]
        if seen == 1:
            base += article.weight
        elif seen - 1 <= MAX_REPEATS_PER_SOURCE:
            base += REPEATED_SOURCE_FACTOR * article.weight

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
    if is_service_title(title):
        score *= SERVICE_PENALTY
    return round(score, 6)


def _cluster_topics(articles: list[Article]) -> list[str]:
    """Dicas de seção do cluster, sem repetição, começando pelas do principal."""
    topics: list[str] = []
    for article in articles:
        for topic in article.topics:
            if topic not in topics:
                topics.append(topic)
    return topics


def _topic_weights(articles: list[Article]) -> dict[str, float]:
    """Peso de cada dica de seção: fração dos artigos do cluster que a trazem.

    Um cluster grande de fontes variadas não recebe bônus cheio em todas as
    seções (antes, a união das dicas de todos os membros decidia por ruído).
    """
    counts = Counter(topic for article in articles for topic in set(article.topics))
    total = max(len(articles), 1)
    return {topic: count / total for topic, count in counts.items()}


def editorial_score(cluster: Cluster) -> float:
    """Score usado pela edição automática para escolher e ordenar matérias.

    ``score`` × peso da seção (:data:`SECTION_WEIGHT`) × fator de idioma
    (0,5 + 0,5 × fração de artigos em português). Não altera ``Cluster.score``,
    que também ordena os candidatos da edição por IA.
    """
    articles = cluster.articles or []
    pt_share = sum(1 for a in articles if a.lang == "pt") / max(len(articles), 1)
    return cluster.score * SECTION_WEIGHT.get(cluster.section, 1.0) * (0.5 + 0.5 * pt_share)


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
                section=classify(classify_text, _cluster_topics(group), config.sections, _topic_weights(group)),
            )
        )
    clusters.sort(key=lambda c: (-c.score, c.key))
    log.debug("%d artigos agrupados em %d clusters", len(articles), len(clusters))
    return clusters
