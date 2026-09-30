"""Testes da página web da edição e do índice do arquivo (qijournal/render/web.py)."""

from __future__ import annotations

import copy
import html as html_lib
import re

import pytest

from qijournal.models import Section, SourceRef, Story
from qijournal.render.web import build_view, render_archive_index, render_edition_page
from tests.fixtures.render.helpers import (
    LEAD_ID,
    MALICIOUS_ID,
    load_edition,
    parse,
    pbcv_config,
    default_config,
    visible_text,
)


@pytest.fixture(scope="module")
def config():
    return default_config()


@pytest.fixture(scope="module")
def edition():
    return load_edition()


@pytest.fixture(scope="module")
def page(edition, config):
    return render_edition_page(edition, config, home_href="./", archive_href="edicoes/")


def render(edition, config=None, **kwargs):
    kwargs.setdefault("home_href", "./")
    kwargs.setdefault("archive_href", "edicoes/")
    return render_edition_page(edition, config or default_config(), **kwargs)


# ── documento ────────────────────────────────────────────────────────────────


def test_document_basics(page):
    assert page.startswith("<!DOCTYPE html>")
    assert '<html lang="pt-BR"' in page
    assert "<title>PBCV Tech — Terça-feira, 29 de setembro de 2026</title>" in page
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in page
    assert len(page.encode("utf-8")) < 250 * 1024


def test_html_is_well_formed_with_unique_ids(page):
    structure = parse(page)
    assert structure.errors == []
    assert structure.stack == []
    duplicated = {k: n for k, n in structure.ids.items() if n > 1}
    assert duplicated == {}


def test_page_is_self_contained(page):
    structure = parse(page)
    scripts = [attrs for tag, attrs in structure.tags if tag == "script"]
    assert len(scripts) == 2 and all("src" not in attrs for attrs in scripts)
    assert "three" not in page.lower() and "gsap" not in page.lower()
    stylesheets = [attrs["href"] for tag, attrs in structure.tags if tag == "link" and attrs.get("rel") == "stylesheet"]
    assert stylesheets and all(href.startswith("https://fonts.googleapis.com/") for href in stylesheets)
    assert '<canvas id="mast-canvas"' in page


def test_meta_and_open_graph(page, edition):
    lead = edition.story(LEAD_ID)
    assert f'<meta property="og:image" content="{lead.image}">' in page
    assert '<meta property="og:title" content="PBCV Tech — Terça-feira, 29 de setembro de 2026">' in page
    description = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
    assert description.startswith("A cinco dias do primeiro turno, o mercado embute prêmio eleitoral")
    assert re.search(r'<meta property="og:description" content="A cinco dias', page)
    assert '<link rel="canonical" href="https://pbcvphyton.github.io/qi-journal/edicoes/2026-09-29.html">' in page
    assert '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' in page


