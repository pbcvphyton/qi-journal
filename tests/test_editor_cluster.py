"""Testes de agrupamento, classificação e pontuação (qijournal.edit.cluster)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from qijournal.config import SectionConfig, load_config
from qijournal.edit.cluster import (
    Cluster,
    classify,
    cluster_articles,
    order_primary_first,
    parse_iso,
    rank_clusters,
    score_cluster,
)
from tests.fixtures.editor.factory import NOW, make_article, sample_articles


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


def _groups_by_id(groups):
    return [[a.id for a in g] for g in groups]


# ── cluster_articles ───────────────────────────────────────────────────────


def test_same_fact_from_several_sources_is_grouped_with_best_primary_first():
    groups = _groups_by_id(cluster_articles(sample_articles()))
    copom = next(g for g in groups if "copom-valor" in g)
    # pt + maior peso + imagem → Valor na frente; os outros 3 veículos juntos
    assert copom[0] == "copom-valor"
    assert sorted(copom) == sorted(["copom-valor", "copom-folha", "copom-g1", "copom-estadao"])
    assert ["stf-jota", "stf-conjur"] in groups
    assert ["fed-ft", "fed-wsj"] in groups


def test_distinct_facts_stay_separate_and_every_article_appears_once():
    articles = sample_articles()
    groups = cluster_articles(articles)
    flat = [a.id for g in groups for a in g]
    assert sorted(flat) == sorted(a.id for a in articles)
    fiscal = next(g for g in _groups_by_id(groups) if "fiscal-valor" in g)
    assert fiscal == ["fiscal-valor"]


def test_summary_bonus_joins_borderline_titles():
    # títulos com Jaccard 3/8 = 0,375 (abaixo de 0,42); resumos parecidos → bônus (+0,15) junta
    same_summary = "A Petrobras anunciou corte de 5% no preço da gasolina vendida às distribuidoras."
    a = make_article("a", "Petrobras reduz preço da gasolina nas refinarias", same_summary)
    b = make_article("b", "Petrobras reduz gasolina e diesel a partir de amanhã", same_summary, source_id="folha")
    c = make_article(
        "c",
        "Petrobras reduz gasolina e diesel a partir de amanhã",
        "Motoristas devem sentir o efeito nas bombas só na semana que vem, dizem postos.",
        source_id="g1",
    )
    assert len(cluster_articles([a, b])) == 1
    assert len(cluster_articles([a, c])) == 2


def test_threshold_controls_grouping():
    a = make_article("a", "Copom mantém Selic em 15% ao ano")
    b = make_article("b", "Copom mantém Selic em 15% e sinaliza cautela", source_id="folha")
    assert len(cluster_articles([a, b], threshold=0.42)) == 1
    assert len(cluster_articles([a, b], threshold=0.95)) == 2


def test_different_languages_rarely_match():
    pt = make_article("pt", "Fed corta juros em 0,25 ponto percentual", source_id="valor")
    en = make_article("en", "Fed cuts interest rates by a quarter point", source_id="ft", lang="en")
    assert len(cluster_articles([pt, en])) == 2


def test_large_cluster_keeps_absorbing_beyond_representatives():
    # 12 veículos com o mesmo título: além dos 8 representantes, os demais ainda entram
    sources = [
        "valor",
        "folha",
        "estadao",
        "g1",
        "infomoney",
        "moneytimes",
        "ft",
        "wsj",
        "nyt",
        "guardian",
        "jota",
        "cnbc",
    ]
    articles = [
        make_article(f"a{i}", "Petrobras anuncia redução do preço do diesel nas refinarias", source_id=src)
        for i, src in enumerate(sources)
    ]
    articles.append(make_article("outro", "Vale anuncia recompra de ações", source_id="valor"))
    groups = cluster_articles(articles)
    assert sorted(len(g) for g in groups) == [1, 12]


def test_empty_input_and_articles_without_tokens():
    assert cluster_articles([]) == []
    a = make_article("a", "!!!")
    b = make_article("b", "???", source_id="folha")
    assert len(cluster_articles([a, b])) == 2


def test_primary_preference_order():
    en_heavy = make_article("en", "Title", lang="en", weight=1.5, image="https://x/i.jpg")
    pt_light = make_article("pt1", "Título", weight=0.8)
    pt_heavy_noimg = make_article("pt2", "Título", weight=1.2)
    pt_heavy_img = make_article("pt3", "Título", weight=1.2, image="https://x/j.jpg")
    ordered = order_primary_first([en_heavy, pt_light, pt_heavy_noimg, pt_heavy_img])
    assert [a.id for a in ordered] == ["pt3", "pt2", "pt1", "en"]


# ── classify ───────────────────────────────────────────────────────────────


def _sections(**keywords):
    return [SectionConfig(id=k, title=k, description="", keywords=v) for k, v in keywords.items()]


def test_classify_counts_keyword_occurrences(config):
    assert classify("Copom mantém a Selic; inflação segue alta", [], config.sections) == "brasil"
    assert classify("STF julga ação sobre ICMS; ministro pede vista", [], config.sections) == "juridico"
    assert classify("Nvidia lança chip de inteligência artificial", [], config.sections) == "tecnologia"
    assert classify("Fundos imobiliários de galpões lideram alta do IFIX", [], config.sections) == "imobiliario"


def test_topic_hint_adds_strong_bonus(config):
    # sem dica, "bolsa" puxa para mercados; a dica do feed (+3) vence um casamento isolado
    assert classify("Bolsa sobe", [], config.sections) == "mercados"
    assert classify("Bolsa sobe", ["politica"], config.sections) == "politica"


def test_short_keywords_only_match_whole_words():
    sections = _sections(tec=["ia"], outra=["xyz"])
    assert classify("Empresa aposta em IA generativa", [], sections) == "tec"
    # "ia" dentro de "Tecnologia"/"media" não conta: empate em zero → primeira seção
    assert classify("Tecnologia de media", [], list(reversed(sections))) == "outra"


def test_long_keywords_tolerate_short_suffixes_but_not_other_words():
    sections = _sections(imob=["imobiliari"], merc=["bolsa"], nada=["zzzz"])
    assert classify("Crédito imobiliário cresce", [], sections) == "imob"
    assert classify("Lançamentos imobiliarios", [], sections) == "imob"
    assert classify("Bolsas asiáticas caem", [], sections) == "merc"
    # "bolsa" não casa com "Bolsonaro" (sufixo longo demais) → empate → primeira seção
    assert classify("Bolsonaro discursa", [], list(reversed(sections))) == "nada"


def test_phrase_keywords_match_as_sequence():
    sections = _sections(a=["banco central"], b=["central"])
    assert classify("O Banco Central decidiu", [], sections) == "a"  # empate 1×1 → primeira seção
    assert classify("Central de atendimento", [], sections) == "b"
    assert classify("banco de dados central", [], sections) == "b"


def test_classify_ties_prefer_first_topic_then_first_section():
    sections = _sections(a=["alfa"], b=["beta"], c=["gama"])
    assert classify("alfa beta", [], sections) == "a"
    assert classify("alfa beta", ["c", "b"], sections) == "b"  # b e c ganham +3; b tem +1
    assert classify("nada aqui", ["c", "b"], sections) == "c"  # empate entre dicas → 1ª dica
    assert classify("nada aqui", [], sections) == "a"


def test_classify_requires_sections():
    with pytest.raises(ValueError):
        classify("texto", [], [])


# ── score_cluster ──────────────────────────────────────────────────────────


def _art(
    aid,
    *,
    source="valor",
    weight=1.0,
    hours=1.0,
    image=None,
    summary="",
    title="Título suficientemente longo para não ser penalizado",
):
    return make_article(aid, title, summary, source_id=source, weight=weight, hours_ago=hours, image=image)


def test_score_sums_distinct_sources_and_discounts_repeats():
    one = score_cluster([_art("a", weight=1.0)], now=NOW, max_age_hours=30)
    two = score_cluster([_art("a", weight=1.0), _art("b", source="folha", weight=1.2)], now=NOW, max_age_hours=30)
    same = score_cluster([_art("a", weight=1.0), _art("b", source="valor", weight=1.0)], now=NOW, max_age_hours=30)
    assert one == pytest.approx(1.0)
    assert two == pytest.approx(2.2)
    assert same == pytest.approx(1.3)


def test_score_age_decay():
    def at(hours):
        return score_cluster([_art("a", hours=hours)], now=NOW, max_age_hours=30)

    assert at(0.5) == pytest.approx(1.0)
    assert at(6) == pytest.approx(1.0)
    assert at(18) == pytest.approx(1.0 - 0.5 * 0.65)  # metade do caminho entre 6h e 30h
    assert at(30) == pytest.approx(0.35)
    assert at(100) == pytest.approx(0.35)
    assert at(-3) == pytest.approx(1.0)  # data no futuro não passa de 1,0
    assert at(None) == pytest.approx(0.5)  # sem data


def test_score_uses_freshest_article_of_the_cluster():
    old = _art("a", hours=30)
    fresh = _art("b", source="folha", hours=1)
    assert score_cluster([old, fresh], now=NOW, max_age_hours=30) == pytest.approx(2.0)


def test_score_bonuses_and_penalties():
    base = score_cluster([_art("a")], now=NOW, max_age_hours=30)
    with_image = score_cluster([_art("a", image="https://x/i.jpg")], now=NOW, max_age_hours=30)
    long_summary = score_cluster([_art("a", summary="x" * 250)], now=NOW, max_age_hours=30)
    short_title = score_cluster([_art("a", title="Dólar sobe")], now=NOW, max_age_hours=30)
    live = score_cluster([_art("a", title="Ao vivo: acompanhe a votação no Senado hoje")], now=NOW, max_age_hours=30)
    live_en = score_cluster(
        [_art("a", title="Live updates: stocks rally after Fed decision")], now=NOW, max_age_hours=30
    )
    assert with_image == pytest.approx(base * 1.15)
    assert long_summary == pytest.approx(base * 1.05)
    assert short_title < base
    assert live == pytest.approx(base * 0.5)
    assert live_en == pytest.approx(base * 0.5)
    assert score_cluster([], now=NOW, max_age_hours=30) == 0.0


# ── rank_clusters ──────────────────────────────────────────────────────────


def test_rank_clusters_orders_by_score_and_classifies(config):
    clusters = rank_clusters(sample_articles(), config, now=NOW)
    scores = [c.score for c in clusters]
    assert scores == sorted(scores, reverse=True)
    top = clusters[0]
    assert isinstance(top, Cluster)
    assert top.primary.id == "copom-valor" and top.key == "copom-valor"
    assert top.section == "brasil"
    by_key = {c.key: c for c in clusters}
    assert by_key["stf-jota"].section == "juridico"
    assert by_key["fii-infomoney"].section == "imobiliario"
    assert by_key["semdata-guardian"].section == "mundo"
    assert {c.section for c in clusters} == set(config.section_ids)
    # "ao vivo" e títulos curtíssimos ficam para trás
    ranks = [c.key for c in clusters]
    assert ranks.index("aovivo-g1") > ranks.index("camara-poder")
    assert ranks.index("curto-g1") > ranks.index("ipo-bj")


def test_rank_clusters_accepts_naive_now(config):
    naive = NOW.replace(tzinfo=None)
    assert [c.key for c in rank_clusters(sample_articles(), config, now=naive)] == [
        c.key for c in rank_clusters(sample_articles(), config, now=NOW)
    ]


def test_parse_iso():
    assert parse_iso("2026-09-29T08:07:00+00:00") == NOW
    assert parse_iso("2026-09-29T08:07:00Z") == NOW
    assert parse_iso("2026-09-29T08:07:00") == NOW  # sem fuso → UTC
    assert parse_iso((NOW - timedelta(hours=3)).isoformat()) == NOW - timedelta(hours=3)
    assert parse_iso(None) is None
    assert parse_iso("ontem") is None


# ── regras de conteúdo no bundle compartilhado ─────────────────────────────


def _bundle_groups():
    import json

    from qijournal.config import ROOT
    from qijournal.models import Bundle
    from qijournal.text import normalize

    bundle = Bundle.from_dict(json.loads((ROOT / "tests" / "fixtures" / "bundle.json").read_text(encoding="utf-8")))
    groups = cluster_articles(bundle.articles)

    def group_of(*words):
        found = [g for g in groups for a in g if all(w in normalize(a.title) for w in words)]
        assert found, words
        return found[0]

    return group_of


def test_same_fact_told_with_different_words_is_grouped():
    group_of = _bundle_groups()
    # mesmo idioma, títulos com poucas palavras em comum (radicais + resumo parecido)
    assert group_of("agu processa bets") is group_of("uniao entra com acoes contra bets")
    assert group_of("governo edita decreto") is group_of("governo publica decreto") is group_of("governo cria comite")
    assert group_of("fundos imobiliarios avancam") is group_of("fiis de shoppings")
    # outro idioma: nomes/cognatos em comum (openai, astra, model; apple, patent)
    openai = group_of("openai cancela estreia")
    assert openai is group_of("openai says it will not release") and len(openai) == 7
    assert openai[0].lang == "pt"  # principal em português
    assert group_of("apple e condenada") is group_of("apple ordered to pay")


def test_related_but_distinct_facts_stay_apart():
    group_of = _bundle_groups()
    assert group_of("decisao sobre posts") is not group_of("lula diz que")  # STF x discurso de Lula
    assert group_of("governo edita decreto") is not group_of("uniao entra com acoes")  # decreto x ação da AGU
    assert group_of("banestes") is not group_of("governo edita decreto")
    assert group_of("nvidia adds") is not group_of("nvidia releases software")  # recompra x software
    assert group_of("mortgage rates") is not group_of("treasury yields climb")
