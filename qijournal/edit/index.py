"""Lista completa do dia ("Todas as notícias"): nenhuma notícia coletada fica de fora.

Cada notícia aparece uma única vez, dentro de um assunto:

1. as matérias da edição (os artigos que a IA ou a edição automática juntaram);
2. os assuntos com cobertura comparada (os artigos que a IA uniu);
3. todas as demais, agrupadas por títulos parecidos (:func:`rank_clusters`).

Os assuntos ficam na ordem das seções da configuração e, dentro de cada seção,
matérias, assuntos comparados e depois os demais pela pontuação editorial.
"""

from __future__ import annotations

from datetime import datetime

from qijournal.config import Config
from qijournal.edit.assemble import plain_text
from qijournal.edit.cluster import as_utc, order_primary_first, rank_clusters
from qijournal.models import Article, Bundle, Edition, IndexItem, IndexTopic, Rationale

TITLE_CHARS = 180


def _items(articles: list[Article]) -> list[IndexItem]:
    return [
        IndexItem(
            source=a.source_name,
            title=plain_text(a.title, TITLE_CHARS),
            url=a.url,
            published=a.published,
            lang=a.lang,
        )
        for a in order_primary_first(articles)
    ]


def build_index(bundle: Bundle, config: Config, edition: Edition, *, now: datetime) -> list[IndexTopic]:
    """Todas as notícias do ``bundle`` por assunto (ver o módulo)."""
    by_id = {a.id: a for a in bundle.articles}
    used: set[str] = set()
    topics: list[tuple[int, int, float, IndexTopic]] = []  # (seção, tipo, -pontuação, assunto)
    section_rank = {section: i for i, section in enumerate(config.section_ids)}

    def take(ids: list[str]) -> list[Article]:
        articles = [by_id[i] for i in dict.fromkeys(ids) if i in by_id and i not in used]
        used.update(a.id for a in articles)
        return articles

    for position, story in enumerate(edition.stories):
        articles = take(story.article_ids)
        if articles:
            topic = IndexTopic(
                section=story.section,
                title=story.headline,
                items=_items(articles),
                story_id=story.id,
                compared=story.coverage is not None,
            )
            topics.append((section_rank.get(story.section, 99), 0, float(position), topic))

    for position, coverage in enumerate(edition.compared):
        articles = take(coverage.article_ids)
        if articles:
            section = coverage.section if coverage.section in section_rank else config.section_ids[0]
            topic = IndexTopic(section=section, title=coverage.topic, items=_items(articles), compared=True)
            topics.append((section_rank.get(section, 99), 1, float(position), topic))

    rest = [a for a in bundle.articles if a.id not in used]
    for cluster in rank_clusters(rest, config, now=as_utc(now)):
        topic = IndexTopic(
            section=cluster.section,
            title=plain_text(cluster.primary.title, TITLE_CHARS),
            items=_items(cluster.articles),
        )
        topics.append((section_rank.get(cluster.section, 99), 2, -cluster.score, topic))

    topics.sort(key=lambda entry: entry[:3])
    return [topic for *_, topic in topics]


def heuristic_rationale(bundle: Bundle, index: list[IndexTopic]) -> Rationale:
    """Racional da edição automática (sem IA): os assuntos são os grupos automáticos."""
    return Rationale(
        method="automática",
        articles=len(bundle.articles),
        outlets=len({a.source_name for a in bundle.articles}),
        groups=len(index),
    )