def test_description_falls_back_to_lead_dek_without_editorial(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial = ""
    html = render(ed, config)
    dek = ed.story(LEAD_ID).dek
    assert f'<meta property="og:description" content="{dek}">' in html


# ── segurança e limpeza de conteúdo ──────────────────────────────────────────


def test_malicious_story_is_escaped(page):
    assert "<script>alert" not in page
    assert "Teste &lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;aspas&#34; no título" in page
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(2)&gt;" in page
    assert "<iframe" not in page
    assert "&lt;b&gt;crua&lt;/b&gt;" in page
    assert "Fonte &lt;i&gt;Maliciosa&lt;/i&gt;" in page
    assert "javascript:" not in page.lower()


def test_malicious_source_url_is_not_linked_but_valid_one_is(page):
    modal = re.search(rf'<div class="mo" id="s-{MALICIOUS_ID}".*?</article>', page, re.S).group(0)
    assert "<span>Fonte &lt;i&gt;Maliciosa&lt;/i&gt;</span>" in modal
    assert '<a href="https://example.com/materia?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">' in modal
    assert "<figure" not in modal  # imagem javascript: descartada


def test_no_markdown_leaks_anywhere(page):
    assert "**" not in page
    assert "*" not in visible_text(page)


def test_no_empty_headings(page):
    headings = parse(page).headings
    assert len(headings) > 50
    assert all(re.search(r"\w", text) for _, text in headings), [h for h in headings if not re.search(r"\w", h[1])]
    assert not re.search(r"<h[1-6][^>]*>\s*:?\s*</h[1-6]>", page)


def test_bold_is_rendered_as_strong(page):
    assert "<strong>R$ 212,4 bilhões</strong>" in page
    assert "<strong>prêmio eleitoral</strong>" in page


# ── estrutura da edição ──────────────────────────────────────────────────────


def test_every_story_has_a_modal_anchor_and_a_link(page, edition):
    structure = parse(page)
    for story in edition.stories:
        assert structure.ids[f"s-{story.id}"] == 1, story.id
        assert f"#s-{story.id}" in structure.hrefs, story.id
    modals = [attrs for tag, attrs in structure.tags if tag == "div" and attrs.get("class") == "mo"]
    assert len(modals) == len(edition.stories)
    assert all(m.get("role") == "dialog" and m.get("aria-modal") == "true" for m in modals)


def test_modal_content(page):
    modal = re.search(
        r'<div class="mo" id="s-stf-forma-maioria-para-limitar-cobranca-retroativa-do-difal".*?</article>', page, re.S
    ).group(0)
    assert 'aria-labelledby="t-stf-forma-maioria-para-limitar-cobranca-retroativa-do-difal"' in modal
    assert "<h3>Por que importa</h3>" in modal
    assert "<strong>Supremo Tribunal Federal</strong>" in modal
    assert (
        'href="https://www.jota.info/tributos/stf-difal-modulacao" target="_blank" rel="noopener noreferrer"' in modal
    )
    # publicado no dia anterior ao da edição: a hora ganha "ontem" (09:00 numa edição das 05:07 parecia futuro)
    assert "Publicado: <time" in modal and "ontem, 23:07</time> BRT · " in modal
    assert '<time datetime="2026-09-29T02:07:00+00:00" data-age>há 6 h</time>' in modal
    assert '<button class="mc2" type="button">&larr; Voltar à edição</button>' in modal


def test_hero_lead_secondary_and_highlights(page, edition):
    hero = re.search(r'<section class="hero".*?</section>', page, re.S).group(0)
    lead = edition.story(LEAD_ID)
    assert f'<img src="{lead.image}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">' in hero
    assert lead.headline in hero
    for story_id in edition.secondary:
        assert f'href="#s-{story_id}"' in hero
    highlights = re.search(r'<section class="dst".*?</section>', page, re.S).group(0)
    assert highlights.count("<li>") == len(edition.highlights) == 8
    for story_id in edition.highlights:
        assert f'href="#s-{story_id}"' in highlights


def test_tabs_one_per_section_plus_all(page, edition):
    tabs = re.findall(r'<button class="tab-btn"[^>]*data-tab="([^"]+)"[^>]*>([^<]+)<', page)
    assert [t[0] for t in tabs] == [s.id for s in edition.sections] + ["all"]
    titles = [html_lib.unescape(t[1]) for t in tabs[:-1]]
    assert titles == [s.title for s in edition.sections]  # nomes completos, sem truncar
    assert tabs[-1][1] == "Todas"
    assert 'aria-selected="true"' in re.search(r'<button class="tab-btn"[^>]*data-tab="brasil"[^>]*>', page).group(0)
    assert '<div class="tab-pane active" id="pane-brasil"' in page


def test_cards_show_optional_image_and_metadata(page):
    pane = re.search(r'<div class="tab-pane[^"]*" id="pane-brasil".*?(?=<div class="tab-pane)', page, re.S).group(0)
    cards = re.findall(r'<article class="cl".*?</article>', pane, re.S)
    assert len(cards) == 4
    with_image = [c for c in cards if "<figure" in c]
    assert len(with_image) == 2  # desemprego e BNDES não têm imagem
    assert "VALOR ECONÔMICO" not in cards[0]  # caixa alta é feita por CSS
    assert "<span>Valor Econômico · Folha de S.Paulo · Estadão · Agência Brasil</span>" in cards[0]
    assert '<time datetime="2026-09-29T05:55:00+00:00" data-age>há 2 h</time>' in cards[0]


def test_ticker_and_weather(page):
    assert 'Dólar <b>R$ 5,22</b> <span class="u">+0,19%</span>' in page
    assert 'Ibovespa <b>182.991 pts</b> <span class="d">-0,27%</span>' in page
    assert re.search(r"Selic <b>13,75%</b></li>", page)  # indicador sem variação
    weather = re.search(r'<section class="wx".*?</section>', page, re.S).group(0)
    assert "<strong>São Paulo</strong>19°C" in weather
    assert "<strong>Montevidéu</strong>14°C" in weather
    assert "Amanhã" in weather and 'aria-label="Pancadas de chuva fracas"' in weather


def test_header_dateline_and_tools(page):
    assert "Terça-feira, 29 de setembro de 2026 · 05:07 BRT" in page
    assert '<header class="mast-wrap">' in page  # cabeçalho claro, como no QI Journal
    assert '<h1 class="logo duo"><a href="./"><span class="lg lg-l"><svg' in page
    assert '<title id="pbcv-tech-logo-title">PBCV Tech</title>' in page
    assert '<title id="dk-pbcv-tech-logo-title">PBCV Tech</title>' in page  # versão do modo escuro
    assert 'class="q"' not in page  # wordmark de texto não é usado quando há logo
    assert 'id="dmBtn"' in page and 'id="srchBtn"' in page and 'id="srchInput"' in page
    assert 'id="rdprog"' in page and 'id="btt"' in page


def test_footer(page):
    footer = re.search(r'<footer class="ft">.*?</footer>', page, re.S).group(0)
    assert "64 fontes consultadas" in footer
    assert "Edição gerada por IA (claude-opus-5-5)" in footer
    assert '<a href="edicoes/">Edições anteriores</a>' in footer
    assert '<a href="https://github.com/pbcvphyton/qi-journal" rel="noopener">Código-fonte</a>' in footer
    assert "<details>" in footer and "59 de 64 feeds ok" in footer
    assert "Bloomberg Línea: <em>XML inválido</em>" in footer
    assert 'class="st-parcial"><span class="dot" aria-hidden="true"></span>South China Morning Post' in footer
    assert 'class="st-erro"><span class="dot" aria-hidden="true"></span>Exame' in footer
    assert (
        'class="st-ok"><span class="dot" aria-hidden="true"></span>Valor Econômico <span class="nf">(7 feeds)</span>'
        in footer
    )


def test_heuristic_mode_label(edition, config):
    ed = copy.deepcopy(edition)
    ed.mode, ed.model = "heuristic", None
    assert "Edição automática (sem IA)" in render(ed, config)


def test_archive_page_links(edition, config):
    html = render(edition, config, home_href="../", archive_href="./")
    assert '<h1 class="logo duo"><a href="../">' in html
    assert '<a href="./">Edições anteriores</a>' in html


def test_unsafe_hrefs_fall_back_to_defaults(edition, config):
    html = render(edition, config, home_href="javascript:alert(1)", archive_href="//evil.example/")
    hrefs = parse(html).hrefs
    assert not [h for h in hrefs if h.startswith(("javascript:", "//"))]
    assert '<h1 class="logo duo"><a href="./">' in html
    assert '<a href="edicoes/">Edições anteriores</a>' in html


# ── seções opcionais e dados incompletos ─────────────────────────────────────


def test_empty_optional_blocks_are_omitted(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial = "  "
    ed.briefing = ["", "**"]
    ed.quotes, ed.weather = [], []
    html = render(ed, config)
    assert 'class="op-bar"' not in html
    assert 'id="brief-h"' not in html
    assert 'class="tk"' not in html and 'class="wx"' not in html
    assert parse(html).errors == []


def test_empty_sections_are_omitted(edition, config):
    ed = copy.deepcopy(edition)
    ed.sections.insert(1, Section(id="vazia", title="Seção Vazia", color="#123456", story_ids=[]))
    ed.sections.append(Section(id="fantasma", title="Só ids inexistentes", color="#123456", story_ids=["nao-existe"]))
    html = render(ed, config)
    assert "Seção Vazia" not in html and 'data-tab="vazia"' not in html
    assert "Só ids inexistentes" not in html


def test_story_without_section_goes_to_outras(edition, config):
    ed = copy.deepcopy(edition)
    ed.sections[0].story_ids.remove("bndes-aprova-r-8-bilhoes-para-transicao-energetica")
    html = render(ed, config)
    assert 'data-tab="outras"' in html
    assert re.search(r'id="pane-outras".*?href="#s-bndes-aprova-r-8-bilhoes-para-transicao-energetica"', html, re.S)


def test_story_with_empty_headline_is_dropped(edition, config):
    ed = copy.deepcopy(edition)
    broken = ed.story("bndes-aprova-r-8-bilhoes-para-transicao-energetica")
    broken.headline = " ** "
    html = render(ed, config)
    assert "s-bndes-aprova-r-8-bilhoes-para-transicao-energetica" not in html
    assert parse(html).errors == []


def test_invalid_lead_falls_back_to_first_story(edition, config):
    ed = copy.deepcopy(edition)
    ed.lead = "nao-existe"
    view = build_view(ed, config)
    assert view.lead is not None and view.lead.id == ed.sections[0].story_ids[0]
    assert view.lead.id not in [s.id for s in view.secondary + view.highlights]


def test_edition_without_stories_still_renders(edition, config):
    ed = copy.deepcopy(edition)
    ed.stories, ed.sections, ed.secondary, ed.highlights = [], [], [], []
    html = render(ed, config)
    assert 'class="hero' not in html and 'class="tabs-wrap"' not in html
    assert 'id="srchBtn"' not in html
    assert parse(html).errors == []


def test_invalid_brand_color_is_replaced(edition):
    cfg = default_config()
    cfg.brand.colors["primary"] = "red;}</style><script>alert(1)</script>"
    html = render(edition, cfg)
    assert "<script>alert(1)" not in html
    assert "--qi:#1E36C8;" in html


# ── view ─────────────────────────────────────────────────────────────────────


def test_view_cleans_sources_and_orders_radar(edition, config):
    ed = copy.deepcopy(edition)
    story = ed.story(LEAD_ID)
    story.sources.append(SourceRef(name="valor econômico", url="https://outra.url/"))
    story.sources.append(SourceRef(name="**Negrito**", url="ftp://x"))
    view = build_view(ed, config)
    names = [s.name for s in view.lead.sources]
    assert names == ["Valor Econômico", "Folha de S.Paulo", "Estadão", "Agência Brasil", "Negrito"]
    assert view.lead.sources[-1].url is None
    # edição sem "wire": o Radar lista as matérias mais recentes da própria edição (âncoras)
    assert not ed.wire and 0 < len(view.radar) <= 8
    by_anchor = {f"#{s.anchor}": s for s in view.stories}
    times = [by_anchor[r.href].published for r in view.radar]
    assert times == sorted(times, reverse=True)
    assert not any(r.external for r in view.radar)


def test_view_strips_markdown_from_headline_and_dek(edition, config):
    ed = copy.deepcopy(edition)
    ed.stories.append(
        Story(
            id="md",
            section="brasil",
            headline="**Título** com *marcação*",
            dek="# Linha fina",
            body=["Texto **forte**"],
            sources=[],
            article_ids=[],
        )
    )
    ed.sections[0].story_ids.append("md")
    view = build_view(ed, config)
    story = next(s for s in view.stories if s.id == "md")
    assert story.headline == "Título com marcação"
    assert story.dek == "Linha fina"
    assert story.body == ["Texto **forte**"]  # negrito só vira <strong> no template


# ── marca alternativa ────────────────────────────────────────────────────────


def test_pbcv_brand_renders_inline_logo(edition):
    html = render(edition, pbcv_config())
    assert "<title>PBCV Advogados — Terça-feira, 29 de setembro de 2026</title>" in html
    assert '<h1 class="logo"><a href="./"><svg' in html
    assert '<title id="pbcv-logo-title">PBCV Advogados</title>' in html
    assert 'class="q"' not in html  # wordmark de texto não é usado quando há logo
    assert "--qi:#394A7A;" in html and "--qn:#1B2745;" in html
    favicon = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)"', html).group(1)
    assert favicon.startswith("data:image/svg+xml,%3Csvg")
    assert "Boletim diário · Mercados, Negócios e Direito" in html
    structure = parse(html)
    assert structure.errors == [] and structure.stack == []
    # Sem brand.masthead: cabeçalho no papel e logo invertido por filtro no modo escuro.
    assert '<header class="mast-wrap">' in html and ".mast-wrap.solid{" not in html
    assert '<meta name="theme-color" content="#1B2745">' in html


def test_default_brand_logo_palette_and_favicon(edition):
    html = render(edition, default_config())
    assert '<meta name="theme-color" content="#0A2051">' in html
    assert '<meta name="generator" content="PBCV Tech · qijournal 2.0">' in html
    assert "--qi:#1E36C8;--qn:#0A2051;--qc:#1FD1E1;" in html
    assert ".mast-wrap.solid{" not in html  # cabeçalho claro
    # Luz Cruzada: PBCV na tinta marinho da QI; versão escura própria, sem filtro de inversão
    assert 'fill="#0A2051"' in html and 'fill="#DCE4FF"' in html
    assert ':root[data-theme="dark"] .mast h1.logo:not(.mono):not(.duo) svg{filter:' in html
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:44px;max-width:100%}" in html
    assert "--ink:#1a2332;--paper:#fff;--bg:#f0f2f5;" in html  # neutros frios do layout original
    favicon = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)"', html).group(1)
    assert favicon.startswith("data:image/svg+xml,%3Csvg") and "prisma-tile" in favicon
    assert "QI Journal" not in html


