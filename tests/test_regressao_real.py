"""Regressões medidas na coleta real de 29/09/2026 (tests/fixtures/real/).

A primeira edição publicada teve paywall na linha fina da manchete, lista de
candidatos como destaque, a Nvidia como manchete, Starship em Mercados e o Papa
em Tecnologia, o mesmo fato em pt e en separado, créditos que levavam a
matérias sobre outro assunto e um e-mail de 44 KB. Estes testes rodam o
pipeline (sem rede e sem IA) sobre aquela coleta.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from qijournal import text
from qijournal.collect.feeds import is_usable_image_url, sanitize_articles
from qijournal.config import load_config
from qijournal.edit import llm
from qijournal.edit.cluster import Cluster, is_service_title, rank_clusters
from qijournal.edit.heuristic import build_heuristic_edition
from qijournal.models import Bundle, SourceRef
from qijournal.render.email import MAX_EMAIL_BYTES, render_email

REAL = Path(__file__).parent / "fixtures" / "real" / "bundle-2026-09-29.json.gz"

BOILERPLATE = [
    "exclusiva para assinantes",
    "para ter acesso completo",
    "faça o seu cadastro",
    "siga o canal",
    "favorite o g1",
    "tem alguma sugestão de reportagem",
    "agora no g1",
    "notícias relacionadas",
    "seguir leyendo",
    "conheça o jota pro",
    "get our breaking news email",
    "follow the day",
    "volte ao topo",
    "antecipada a assinantes",
    "dar de presente",
    "initial plugin text",
    "quer receber reportagens",
]


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


@pytest.fixture(scope="module")
def raw() -> Bundle:
    with gzip.open(REAL, "rt", encoding="utf-8") as handle:
        return Bundle.from_dict(json.load(handle))


@pytest.fixture(scope="module")
def bundle(raw: Bundle, config) -> Bundle:
    """A coleta com as regras atuais de limpeza e exclusão (como no ``render --bundle``)."""
    clean = copy.copy(raw)
    clean.articles = sanitize_articles(
        raw.articles,
        exclude_url_patterns=config.edition.exclude_url_patterns,
        exclude_title_patterns=config.edition.exclude_title_patterns,
    )
    return clean


@pytest.fixture(scope="module")
def now(raw: Bundle) -> datetime:
    return datetime.fromisoformat(raw.collected_at)


@pytest.fixture(scope="module")
def clusters(bundle: Bundle, config, now) -> list[Cluster]:
    return rank_clusters(bundle.articles, config, now=now)


@pytest.fixture(scope="module")
def edition(bundle: Bundle, config, now):
    return build_heuristic_edition(bundle, config, now=now)


def cluster_of(clusters: list[Cluster], title_part: str) -> Cluster:
    matches = [c for c in clusters if any(title_part in a.title for a in c.articles)]
    assert len(matches) == 1, [c.primary.title for c in matches]
    return matches[0]


def titles(cluster: Cluster) -> list[str]:
    return [a.title for a in cluster.articles]


# ── coleta ───────────────────────────────────────────────────────────────────


def test_real_bundle_is_the_published_run(raw: Bundle):
    assert len(raw.articles) == 993 and sum(s.ok for s in raw.sources) == 64


def test_no_boilerplate_left_in_real_summaries(raw: Bundle, bundle: Bundle):
    before = sum(any(p in a.summary.lower() for p in BOILERPLATE) for a in raw.articles)
    assert before > 150  # ~16% dos resumos traziam boilerplate
    leftovers = [(a.source_id, p) for a in bundle.articles for p in BOILERPLATE if p in a.summary.lower()]
    assert leftovers == []
    # créditos de foto numa linha própria também saem
    credits = [a.id for a in bundle.articles for line in a.summary.split("\n") if re.fullmatch(r"(Arte|Reprodução)/\S+", line)]
    assert credits == []


def test_abr_tracking_pixel_is_not_a_photo(raw: Bundle, bundle: Bundle):
    pixels = [a for a in raw.articles if a.image and "ebc.png" in a.image]
    assert len(pixels) >= 20
    assert not any(is_usable_image_url(a.image) for a in pixels)
    assert not any(a.image and "ebc.png" in a.image for a in bundle.articles)


def test_service_and_sponsored_items_are_dropped(raw: Bundle, bundle: Bundle):
    ms = "Candidatos a deputado estadual no Mato Grosso do Sul (MS): veja número e nome na lista de 2026"
    assert any(a.title == ms for a in raw.articles)
    assert not any(a.title == ms for a in bundle.articles)
    assert not any("advertising partner" in a.summary for a in bundle.articles)
    assert not any("/loterias/" in a.url or "resultado-do-concurso" in a.url for a in bundle.articles)


# ── agrupamento e classificação ──────────────────────────────────────────────


def test_nvidia_story_is_not_mixed_with_the_pope(clusters):
    nvidia = cluster_of(clusters, "Nvidia aprova limite adicional de US$ 150 bilhões")
    assert not any(re.search(r"\bPope\b|\bPapa\b|Opinion", t) for t in titles(nvidia))
    assert any(a.source_id == "ft" and "buyback" in a.title for a in nvidia.articles)


def test_tariff_cut_and_arms_offer_are_separate_facts(clusters):
    tariffs = cluster_of(clusters, "China e EUA acertam cortes de tarifas")
    arms = cluster_of(clusters, "Trump perguntou a Xi se China gostaria de comprar armas")
    assert tariffs is not arms
    assert len({a.source_id for a in tariffs.articles}) >= 3


def test_salary_day_is_not_glued_to_candidate_lists(clusters):
    payday = cluster_of(clusters, "quinto dia útil de outubro de 2026")
    assert not any("candidatos" in t.lower() for t in titles(payday))
    assert is_service_title(payday.primary.title)


def test_same_fact_in_portuguese_and_english_is_one_story(clusters):
    raf = cluster_of(clusters, "Reino Unido investiga ação de Estado estrangeiro")
    assert any("RAF Fairford" in t for t in titles(raf)) and raf.primary.lang == "pt"
    openai = cluster_of(clusters, "OpenAI abandona lançamento de novo modelo")
    assert any(a.lang == "en" and "OpenAI" in a.title for a in openai.articles) and openai.primary.lang == "pt"


def test_unrelated_facts_are_not_merged_across_languages(clusters):
    evonik = cluster_of(clusters, "Evonik rejects")
    gold = cluster_of(clusters, "biggest gold miner rejects")
    assert evonik is not gold
    assert cluster_of(clusters, "Nvidia launches record") is not cluster_of(clusters, "OpenAI scraps release")


def test_starship_and_pope_land_in_the_right_sections(clusters):
    assert cluster_of(clusters, "Starship, da SpaceX, realiza 1º voo orbital").section == "tecnologia"
    assert cluster_of(clusters, "Papa diz que extrema direita").section == "mundo"


# ── edição automática ────────────────────────────────────────────────────────


def test_lead_is_a_brazilian_economy_or_legal_story(edition):
    lead = edition.story(edition.lead)
    assert lead.section in {"brasil", "mercados", "juridico"}
    assert not is_service_title(lead.headline) and lead.lang == "pt"
    assert "Nvidia" not in lead.headline


def test_brazilian_economic_day_is_in_the_edition(edition):
    headlines = " | ".join(s.headline for s in edition.stories)
    assert "Focus" in headlines and "Juros futuros" in headlines and "Ibovespa" in headlines
    assert any("Nvidia" in s.headline for s in edition.stories)  # o grande fato global continua
    assert all(s.lang == "pt" for s in edition.stories)  # nenhum fato publicado em inglês tendo versão em pt
    assert not any(is_service_title(s.headline) for s in edition.stories)


def test_no_paywall_or_widget_text_in_published_stories(edition):
    for story in edition.stories:
        for part in (story.dek, *story.body):
            assert not text.is_paywall(part), (story.headline, part)
            assert "Leia também" not in part and "Seguir leyendo" not in part


def test_credits_never_point_to_another_subject(edition):
    nvidia = next(s for s in edition.stories if s.headline.startswith("Nvidia aprova limite"))
    urls = " ".join(ref.url for ref in nvidia.sources).lower()
    assert "pope" not in urls and "papa" not in urls and "opinion" not in urls


def test_radar_brings_news_beyond_the_edition(edition):
    assert 5 <= len(edition.wire) <= 15
    story_titles = {s.headline for s in edition.stories}
    assert not any(item["title"] in story_titles for item in edition.wire)
    assert all(item["section"] in {"brasil", "mercados", "juridico", "imobiliario"} for item in edition.wire)


# ── e-mail ───────────────────────────────────────────────────────────────────


def test_real_email_fits_the_gmail_routine_budget(edition, config):
    _, html, _ = render_email(edition, config)
    assert len(html.encode("utf-8")) <= 40 * 1024 == MAX_EMAIL_BYTES


def test_email_stays_under_budget_even_with_many_sources(edition, config):
    inflated = copy.deepcopy(edition)
    for story in inflated.stories:
        story.sources = [SourceRef(name=f"Veículo {i}", url=f"https://exemplo{i}.com/{story.id}") for i in range(10)]
        story.dek = (story.dek + " ") * 3
    _, html, _ = render_email(inflated, config)
    assert len(html.encode("utf-8")) <= MAX_EMAIL_BYTES


# ── edição por IA: lista de candidatos ───────────────────────────────────────


def test_ai_candidates_cover_the_whole_day(bundle: Bundle, config, now):
    candidates = llm.build_candidates(bundle, config, now=now)
    assert len(candidates.lines) == config.edition.max_candidates == 180
    facts = {id(candidates.cluster_of[a.id]) for a in candidates.by_short_id.values()}
    assert len(facts) > 100  # antes: 42 fatos em 180 linhas
    sections = {}
    for article in candidates.by_short_id.values():
        sections.setdefault(candidates.suggested_section[article.id], set()).add(id(candidates.cluster_of[article.id]))
    assert len(sections["brasil"]) >= 3 and len(sections["mercados"]) >= 3 and len(sections["imobiliario"]) >= 1
    listed = " | ".join(candidates.lines)
    assert "Focus" in listed and "Juros futuros" in listed
