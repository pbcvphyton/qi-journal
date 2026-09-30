"""Testes da edição automática, sem IA (qijournal.edit.heuristic)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qijournal.config import load_config
from qijournal.edit.cluster import Cluster
from qijournal.edit.heuristic import (
    MAX_PER_SECTION,
    build_heuristic_edition,
    dek_and_body,
    importance_for_rank,
    select_clusters,
    story_from_articles,
)
from qijournal.models import Bundle, Edition
from tests.fixtures.editor.factory import NOW, make_article, sample_bundle, sample_sections

FORBIDDEN = set("*#<>")
SHARED_BUNDLE = Path(__file__).parent / "fixtures" / "bundle.json"


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


@pytest.fixture(scope="module")
def edition(config):
    return build_heuristic_edition(sample_bundle(), config, now=NOW)


def assert_edition_invariants(edition: Edition, config) -> None:
    """Contratos que toda edição precisa cumprir para o renderizador."""
    ids = [s.id for s in edition.stories]
    assert len(ids) == len(set(ids))
    placed = [sid for section in edition.sections for sid in section.story_ids]
    assert sorted(placed) == sorted(ids)  # cada matéria em exatamente uma seção
    assert [s.id for s in edition.sections] == [
        sid for sid in config.section_ids if sid in {s.id for s in edition.sections}
    ]
    assert edition.lead in ids
    featured = [edition.lead, *edition.secondary, *edition.highlights]
    assert len(featured) == len(set(featured)) and set(featured) <= set(ids)
    for story in edition.stories:
        assert story.headline.strip()
        assert not FORBIDDEN & set(story.headline), story.headline
        assert not FORBIDDEN & set(story.dek), story.dek
        assert all("**" not in p for p in story.body) or edition.mode == "ai"
        assert 1 <= story.importance <= 5
        assert story.sources and story.article_ids
        names = [s.name for s in story.sources]
        assert len(names) == len(set(names))


# ── edição completa ────────────────────────────────────────────────────────


def test_heuristic_edition_contract(edition, config):
    assert_edition_invariants(edition, config)
    assert edition.mode == "heuristic" and edition.model is None
    assert edition.editorial == ""
    assert edition.date == "2026-09-29"
    assert len(edition.secondary) == config.edition.secondary_count
    assert len(edition.highlights) == config.edition.highlights_count
    # todas as seções com candidatos aparecem
    assert {s.id for s in edition.sections} == sample_sections(config)


def test_lead_is_portuguese_with_image_and_top_importance(edition):
    lead = edition.story(edition.lead)
    assert lead.headline == "Copom mantém Selic em 15% ao ano pela quinta reunião seguida"
    assert lead.image == "https://img.example.com/copom.jpg"
    assert lead.importance == 5
    # mesmo peso: a versão mais recente vem antes (Estadão 2,2 h × Folha 2,5 h)
    assert [s.name for s in lead.sources] == ["Valor Econômico", "Estadão", "Folha de S.Paulo", "g1"]
    assert sorted(lead.article_ids) == sorted(["copom-valor", "copom-folha", "copom-g1", "copom-estadao"])
    assert lead.section == "brasil"
    assert lead.published == "2026-09-29T05:07:00+00:00"


def test_briefing_brings_what_the_top_of_the_page_does_not_show(edition):
    """"Em 1 minuto" automático: a melhor matéria de cada seção fora da manchete, das
    chamadas e dos destaques (antes só repetia esses títulos)."""
    shown = {edition.lead, *edition.secondary, *edition.highlights}
    expected = []
    for section in edition.sections:
        rest = [sid for sid in section.story_ids if sid not in shown]
        if rest:
            expected.append(edition.story(rest[0]).headline)
    assert edition.briefing == expected[:5]
    assert not {edition.story(sid).headline for sid in shown} & set(edition.briefing)


def test_malicious_title_is_cleaned(edition):
    pix = next(s for s in edition.stories if "xss-estadao" in s.article_ids)
    assert pix.headline == "Banco Central anuncia nova regra para o Pix"
    assert "alert" not in pix.id


def test_english_titles_are_kept_and_stats_filled(edition):
    fed = next(s for s in edition.stories if "fed-ft" in s.article_ids)
    assert fed.headline == "Federal Reserve cuts interest rates by a quarter point"
    assert fed.why_it_matters is None
    assert edition.stats.sources_total == 4 and edition.stats.sources_ok == 3
    assert edition.stats.articles_collected == edition.stats.articles_considered == len(sample_bundle().articles)
    assert edition.stats.llm_input_tokens is None


def test_importance_distribution(edition):
    importances = [s.importance for s in edition.stories]
    assert importances.count(5) >= 3
    assert min(importances) >= 1
    assert importance_for_rank(0, 24) == importance_for_rank(2, 24) == 5
    assert importance_for_rank(3, 24) == 4
    assert importance_for_rank(10, 24) == 3
    assert importance_for_rank(18, 24) == 2
    assert importance_for_rank(23, 24) == 1


def test_edition_roundtrips_through_json(edition):
    data = json.loads(json.dumps(edition.to_dict(), ensure_ascii=False))
    assert Edition.from_dict(data).to_dict() == edition.to_dict()


def test_empty_bundle_raises(config):
    with pytest.raises(ValueError):
        build_heuristic_edition(sample_bundle(articles=[]), config, now=NOW)


def test_page_info_supplies_image_and_longer_text(config):
    article = make_article(
        "solo", "Vale anuncia recompra de ações de até R$ 5 bilhões", "Resumo curto do feed.", hours_ago=1
    )
    page_text = (
        "A Vale aprovou um programa de recompra de até R$ 5 bilhões em ações. "
        "O programa terá duração de 18 meses, segundo fato relevante. "
        "A companhia disse que a iniciativa reflete a confiança na geração de caixa."
    )
    info = SimpleNamespace(image="https://img.example.com/vale.jpg", description="Descrição.", text=page_text)
    bundle = sample_bundle(articles=[article])
    edition = build_heuristic_edition(bundle, config, now=NOW, page_info={"solo": info})
    story = edition.story(edition.lead)
    assert story.image == "https://img.example.com/vale.jpg"
    # o texto da página (mais longo que o resumo do feed) alimenta linha fina e corpo
    assert story.dek.startswith("A Vale aprovou um programa de recompra") and "18 meses" in story.dek
    assert story.body == ["A companhia disse que a iniciativa reflete a confiança na geração de caixa."]


# ── seleção com diversidade ────────────────────────────────────────────────


def _cluster(key: str, section: str, score: float) -> Cluster:
    return Cluster(articles=[make_article(key, f"Título {key} suficientemente longo")], score=score, section=section)


def test_select_clusters_caps_sections_and_reserves_one_per_section(config):
    clusters = [_cluster(f"m{i}", "mercados", 100 - i) for i in range(20)]
    clusters += [_cluster("j1", "juridico", 1.0), _cluster("i1", "imobiliario", 0.5)]
    clusters.sort(key=lambda c: -c.score)
    cfg = dataclasses.replace(config, edition=dataclasses.replace(config.edition, target_stories=10))
    chosen = select_clusters(clusters, cfg)
    assert len(chosen) == 10
    keys = [c.key for c in chosen]
    assert "j1" in keys and "i1" in keys  # a melhor de cada seção é garantida
    # teto de 6 por seção é respeitado enquanto houver outras seções; depois completa
    assert sum(c.section == "mercados" for c in chosen) == 8
    assert [c.score for c in chosen] == sorted((c.score for c in chosen), reverse=True)


def test_select_clusters_respects_cap_when_there_is_enough_variety(config):
    clusters = [_cluster(f"m{i}", "mercados", 100 - i) for i in range(10)]
    clusters += [_cluster(f"b{i}", "brasil", 50 - i) for i in range(10)]
    clusters.sort(key=lambda c: -c.score)
    cfg = dataclasses.replace(config, edition=dataclasses.replace(config.edition, target_stories=12))
    chosen = select_clusters(clusters, cfg)
    assert sum(c.section == "mercados" for c in chosen) == MAX_PER_SECTION
    assert sum(c.section == "brasil" for c in chosen) == MAX_PER_SECTION


# ── texto de uma matéria ───────────────────────────────────────────────────


def test_dek_skips_sentence_that_repeats_title():
    headline = "Copom mantém Selic em 15%"
    dek, body = dek_and_body(headline, "Copom mantém Selic em 15%. A decisão foi unânime. O mercado esperava.")
    assert dek == "A decisão foi unânime."
    assert body == ["O mercado esperava."]


def test_short_sentence_that_merely_starts_like_the_title_is_kept():
    dek, _ = dek_and_body("Copom mantém Selic em 15% ao ano", "Copom. A decisão foi unânime. O mercado esperava.")
    assert dek.startswith("Copom.")


def test_real_page_info_class_is_supported(config):
    enrich = pytest.importorskip("qijournal.collect.enrich")
    article = make_article("solo", "Título da matéria única de teste", "", hours_ago=1)
    info = enrich.PageInfo(image="https://img.example.com/p.jpg", description="Descrição da página.", text=None)
    edition = build_heuristic_edition(sample_bundle(articles=[article]), config, now=NOW, page_info={"solo": info})
    story = edition.story(edition.lead)
    assert story.image == "https://img.example.com/p.jpg"
    assert story.dek == "Descrição da página."


def test_dek_reserves_a_sentence_for_the_body_and_single_sentence_fills_both():
    dek, body = dek_and_body("Título", "Primeira frase curta. Segunda frase curta.")
    assert (dek, body) == ("Primeira frase curta.", ["Segunda frase curta."])
    dek, body = dek_and_body("Título", "Única frase do resumo.")
    assert (dek, body) == ("Única frase do resumo.", ["Única frase do resumo."])
    assert dek_and_body("Título", "") == ("", [])


def test_long_first_sentence_is_truncated_in_dek_and_kept_whole_in_body():
    sentence = "O governo anunciou " + "uma série de medidas " * 20 + "para conter os preços."
    dek, body = dek_and_body("Título", sentence)
    assert len(dek) <= 221 and dek.endswith("…")
    assert body == [sentence]


def test_body_paragraphs_are_limited_and_plain():
    sentences = [f"Frase número {i} com algum conteúdo relevante para o leitor **executivo**." for i in range(60)]
    text = " ".join(sentences[:10]) + "\n" + " ".join(sentences[10:])
    dek, body = dek_and_body("Título", text)
    assert 1 <= len(body) <= 3
    assert all(len(p) <= 600 for p in body)
    assert all("*" not in p for p in body)
    assert "*" not in dek


def test_short_text_becomes_about_two_paragraphs():
    sentences = [f"Esta é a frase {i} de um resumo de tamanho médio escrito pelo veículo." for i in range(1, 13)]
    _, body = dek_and_body("Título", " ".join(sentences))
    assert len(body) == 2


def test_story_from_articles_uses_other_articles_image_and_dedupes_sources():
    a = make_article("a", "Título principal da matéria de teste", "Texto. Mais texto.", source_id="valor")
    b = make_article("b", "Título secundário", "", source_id="valor", image="https://img/b.jpg")
    c = make_article("c", "Outro título", "", source_id="ft", lang="en")
    taken: set[str] = set()
    story = story_from_articles([a, b, c], section="brasil", importance=3, page_info=None, taken=taken)
    assert story.image == "https://img/b.jpg"
    assert [s.name for s in story.sources] == ["Valor Econômico", "Financial Times"]
    assert story.article_ids == ["a", "b", "c"]
    assert story.id in taken


def test_story_from_articles_with_markup_only_title_uses_text():
    a = make_article("a", "<b></b>", "O Senado aprovou a reforma. Texto segue para sanção.")
    story = story_from_articles([a], section="politica", importance=2, page_info=None, taken=set())
    assert story.headline == "O Senado aprovou a reforma."
    assert story.dek == "Texto segue para sanção."


# ── bundle compartilhado (gerado por outra área) ───────────────────────────


@pytest.mark.skipif(not SHARED_BUNDLE.exists(), reason="tests/fixtures/bundle.json ainda não existe")
def test_shared_bundle_fixture(config):
    from datetime import datetime

    bundle = Bundle.from_dict(json.loads(SHARED_BUNDLE.read_text(encoding="utf-8")))
    now = datetime.fromisoformat(bundle.collected_at)
    edition = build_heuristic_edition(bundle, config, now=now)
    assert_edition_invariants(edition, config)
    assert len(edition.stories) == min(config.edition.target_stories, len(edition.stories))
    assert len(edition.stories) >= 10


# ── regressões da edição real de 29/09/2026 ─────────────────────────────────

VALOR_STUB = (
    "Com caixa elevado graças ao boom da IA, a Nvidia lança o maior programa de recompra já anunciado nos EUA. "
    "Matéria exclusiva para assinantes. Para ter acesso completo, acesse o link da matéria e faça o seu cadastro."
)
FOLHA_GIFT = (
    "Você tem 7 acessos por dia para dar de presente. Qualquer pessoa que não é assinante poderá ler. "
    "Assinantes podem liberar 7 acessos por dia para conteúdos da Folha."
)


def test_paywall_stub_primary_borrows_the_text_of_the_group():
    """Principal do Valor com resumo de paywall: a matéria usa o texto mais longo do
    grupo (mesmo idioma primeiro) e nenhuma frase de paywall/presente sobra."""
    valor = make_article("nv-valor", "Nvidia aprova limite adicional de US$ 150 bilhões em recompra de ações",
                         VALOR_STUB, source_id="valor", weight=1.3)
    folha_text = (
        "A Nvidia anunciou nesta segunda-feira um programa de recompra de US$ 150 bilhões, o maior da história, "
        "superando a Apple. A empresa tem caixa elevado graças à demanda por chips de inteligência artificial. "
        "Analistas avaliam que a medida sinaliza confiança na continuidade do ciclo de investimentos."
    )
    folha = make_article("nv-folha", "Nvidia supera Apple com recompra recorde de US$ 150 bilhões", "Resumo curto.",
                         source_id="folha")
    ft = make_article("nv-ft", "Nvidia launches record $150bn share buyback", "x " * 200, source_id="ft", lang="en")
    page_info = {"nv-folha": SimpleNamespace(image=None, description=FOLHA_GIFT, text=f"{FOLHA_GIFT}\n{folha_text}")}
    story = story_from_articles([valor, folha, ft], section="tecnologia", importance=5, page_info=page_info, taken=set())
    text_all = " ".join([story.dek, *story.body])
    assert "assinantes" not in text_all and "cadastro" not in text_all and "presente" not in text_all
    assert story.dek.startswith("A Nvidia anunciou nesta segunda-feira")
    assert story.lang == "pt" and story.headline.startswith("Nvidia aprova")


def test_paywall_sentence_never_becomes_the_dek():
    dek, body = dek_and_body("Título", f"{FOLHA_GIFT} O Ibovespa fechou em queda de 0,26%, aos 182.991 pontos.")
    assert dek == "O Ibovespa fechou em queda de 0,26%, aos 182.991 pontos."
    assert not any("presente" in p for p in body)


def test_abbreviations_do_not_cut_the_dek():
    summary = (
        "Five British men who were detained near R.A.F. Fairford, a base used by American forces in the war "
        "against Iran, were being released on bail but remained under investigation."
    )
    dek, _ = dek_and_body("UK releases suspects", summary)
    assert dek == summary  # frase inteira (abaixo de DEK_MAX), não "…detained near R.A.F."
    dek, _ = dek_and_body("Conselho", "O conselho entende que a atuação do Sr. Fulano foi regular. Outra frase aqui.")
    assert dek.startswith("O conselho entende que a atuação do Sr. Fulano foi regular.")


def test_intertitles_are_not_glued_to_the_next_paragraph():
    _, body = dek_and_body(
        "Dívida pública",
        "A dívida pública federal subiu 0,04% em agosto e chegou a R$ 9,29 trilhões.\nDívida interna\n"
        "A maior parte da dívida está concentrada no mercado doméstico, segundo o Tesouro.",
    )
    assert not any("Dívida interna A maior" in p for p in body)


def test_feed_thumbnails_lose_to_the_page_og_image():
    thumb = make_article("t", "Título da matéria do JOTA sobre o STF", "Resumo.", source_id="jota",
                         image="https://www.jota.info/wp-content/uploads/2026/09/foto-300x200.jpg")
    other = make_article("o", "Outra fonte", "Resumo.", source_id="g1", image="https://s2.glbimg.com/foto.jpg?fit=300%2C200")
    page_info = {"t": SimpleNamespace(image="https://www.jota.info/wp-content/uploads/2026/09/foto.jpg", description=None, text=None)}
    from qijournal.edit.heuristic import image_for

    assert image_for([thumb, other], page_info) == "https://www.jota.info/wp-content/uploads/2026/09/foto.jpg"
    # sem og:image, a miniatura ainda é melhor que nada
    assert image_for([thumb], {}) == thumb.image
    # foto grande do feed do principal continua em primeiro lugar
    big = make_article("b", "Título", source_id="valor", image="https://s2.glbimg.com/foto-grande.jpg")
    assert image_for([big], page_info) == big.image


def test_credits_prefer_the_on_topic_article_of_each_outlet():
    from qijournal.edit.heuristic import sources_for

    group = [
        make_article("p", "Nvidia aprova recompra de US$ 150 bilhões", source_id="valor"),
        make_article("cnbc", "Nvidia share buyback plan gets $150 billion boost", source_id="cnbc", lang="en"),
        make_article("ft-pope", "Pope Leo criticises Nvidia’s Jensen Huang over AI safety", source_id="ft", lang="en"),
        make_article("ft-buyback", "Nvidia launches record $150bn share buyback", source_id="ft", lang="en"),
        make_article("wsj", "Opinion | Pope Leo’s French Resistance", source_id="wsj", lang="en"),
    ]
    refs = {r.name: r.url for r in sources_for(group)}
    assert refs["Financial Times"].endswith("/ft-buyback")
    assert "The Wall Street Journal" not in refs
    assert list(refs)[0] == "Valor Econômico"


def test_english_only_duplicate_of_a_chosen_portuguese_fact_is_left_out(config):
    pt = Cluster(
        articles=[make_article("pt", "Reino Unido investiga plano contra base aérea de Fairford usada pela RAF",
                               "Suspeitos detidos perto da base de Fairford.", source_id="valor")],
        score=5.0,
        section="mundo",
    )
    en = Cluster(
        articles=[make_article("en", "Possible Terrorist Plot at RAF Fairford Air Base", "Five men detained near RAF Fairford.",
                               source_id="nyt", lang="en")],
        score=6.0,
        section="mundo",
    )
    fillers = [
        Cluster(articles=[make_article(f"f{i}", f"Fato {i}", source_id="g1")], score=1.0 + i / 10, section="brasil")
        for i in range(5)
    ]
    chosen = select_clusters([en, pt, *fillers], config)
    assert pt in chosen and en not in chosen


def test_lead_skips_service_titles_and_stories_without_text(config):
    from qijournal.edit.heuristic import _choose_lead
    from qijournal.models import Story

    def story(sid, headline, body_chars, image=True):
        return Story(id=sid, section="brasil", headline=headline, dek="", body=["x" * body_chars], sources=[],
                     article_ids=[sid], image="https://img/x.jpg" if image else None)

    stories = [
        story("lista", "Candidatos a deputado no MS: veja número e nome na lista", 900),
        story("stub", "Nota curta", 80),
        story("focus", "Mercado eleva projeção do IPCA para 4,99%, aponta Focus", 600),
    ]
    clusters = [Cluster(articles=[make_article(s.id, s.headline)], score=10 - i, section="brasil")
                for i, s in enumerate(stories)]
    assert _choose_lead(stories, clusters).id == "focus"


def test_wire_lists_recent_relevant_news_that_are_not_stories(config):
    from qijournal.edit.heuristic import wire_items

    clusters = [
        Cluster(articles=[make_article("used", "Copom mantém Selic", hours_ago=1)], score=9, section="brasil"),
        Cluster(articles=[make_article("w1", "Juros futuros sobem", hours_ago=2)], score=5, section="mercados"),
        Cluster(articles=[make_article("w2", "CNJ cancela precatórios", hours_ago=1)], score=4, section="juridico"),
        Cluster(articles=[make_article("en", "Gold edges higher", lang="en", hours_ago=1)], score=8, section="mercados"),
        Cluster(articles=[make_article("pol", "Lula fala em comício", hours_ago=1)], score=8, section="politica"),
        Cluster(articles=[make_article("srv", "Mega-Sena: confira o resultado", hours_ago=1)], score=8, section="brasil"),
    ]
    items = wire_items(clusters, {"used"})
    assert [i["title"] for i in items] == ["CNJ cancela precatórios", "Juros futuros sobem"]  # mais recente primeiro
    assert items[0]["url"].endswith("/w2") and items[0]["source"] == "Valor Econômico"