def test_solid_masthead_is_still_supported(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "#3322CC"
    cfg.brand.colors["on_masthead"] = "#F5F3ED"
    html = render(edition, cfg)
    assert '<header class="mast-wrap solid">' in html and '<meta name="theme-color" content="#3322CC">' in html
    assert ".mast-wrap.solid{--mh:#3322CC;--on-mh:#F5F3ED;--on-mh-rgb:245,243,237;" in html


def test_text_wordmark_without_logo(edition):
    cfg = default_config()
    cfg.brand.logo_svg = None
    html = render(edition, cfg)
    assert '<h1><a href="./"><span class="i">PBCV</span><span class="journal"> Tech</span></a></h1>' in html


def test_invalid_masthead_color_keeps_paper_header(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "blue;}</style><script>alert(1)</script>"
    html = render(edition, cfg)
    assert "<script>alert(1)" not in html
    assert '<header class="mast-wrap">' in html and ".mast-wrap.solid{" not in html


# ── arquivo ──────────────────────────────────────────────────────────────────


def test_archive_index_groups_by_month(config):
    entries = [
        {
            "date": "2026-09-29",
            "date_label": "Terça-feira, 29 de setembro de 2026",
            "href": "2026-09-29.html",
            "lead_headline": "Arrecadação <b>recorde</b> & **ok**",
            "mode": "ai",
        },
        {
            "date": "2026-09-28",
            "date_label": "Segunda-feira, 28 de setembro de 2026",
            "href": "2026-09-28.html",
            "lead_headline": "Outra manchete",
            "mode": "heuristic",
        },
        {
            "date": "2026-08-31",
            "date_label": "Segunda-feira, 31 de agosto de 2026",
            "href": "2026-08-31.html",
            "lead_headline": "",
            "mode": "ai",
        },
        {"date": "2026-08-30", "date_label": "Domingo", "href": "javascript:alert(1)", "lead_headline": "x"},
    ]
    html = render_archive_index(entries, config, home_href="../")
    months = re.findall(r'<h3 class="arch-m">([^<]+)</h3>', html)
    assert months == ["Setembro de 2026", "Agosto de 2026"]
    assert "Arrecadação &lt;b&gt;recorde&lt;/b&gt; &amp; ok" in html
    assert html.count('<span class="badge"') == 2
    assert '<a href="2026-09-28.html"><time datetime="2026-09-28">' in html
    assert "javascript:" not in html
    assert '<a class="arch-home" href="../">' in html
    assert "3 edições no arquivo" in html
    assert "<title>PBCV Tech — Edições anteriores</title>" in html
    structure = parse(html)
    assert structure.errors == [] and all(re.search(r"\w", t) for _, t in structure.headings)


def test_archive_index_empty(config):
    html = render_archive_index([], config, home_href="../")
    assert "Nenhuma edição arquivada ainda." in html
    assert "0 edições no arquivo" in html


def test_lead_excerpt_skips_paragraphs_that_repeat_the_dek(edition, config):
    ed = copy.deepcopy(edition)
    lead = ed.story(LEAD_ID)
    lead.body = [lead.dek + " Mais um detalhe do texto.", "Segundo parágrafo com **dado novo**."]
    view = build_view(ed, config)
    assert view.lead_excerpt == "Segundo parágrafo com **dado novo**."
    assert '<p class="sub">Segundo parágrafo com <strong>dado novo</strong>.</p>' in render(ed, config)
    lead.body = [lead.dek]
    assert build_view(ed, config).lead_excerpt == ""
    assert '<p class="sub">' not in render(ed, config)


# ── texto da manchete e do modal ─────────────────────────────────────────────


def _modal(page_html: str, story_id: str) -> str:
    start = page_html.index(f'<div class="mo" id="s-{story_id}"')
    return page_html[start : page_html.index("</article>", start)]


def test_modal_hides_dek_already_contained_in_body(edition):
    ed = copy.deepcopy(edition)
    lead = ed.story(ed.lead)
    first = "A arrecadação federal somou R$ 230 bilhões em agosto, alta real de 8% sobre agosto de 2025, com o IR."
    lead.dek = first[:60].rstrip() + "…"  # linha fina cortada da 1ª frase (edição automática)
    lead.body = [first + " Outra frase.", "Segundo parágrafo."]
    other = next(s for s in ed.stories if s.id != ed.lead and s.dek and s.body)
    other.body = ["Parágrafo que não repete a linha fina."]
    page_html = render(ed)
    assert 'class="mdek"' not in _modal(page_html, lead.id)
    assert 'class="mdek"' in _modal(page_html, other.id)
    view = build_view(ed, default_config())
    assert view.lead.dek_in_body and not next(s for s in view.stories if s.id == other.id).dek_in_body


def test_lead_extra_paragraphs_only_for_missing_image(edition):
    ed = copy.deepcopy(edition)
    lead = ed.story(ed.lead)
    lead.dek = "Linha fina própria."
    lead.body = ["Primeiro parágrafo.", "Segundo **parágrafo**.", "Terceiro parágrafo.", "Quarto parágrafo."]
    view = build_view(ed, default_config())
    assert view.lead_excerpt == "Primeiro parágrafo."
    assert view.lead_more == ["Segundo **parágrafo**.", "Terceiro parágrafo."]
    page_html = render(ed)
    assert '<p class="sub more">Segundo <strong>parágrafo</strong>.</p>' in page_html
    assert ".hero-l .more{display:none}" in page_html  # só aparecem sem imagem (CSS :has)


# ── regressões da edição real de 29/09/2026 ─────────────────────────────────


def test_english_story_is_marked_with_lang_en(edition, config):
    ed = copy.deepcopy(edition)
    story = next(s for s in ed.stories if s.id != LEAD_ID)
    story.lang = "en"
    page = render_edition_page(ed, config, home_href="./", archive_href="edicoes/")
    card = re.search(rf'<article class="cl"[^>]*>(?:(?!</article>).)*#s-{re.escape(story.id)}".*?</article>', page, re.S)
    assert card and '<h3 lang="en">' in card.group(0)
    modal = re.search(rf'<div class="mo" id="s-{re.escape(story.id)}".*?</article>', page, re.S).group(0)
    assert '<article class="ml" tabindex="-1" lang="en">' in modal
    # matérias em português não repetem o atributo (a página já é pt-BR)
    assert 'lang="pt' not in re.sub(r'<html lang="pt-BR"|lang="pt-BR">Publicado|<p class="mback" lang="pt-BR"', "", page)


def test_archive_copy_has_no_frozen_relative_age(edition, config):
    archived = render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)
    home = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert "<time datetime=\"2026-09-29T05:55:00+00:00\" data-age>" in home
    assert " data-age>" not in archived
    assert "há 2 h" in home and "há 2 h" not in archived


def test_radar_uses_the_wire_with_links_to_the_sources(edition, config):
    ed = copy.deepcopy(edition)
    ed.wire = [
        {"title": "Juros futuros sobem com pesquisas", "url": "https://valor.globo.com/financas/juros.ghtml",
         "source": "Valor Econômico", "published": "2026-09-28T20:10:00+00:00", "section": "mercados"},
        {"title": "Link inválido", "url": "javascript:alert(1)", "source": "X", "published": None, "section": "brasil"},
    ]
    page = render_edition_page(ed, config, home_href="./", archive_href="edicoes/")
    radar = re.search(r'<ol class="radar-list">.*?</ol>', page, re.S).group(0)
    assert radar.count("<li>") == 1 and "javascript:" not in radar
    assert 'href="https://valor.globo.com/financas/juros.ghtml" target="_blank" rel="noopener noreferrer"' in radar
    assert '<span class="tm">ontem, 17:10</span>' in radar
    # a edição round-trip mantém o campo (e edições antigas, sem ele, continuam válidas)
    from qijournal.models import Edition

    assert Edition.from_dict(ed.to_dict()).wire[0]["title"] == "Juros futuros sobem com pesquisas"
    old = ed.to_dict()
    del old["wire"]
    assert Edition.from_dict(old).wire == []


def test_all_tab_opens_first_when_the_first_section_is_thin(edition, config):
    ed = copy.deepcopy(edition)
    first = ed.sections[0]
    keep, move = first.story_ids[:2], first.story_ids[2:]
    first.story_ids = keep
    ed.sections[1].story_ids = move + ed.sections[1].story_ids
    for sid in move:
        ed.story(sid).section = ed.sections[1].id
    page = render_edition_page(ed, config, home_href="./", archive_href="edicoes/")
    assert '<section class="tabs-wrap all"' in page
    assert re.search(r'id="tab-all"[^>]*aria-selected="true"', page)
    assert re.search(rf'data-tab="{first.id}"[^>]*aria-selected="false"', page)
    assert page.count('class="tab-pane active"') == len(ed.sections)


def test_modal_opened_from_the_email_link_can_go_back_to_the_edition(page):
    assert "w.history.pushState(null, '', '#' + initial.id)" in page  # "voltar" do sistema fecha o modal
    assert "w.addEventListener('load'" in page  # foco no diálogo depois da navegação por âncora
    assert ".mc{position:sticky" in page  # o × acompanha a rolagem
    assert "closest('.mc,.mc2')" in page


def test_desktop_tabs_fit_without_hiding_sections(page, config):
    assert "@media (min-width:900px){.tab-btn{padding:12px 11px;letter-spacing:.8px}" in page
    assert next(s.title for s in config.sections if s.id == "imobiliario") == "Imobiliário"


def test_unknown_brand_falls_back_to_the_default(caplog):
    # Ex.: variável QIJ_BRAND=qi que sobrou da marca antiga no GitHub.
    from qijournal.config import load_config

    with caplog.at_level("WARNING", logger="qijournal.config"):
        cfg = load_config(env={"QIJ_BRAND": "qi"})
    assert cfg.brand.key == "tech" and cfg.brand.name == "PBCV Tech"
    assert "Marca 'qi' não existe" in caplog.text


# ── cobertura comparada ──────────────────────────────────────────────────────


def _coverage(stances, *, sides=("Destaca o alívio", "Destaca o risco"), **extra):
    from qijournal.models import Coverage, CoverageOutlet

    outlets = [
        CoverageOutlet(name=f"Veículo {i}", stance=st, framing=f"Enfoque {i}", url=f"https://v{i}.example/n")
        for i, st in enumerate(stances)
    ]
    return Coverage(
        topic="Assunto do dia", conclusion="A cobertura pendeu para o alívio.", outlets=outlets,
        side_a=sides[0], side_b=sides[1], **extra,
    )


def test_coverage_view_meter_grows_toward_the_side_with_more_outlets():
    from qijournal.render.web import coverage_view

    view = coverage_view(_coverage(["a", "a", "a", "b", "neutro", "neutro", "a"]))
    assert (view.pct_a, view.pct_n, view.pct_b) == (57, 29, 14) and view.lean == "a"
    assert view.lean_label == "Pende para: Destaca o alívio · 4 de 7 veículos"
    assert view.aria == "Destaca o alívio: 4 veículos; neutros: 2; Destaca o risco: 1 veículo"
    tie = coverage_view(_coverage(["a", "b"]))
    assert tie.lean == "equilibrio" and tie.lean_label == "Equilíbrio: 1 × 1 de 2 veículos"
    flat = coverage_view(_coverage(["a", "b", "neutro"], sides=("", "")))  # sem debate: todos neutros
    assert flat.lean == "convergente" and (flat.pct_a, flat.pct_n, flat.pct_b) == (0, 100, 0)
    assert flat.lean_label == "Sem divergência entre os 3 veículos"
    assert coverage_view(None) is None


def test_coverage_on_cards_modal_and_compared_section(edition, config):
    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).coverage = _coverage(["a", "a", "b"])
    card_story = next(s for s in ed.stories if s.id not in (ed.lead, *ed.secondary))
    card_story.coverage = _coverage(["b", "b", "a"])
    ed.compared = [_coverage(["a", "neutro"], section="mercados", url="javascript:alert(1)", published=ed.generated_at)]
    page = render(ed, config)
    assert page.count('<div class="cov mini lean-a"') == 2  # manchete + card dela na aba da seção
    assert page.count('<div class="cov mini lean-b"') == 1  # card
    modal = re.search(rf'<div class="mo" id="s-{LEAD_ID}".*?</article>\s*</div>', page, re.S).group(0)
    assert '<section class="mcov" aria-label="Cobertura comparada">' in modal
    assert '<details class="cov-who" open>' in modal
    assert '<span class="cov-a" style="width:67%"></span><span class="cov-b" style="width:33%"></span>' in modal
    assert "A cobertura pendeu para o alívio." in modal
    section = re.search(r'<section class="cmp" id="cobertura".*?</section>', page, re.S).group(0)
    assert "<h3>Assunto do dia</h3>" in section  # URL insegura: título sem link
    assert "Mercados &amp; Finanças" in section and "javascript:" not in page
    assert parse(page).errors == []


def test_no_coverage_no_section(edition, config):
    page = render(edition, config)
    assert 'class="cmp"' not in page and 'class="cov ' not in page


def test_logo_with_dark_variant_and_height(edition):
    cfg = default_config()
    cfg.brand.logo_svg = '<svg viewBox="0 0 10 2"><title id="t">Claro</title><path fill="#123456" d="M0 0h1"/></svg>'
    cfg.brand.logo_svg_dark = '<svg viewBox="0 0 10 2"><title id="t">Escuro</title><path fill="#FFFFFF" d="M0 0h1"/></svg>'
    cfg.brand.logo_height = 56
    html = render(edition, cfg)
    assert '<h1 class="logo duo"><a href="./"><span class="lg lg-l"><svg' in html
    assert '<span class="lg lg-d"><svg viewBox="0 0 10 2"><title id="dk-t">Escuro</title>' in html
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:56px;max-width:100%}" in html
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:40px}" in html  # celular: 72%
    assert parse(html).ids["t"] == 1 and parse(html).ids["dk-t"] == 1
