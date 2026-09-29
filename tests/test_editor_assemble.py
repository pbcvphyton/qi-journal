"""Testes da montagem da edição (qijournal.edit.assemble)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qijournal.config import load_config
from qijournal.edit.assemble import (
    assemble_edition,
    edition_stats,
    plain_text,
    top_headlines,
    unique_story_id,
)
from qijournal.models import EditionStats, SourceRef, Story
from tests.fixtures.editor.factory import NOW, sample_bundle


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


def story(sid, section="brasil", importance=3, *, image=None, published=None, headline=None, dek="Linha fina."):
    return Story(
        id=sid,
        section=section,
        headline=headline if headline is not None else f"Título {sid}",
        dek=dek,
        body=["Parágrafo."],
        sources=[SourceRef(name="Valor Econômico", url=f"https://valor.example.com/{sid}")],
        article_ids=[sid],
        importance=importance,
        image=image,
        published=published,
    )


def assemble(stories, config, **overrides):
    kwargs = dict(
        bundle=sample_bundle(),
        config=config,
        now=NOW,
        mode="heuristic",
        model=None,
        editorial="",
        briefing=[],
        stats=EditionStats(),
    )
    kwargs.update(overrides)
    return assemble_edition(stories, **kwargs)


# ── unique_story_id / plain_text / stats ──────────────────────────────────


def test_unique_story_id_adds_suffixes_and_registers_id():
    taken: set[str] = set()
    assert unique_story_id("Copom mantém Selic em 15%", taken) == "copom-mantem-selic-em-15"
    assert unique_story_id("Copom mantém Selic em 15%!", taken) == "copom-mantem-selic-em-15-2"
    assert unique_story_id("Copom mantém Selic em 15%?", taken) == "copom-mantem-selic-em-15-3"
    assert taken == {"copom-mantem-selic-em-15", "copom-mantem-selic-em-15-2", "copom-mantem-selic-em-15-3"}


def test_unique_story_id_limits_slug_length_and_handles_empty_headline():
    long_id = unique_story_id("Governo anuncia pacote bilionário de medidas para conter a alta dos preços", set())
    assert len(long_id) <= 48 and not long_id.endswith("-")
    taken: set[str] = set()
    assert unique_story_id("***", taken) == "materia"
    assert unique_story_id("", taken) == "materia-2"


def test_plain_text_removes_markup_and_forbidden_characters():
    assert plain_text("**Copom** mantém _Selic_ em [15%](https://x)") == "Copom mantém _Selic_ em 15%"
    assert plain_text("## Título") == "Título"
    assert plain_text("<script>alert(1)</script> Banco Central anuncia regra para o #Pix") == (
        "Banco Central anuncia regra para o Pix"
    )
    assert plain_text("<b>Juros</b> &amp; câmbio") == "Juros & câmbio"
    assert plain_text("Juros > 15% e inflação < 4%") == "Juros 15% e inflação 4%"
    out = plain_text("palavra " * 40, 60)
    assert len(out) <= 61 and out.endswith("…")
    assert plain_text(None) == ""


def test_edition_stats_counts_sources():
    stats = edition_stats(sample_bundle(), articles_considered=12, llm_input_tokens=10, llm_output_tokens=5)
    assert stats.sources_total == 4
    assert stats.sources_ok == 3
    assert stats.sources_failed == [{"source_id": "bbc", "url": "https://bbc.example.com/rss", "error": "HTTP 403"}]
    assert stats.articles_collected == len(sample_bundle().articles)
    assert stats.articles_considered == 12
    assert (stats.llm_input_tokens, stats.llm_output_tokens) == (10, 5)


# ── assemble_edition ───────────────────────────────────────────────────────


def test_dates_are_local_to_site_timezone(config):
    # 02:30 UTC ainda é dia 28 em São Paulo (UTC-3)
    now = datetime(2026, 9, 29, 2, 30, tzinfo=UTC)
    edition = assemble([story("a")], config, now=now)
    assert edition.date == "2026-09-28"
    assert edition.date_label == "Segunda-feira, 28 de setembro de 2026"
    assert edition.generated_at == "2026-09-29T02:30:00+00:00"
    edition = assemble([story("a")], config)
    assert edition.date == "2026-09-29"
    assert edition.date_label == "Terça-feira, 29 de setembro de 2026"


def test_sections_follow_config_order_and_skip_empty(config):
    stories = [
        story("m1", "mercados", 3, published="2026-09-29T05:00:00+00:00"),
        story("b1", "brasil", 2),
        story("m2", "mercados", 5),
        story("m3", "mercados", 3, published="2026-09-29T07:00:00+00:00"),
        story("i1", "imobiliario", 1),
    ]
    edition = assemble(stories, config)
    assert [s.id for s in edition.sections] == ["brasil", "mercados", "imobiliario"]
    mercados = edition.sections[1]
    # importância desc, depois publicação mais recente primeiro
    assert mercados.story_ids == ["m2", "m3", "m1"]
    assert mercados.title == config.section("mercados").title
    assert mercados.color == config.section("mercados").color
    placed = sorted(sid for s in edition.sections for sid in s.story_ids)
    assert placed == sorted(s.id for s in stories)


def test_lead_uses_given_id_or_most_important_with_image(config):
    stories = [story("a", importance=5), story("b", importance=5, image="https://img/b.jpg"), story("c", importance=3)]
    assert assemble(stories, config, lead_id="c").lead == "c"
    assert assemble(stories, config).lead == "b"  # empate em importância → com imagem
    assert assemble(stories, config, lead_id="nao-existe").lead == "b"


def test_secondary_prefers_other_sections_and_highlights_follow(config):
    stories = [
        story("lead", "brasil", 5),
        story("b2", "brasil", 5),
        story("b3", "brasil", 4),
        story("m1", "mercados", 4),
        story("j1", "juridico", 3),
        story("w1", "mundo", 3),
        story("t1", "tecnologia", 2),
        story("p1", "politica", 1),
    ]
    edition = assemble(stories, config, lead_id="lead")
    assert edition.secondary == ["m1", "j1", "w1"]  # seções diferentes da manchete e entre si
    assert edition.highlights == ["b2", "b3", "t1", "p1"]  # restante por importância
    assert edition.lead not in edition.secondary + edition.highlights


def test_secondary_falls_back_to_same_section_and_highlights_are_capped(config):
    stories = [story(f"s{i}", "brasil", 5 - i % 5) for i in range(15)]
    edition = assemble(stories, config)
    assert len(edition.secondary) == config.edition.secondary_count
    assert len(edition.highlights) == config.edition.highlights_count
    ids = [edition.lead, *edition.secondary, *edition.highlights]
    assert len(ids) == len(set(ids))


def test_quotes_weather_stats_and_metadata_are_copied(config):
    bundle = sample_bundle()
    stats = EditionStats(sources_total=4)
    edition = assemble(
        [story("a")],
        config,
        bundle=bundle,
        stats=stats,
        mode="ai",
        model="claude-opus-5-5",
        editorial="Dia de **juros**.",
        briefing=["Um", "Dois"],
    )
    assert edition.quotes == bundle.quotes and edition.quotes is not bundle.quotes
    assert edition.weather == bundle.weather
    assert edition.stats is stats
    assert (edition.mode, edition.model) == ("ai", "claude-opus-5-5")
    assert edition.editorial == "Dia de **juros**." and edition.briefing == ["Um", "Dois"]
    assert top_headlines(edition, 5) == ["Título a"]


@pytest.mark.parametrize(
    "stories, message",
    [
        ([], "pelo menos uma"),
        ([story("a"), story("a")], "repetido"),
        ([story("a", headline="  ")], "sem título"),
        ([story("a", headline="**Copom** decide")], "marcação proibida"),
        ([story("a", headline="Título <b>")], "marcação proibida"),
        ([story("a", dek="# Linha fina")], "marcação proibida"),
        ([story("a", section="esportes")], "seção desconhecida"),
        ([story("a", importance=9)], "importância"),
        ([story("")], "sem id"),
    ],
)
def test_validation_errors(config, stories, message):
    with pytest.raises(ValueError, match=message):
        assemble(stories, config)


def test_roundtrip_to_dict(config):
    from qijournal.models import Edition

    edition = assemble([story("a", image="https://img/a.jpg"), story("b", "mundo")], config)
    assert Edition.from_dict(edition.to_dict()).to_dict() == edition.to_dict()
