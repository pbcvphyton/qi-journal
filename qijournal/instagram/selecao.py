"""Escolha das matérias de cada publicação a partir da edição do dia."""

from __future__ import annotations

import math
from typing import Iterable

from qijournal.models import Coverage, Edition, Story


def ordered_stories(edition: Edition) -> list[Story]:
    """Matérias na ordem editorial: manchete, chamadas, destaques e o resto por importância."""
    by_id = {s.id: s for s in edition.stories}
    order: list[str] = []
    for sid in [edition.lead, *edition.secondary, *edition.highlights]:
        if sid in by_id and sid not in order:
            order.append(sid)
    rest = sorted((s for s in edition.stories if s.id not in order), key=lambda s: -s.importance)
    return [by_id[sid] for sid in order] + rest


def diverse_stories(edition: Edition, n: int, *, exclude: Iterable[str] = ()) -> list[Story]:
    """Até ``n`` matérias na ordem editorial, no máximo uma por editoria enquanto der."""
    excluded = set(exclude)
    pool = [s for s in ordered_stories(edition) if s.id not in excluded]
    chosen: list[Story] = []
    sections: set[str] = set()
    for story in pool:
        if len(chosen) == n:
            break
        if story.section not in sections:
            chosen.append(story)
            sections.add(story.section)
    for story in pool:  # menos editorias que vagas: completa na ordem editorial
        if len(chosen) == n:
            break
        if story not in chosen:
            chosen.append(story)
    order = {s.id: i for i, s in enumerate(pool)}
    return sorted(chosen, key=lambda s: order[s.id])


def divergence_score(coverage: Coverage | None) -> float:
    """Quanto a cobertura se dividiu: 0 sem debate; maior com lados equilibrados, poucos
    neutros e mais veículos."""
    if coverage is None or not coverage.has_debate:
        return 0.0
    a, b = coverage.count("a"), coverage.count("b")
    if a == 0 or b == 0:
        return 0.0
    balance = min(a, b) / max(a, b)  # 1 = empate entre os lados
    engaged = (a + b) / len(coverage.outlets)  # fração que tomou um lado
    return balance * engaged * math.log2(1 + len(coverage.outlets))


def coverage_story(edition: Edition, *, exclude: Iterable[str] = (), min_outlets: int = 3) -> Story | None:
    """A matéria em que a imprensa mais se dividiu (com pelo menos ``min_outlets`` veículos)."""
    excluded = set(exclude)
    candidates = [
        s
        for s in ordered_stories(edition)
        if s.id not in excluded and s.coverage is not None and len(s.coverage.outlets) >= min_outlets
    ]
    scored = [(divergence_score(s.coverage), -i, s) for i, s in enumerate(candidates)]
    scored = [t for t in scored if t[0] > 0]
    if not scored:
        return None
    return max(scored, key=lambda t: (t[0], t[1]))[2]
