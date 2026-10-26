"""Escolha das matérias das publicações do Instagram."""

from __future__ import annotations

from qijournal.instagram.selecao import coverage_story, divergence_score, diverse_stories, ordered_stories
from qijournal.models import Coverage, CoverageOutlet, Edition, Story


def story(sid, section="brasil", importance=3, stances=None):
    coverage = None
    if stances is not None:
        coverage = Coverage(
            topic=sid,
            conclusion="",
            outlets=[CoverageOutlet(name=f"V{i}", stance=st) for i, st in enumerate(stances)],
            side_a="lado A" if "a" in stances else "",
            side_b="lado B" if "b" in stances else "",
        )
    return Story(id=sid, section=section, headline=sid, dek="", body=[], sources=[], article_ids=[], importance=importance, coverage=coverage)


def edition(stories, lead, secondary=(), highlights=()):
    return Edition(
        date="2026-10-20", date_label="", generated_at="", mode="ai", model=None, editorial="", briefing=[],
        lead=lead, secondary=list(secondary), highlights=list(highlights), sections=[], stories=stories, quotes=[], weather=[],
    )


def test_ordem_editorial_manchete_chamadas_destaques_e_importancia():
    e = edition([story("x", importance=2), story("y", importance=5), story("lead"), story("sec"), story("hl")], "lead", ["sec"], ["hl"])
    assert [s.id for s in ordered_stories(e)] == ["lead", "sec", "hl", "y", "x"]


def test_diversidade_uma_por_editoria_e_completa_se_faltar():
    e = edition([story("a1", "brasil"), story("a2", "brasil"), story("b1", "mundo"), story("c1", "esporte")], "a1", ["a2", "b1", "c1"])
    assert [s.id for s in diverse_stories(e, 3)] == ["a1", "b1", "c1"]
    assert [s.id for s in diverse_stories(e, 4)] == ["a1", "a2", "b1", "c1"]
    assert [s.id for s in diverse_stories(e, 2, exclude=["a1"])] == ["a2", "b1"]


def test_divergencia_prefere_lados_equilibrados_e_engajados():
    assert divergence_score(None) == 0
    assert divergence_score(story("s", stances=["neutro"] * 5).coverage) == 0
    assert divergence_score(story("s", stances=["a"] * 5).coverage) == 0
    equilibrado = divergence_score(story("s", stances=["a", "a", "b", "b"]).coverage)
    morno = divergence_score(story("s", stances=["a", "b"] + ["neutro"] * 8).coverage)
    torto = divergence_score(story("s", stances=["a", "a", "a", "b"]).coverage)
    assert equilibrado > torto > 0 and equilibrado > morno > 0


def test_materia_de_cobertura_ignora_poucos_veiculos_e_excluidas():
    e = edition(
        [story("lead", stances=["a", "b", "a", "b"]), story("dois", stances=["a", "b"]), story("ok", stances=["a", "b", "b"])],
        "lead",
    )
    assert coverage_story(e).id == "lead"
    assert coverage_story(e, exclude=["lead"]).id == "ok"
    assert coverage_story(e, exclude=["lead", "ok"]) is None
