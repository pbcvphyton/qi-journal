"""Testes do e-mail diário (qijournal/render/email.py)."""

from __future__ import annotations

import copy
import re

import pytest

from qijournal import text
from qijournal.render.email import render_email, story_url
from tests.fixtures.render.helpers import LEAD_ID, MALICIOUS_ID, load_edition, parse, pbcv_config, default_config

BASE = "https://pbcvphyton.github.io/qi-journal/"
PAGE = BASE + "edicoes/2026-09-29.html"  # links das matérias: cópia arquivada da edição


@pytest.fixture(scope="module")
def config():
    return default_config()


@pytest.fixture(scope="module")
def edition():
    return load_edition()


@pytest.fixture(scope="module")
def rendered(edition, config):
    return render_email(edition, config)


def linked_story_ids(html: str) -> list[str]:
    """Ids das matérias com título linkado (a manchete aparece em <h1>, as demais em <h2>)."""
    return re.findall(r'<h[12][^>]*><a href="' + re.escape(PAGE) + r'#s-([^"]+)"', html)


# ── assunto ──────────────────────────────────────────────────────────────────


def expected_subject(edition, brand: str = "PBCV Tech") -> str:
    lead = text.truncate(edition.story(edition.lead).headline, 70)
    return f"{brand} · 29/09/2026: {lead}"


def test_default_subject(rendered, edition):
    """O assunto traz o gancho do dia (a manchete, até 70 caracteres)."""
    subject, _, _ = rendered
    assert subject == expected_subject(edition)
    assert subject.startswith("PBCV Tech · 29/09/2026: Arrecadação federal bate recorde")
    assert len(subject) <= len("PBCV Tech · 29/09/2026: ") + 71


def test_subject_without_lead_and_with_line_breaks(edition):
    cfg = default_config()
    ed = copy.deepcopy(edition)
    ed.story(ed.lead).headline = "Linha um\nlinha dois"
    assert render_email(ed, cfg)[0] == "PBCV Tech · 29/09/2026: Linha um linha dois"
    ed.lead = "nao-existe"
    assert render_email(ed, cfg)[0] == "PBCV Tech · 29/09/2026: Terça-feira, 29 de setembro de 2026"


def test_custom_subject_template_with_short_date(edition):
    cfg = default_config()
    cfg.email.subject_template = "[{brand}] Edição de {date}"
    assert render_email(edition, cfg)[0] == "[PBCV Tech] Edição de 29/09/2026"


@pytest.mark.parametrize("template", ["{brand} {desconhecido}", "{0}", "{brand"])
def test_invalid_subject_template_falls_back(edition, template):
    cfg = default_config()
    cfg.email.subject_template = template
    assert render_email(edition, cfg)[0] == expected_subject(edition)


def test_subject_has_no_line_breaks(edition):
    cfg = default_config()
    cfg.email.subject_template = "{brand}\r\nBcc: alguem@example.com\n{date_label}"
    subject = render_email(edition, cfg)[0]
    assert "\n" not in subject and "\r" not in subject


# ── HTML ─────────────────────────────────────────────────────────────────────


def test_html_is_email_safe(rendered):
    _, html, _ = rendered
    assert len(html.encode("utf-8")) < 90 * 1024
    lowered = html.lower()
    assert "<script" not in lowered and "<svg" not in lowered
    assert "fonts.googleapis" not in lowered and "@import" not in lowered
    assert "**" not in html
    assert 'width="640"' in html and "max-width:640px" in html
    structure = parse(html)
    assert structure.errors == [] and structure.stack == []


def test_html_uses_inline_styles_for_layout(rendered):
    _, html, _ = rendered
    # O <style> é só melhoria progressiva: todo bloco de conteúdo tem estilo inline.
    for tag, attrs in parse(html).tags:
        if tag in ("td", "p", "h1", "h2", "a") and attrs.get("class") != "ink":
            assert attrs.get("style"), (tag, attrs)


def test_preheader_is_hidden_and_plain(rendered):
    _, html, _ = rendered
    pre = re.search(r'<div style="display:none;[^"]*">(.*?)&#847;', html, re.S).group(1)
    assert pre.startswith("A cinco dias do primeiro turno, o mercado embute prêmio eleitoral")
    assert "*" not in pre and "<" not in pre


def test_header_ticker_weather_editorial_and_briefing(rendered):
    _, html, _ = rendered
    # Cabeçalho claro: logo em PNG (GitHub Pages) numa célula branca; no modo escuro (CSS) troca pela versão escura.
    assert '<td align="center" bgcolor="#ffffff" class="px hdr" style="padding:28px 20px 18px;background:#ffffff;' in html
    assert (
        '<img class="lg-l" src="https://pbcvphyton.github.io/qi-journal/assets/pbcv-tech-logo-email.png"'
        ' width="260" height="38" alt="PBCV Tech"' in html
    )
    assert (
        '<!--[if !mso]><!--><img class="lg-d" src="https://pbcvphyton.github.io/qi-journal/assets/'
        'pbcv-tech-logo-email-dark.png" width="260" height="38" alt="" style="display:none;' in html
    )
    assert ".hdr{background:#111827!important;" in html and ".lg-d{display:block!important;" in html
    assert 'text-transform:uppercase;color:#5f6b7e;" class="muted">Seu terminal financeiro diário</p>' in html
    assert 'Terça-feira, 29 de setembro de 2026&nbsp;· <span style="white-space:nowrap">05:07 BRT</span>' in html
    assert "#687487" not in html  # cinza antigo, abaixo de AA sobre #f8f9fb
    assert (
        'Dólar <b style="color:#ffffff;font-weight:bold;">R$ 5,22</b> <span style="color:#2EDBEA;">+0,19%</span>'
        in html
    )
    assert '<span style="color:#FF5C8A;">-0,27%</span>' in html
    # cidade e "Amanhã" em grupos separados: no celular (360 px) a linha quebra em vez de estourar
    # dois grupos inquebráveis por cidade: no celular a linha quebra depois do "|" (nunca começa com ele)
    assert 'São Paulo</b> 19°C ↓19° ↑33° |</span> <span style="white-space:nowrap">Amanhã' in html
    assert '<td class="px soft rule muted" align="center"' in html
    assert "<span style=\"color:#56627e;\"> · </span>" not in html  # ticker sem "·" pendurado nas quebras
    assert "<strong>prêmio eleitoral</strong>" in html
    assert "Em 1 minuto" in html and "<strong>R$ 212,4 bi</strong>" in html


def test_lead_with_image_and_link(rendered, edition):
    _, html, _ = rendered
    lead = edition.story(LEAD_ID)
    lead_url = f"{PAGE}#s-{LEAD_ID}"
    assert f'<img src="{lead.image}" width="600" alt="{lead.headline}"' in html
    assert f'<a href="{lead_url}" class="lnk" style="color:#1E36C8">Ler na edição &rarr;</a>' in html
    assert linked_story_ids(html)[0] == LEAD_ID


def test_stories_are_limited_prioritized_and_grouped(rendered, edition, config):
    _, html, _ = rendered
    ids = linked_story_ids(html)[1:]
    assert len(ids) == config.email.max_stories == 14
    assert len(set(ids)) == len(ids) and LEAD_ID not in ids
    assert set(edition.secondary) <= set(ids) and set(edition.highlights) <= set(ids)
    # Agrupadas por seção, na ordem da edição.
    section_of = {s.id: s.section for s in edition.stories}
    order = [s.id for s in edition.sections]
    positions = [order.index(section_of[i]) for i in ids]
    assert positions == sorted(positions)
    titles = re.findall(r'<p class="sc sc-\d+" style="margin:0;padding:2px 0 2px 10px;[^"]*">([^<]+)</p>', html)
    assert len(titles) == len({section_of[i] for i in ids})


def test_max_stories_zero_keeps_only_the_lead(edition):
    cfg = default_config()
    cfg.email.max_stories = 0
    _, html, text = render_email(edition, cfg)
    assert linked_story_ids(html) == [LEAD_ID]
    assert text.count("#s-") == 1


def test_cta_and_footer(rendered):
    _, html, _ = rendered
    assert f'<a href="{BASE}" style="display:inline-block;' in html and "Abrir edição completa" in html
    assert f'<a href="{BASE}edicoes/"' in html
    assert '<a href="https://github.com/pbcvphyton/qi-journal"' in html
    assert "Gerado automaticamente em 05:07 (BRT) · Edição gerada por IA (claude-opus-5-5)" in html
    assert "64 fontes consultadas" in html


def test_malicious_story_is_escaped_in_email(edition):
    cfg = default_config()
    cfg.email.max_stories = 50
    _, html, text = render_email(edition, cfg)
    assert MALICIOUS_ID in linked_story_ids(html)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "Teste &lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;aspas&#34; no título" in html
    assert "javascript:" not in html.lower() and "javascript:" not in text.lower()
    assert "https://example.com/materia?a=1&b=2" not in html  # só aparece escapado (&amp;)
    assert "Fonte &lt;i&gt;Maliciosa&lt;/i&gt;" in html
    assert len(html.encode("utf-8")) < 90 * 1024


def test_optional_blocks_are_omitted(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial, ed.briefing, ed.quotes, ed.weather = "", [], [], []
    ed.mode, ed.model = "heuristic", None
    _, html, text = render_email(ed, config)
    assert "font-style:italic" not in html  # bloco do editorial
    assert "Em 1 minuto" not in html and "EM 1 MINUTO" not in text
    assert "EDITORIAL" not in text and "MERCADOS\n" not in text and "CLIMA" not in text
    assert "Edição automática (sem IA)" in html and "Edição automática (sem IA)" in text
    # Sem editorial, o preheader usa a linha fina da manchete.
    assert re.search(r'<div style="display:none;[^"]*">Receita somou R\$ 212,4 bilhões', html)


def test_pbcv_brand_email_uses_text_wordmark(edition):
    subject, html, _ = render_email(edition, pbcv_config())
    assert subject.startswith("PBCV Advogados · 29/09/2026: ")
    assert "<svg" not in html.lower()
    assert '<span style="color:#A3B4E0;">PBCV</span>' in html
    assert "background:#1B2745;" in html
    assert "<img src=\"https://pbcvphyton.github.io/qi-journal/assets/" not in html  # sem logo de e-mail
    assert 'class="px" style="padding:28px 20px 18px;border-bottom:3px solid #1B2745;"' in html  # cabeçalho no papel


def test_email_logo_falls_back_to_text(edition):
    cfg = default_config()
    cfg.brand.email_logo = {"src": "javascript:alert(1)", "width": 212, "height": 44}
    _, html, _ = render_email(edition, cfg)
    assert "javascript:" not in html and "<img src=\"https://pbcvphyton" not in html
    # wordmark em marinho (sem a classe "q", que pintaria o PBCV de aqua ilegível no branco)
    assert (
        '<span style="color:#0A2051;" class="ink">PBCV</span><span style="color:#0A2051;font-weight:900;" class="ink">'
        " Tech</span>" in html
    )


def test_solid_masthead_email_uses_blended_tones(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "#3322CC"
    cfg.brand.colors["on_masthead"] = "#F5F3ED"
    _, html, _ = render_email(edition, cfg)
    assert '<td align="center" bgcolor="#3322CC" class="px" style="padding:30px 20px 20px;background:#3322CC;' in html
    assert 'text-transform:uppercase;color:#CEC9E6;">Seu terminal financeiro diário</p>' in html  # 80% sobre o azul


# ── texto puro ───────────────────────────────────────────────────────────────


def test_text_version_is_plain_and_complete(rendered, edition):
    _, html, text = rendered
    assert "**" not in text
    assert not re.search(r"</?(strong|a|p|b|span|div|br)\b", text)
    assert text.startswith("PBCV TECH\nTerça-feira, 29 de setembro de 2026 · 05:07 BRT\n")
    for heading in ("MERCADOS", "CLIMA", "EDITORIAL", "EM 1 MINUTO", "MANCHETE", "BRASIL · ECONOMIA"):
        assert f"\n{heading}\n{'-' * len(heading)}\n" in text
    assert "prêmio eleitoral nos" in text
    assert "Dólar R$ 5,22 (+0,19%)" in text and "Selic 13,75%" in text
    assert f"Ler na edição: {PAGE}#s-{LEAD_ID}" in text
    # Mesmas matérias do HTML, com links por extenso.
    for story_id in linked_story_ids(html):
        assert f"{PAGE}#s-{story_id}" in text
    assert f"Abrir edição completa: {BASE}\n" in text
    assert f"Edições anteriores: {BASE}edicoes/\n" in text
    assert text.endswith("(claude-opus-5-5)\n")
    assert "Gerado automaticamente em 05:07 (BRT) · Edição gerada por IA (claude-opus-5-5)" in " ".join(text.split())


def test_text_lines_are_wrapped(rendered):
    _, _, text = rendered
    long_lines = [ln for ln in text.splitlines() if len(ln) > 72 and "https://" not in ln]
    assert long_lines == []


def test_story_url_encodes_unexpected_characters():
    assert story_url(BASE, "copom-mantem-selic") == f"{BASE}#s-copom-mantem-selic"
    assert story_url(BASE, 'a b"<c') == f"{BASE}#s-a%20b%22%3Cc"


# ── regressões da edição real de 29/09/2026 ─────────────────────────────────


def test_every_section_gets_at_least_one_story_in_the_email(edition):
    """Antes, Imobiliário e Tecnologia (além da manchete) ficavam de fora do e-mail."""
    cfg = default_config()
    cfg.email.max_stories = 8
    _, html, _ = render_email(edition, cfg)
    ids = linked_story_ids(html)[1:]
    section_of = {s.id: s.section for s in edition.stories}
    with_stories = {sec.id for sec in edition.sections if any(sid != LEAD_ID for sid in sec.story_ids)}
    assert {section_of[i] for i in ids} == with_stories
    assert len(ids) == 8


def test_email_is_slim_and_shows_at_most_three_sources(edition):
    from qijournal.models import SourceRef

    cfg = default_config()
    inflated = copy.deepcopy(edition)
    for story in inflated.stories:
        story.sources = [SourceRef(name=f"Veículo {i}", url=f"https://v{i}.example.com/{story.id}") for i in range(10)]
    _, html, _ = render_email(inflated, cfg)
    assert len(html.encode("utf-8")) <= 40 * 1024
    assert "Veículo 3" not in html and "· +7" in html
    # só a primeira fonte de cada matéria leva link
    assert html.count('href="https://v0.example.com/') >= 1 and 'href="https://v1.example.com/' not in html
    assert "\n  <" not in html  # sem indentação do template


def test_email_budget_drops_stories_until_it_fits(edition, monkeypatch):
    from qijournal.render import email as email_module

    cfg = default_config()
    monkeypatch.setattr(email_module, "MAX_EMAIL_BYTES", 20 * 1024)
    _, html, text = render_email(edition, cfg)
    assert len(html.encode("utf-8")) <= 20 * 1024
    shown = linked_story_ids(html)
    assert shown[0] == LEAD_ID and 1 <= len(shown) - 1 < cfg.email.max_stories
    assert text.count("#s-") == len(shown)  # texto puro com as mesmas matérias


def test_heuristic_email_skips_the_one_minute_block(edition, config):
    ed = copy.deepcopy(edition)
    ed.mode, ed.model = "heuristic", None
    _, html, _ = render_email(ed, config)
    assert "Em 1 minuto" not in html


def test_english_story_is_marked_in_the_email(edition, config):
    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).lang = "en"
    _, html, _ = render_email(ed, config)
    assert re.search(r'<h1 class="hl"[^>]*lang="en"', html)


def test_coverage_line_in_html_and_text(edition):
    from qijournal.models import Coverage, CoverageOutlet

    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).coverage = Coverage(
        topic="Arrecadação",
        conclusion="A maioria destacou o recorde.",
        outlets=[CoverageOutlet(name=n, stance=st) for n, st in (("Valor", "a"), ("Folha", "a"), ("g1", "b"))],
        side_a="Destaca o recorde",
        side_b="Destaca o risco fiscal",
    )
    _, html, text = render_email(ed, default_config())
    assert '<td width="67%" height="6" bgcolor="#1E36C8"' in html and '<td width="33%" height="6" bgcolor="#C8631A"' in html
    assert "Pende para: Destaca o recorde · 2 de 3 veículos</b> · A maioria destacou o recorde." in html
    flat = " ".join(text.split())  # o texto puro quebra as linhas
    assert "Cobertura: Pende para: Destaca o recorde · 2 de 3 veículos. A maioria destacou o recorde." in flat
    assert parse(html).errors == []


def test_dark_mode_keeps_each_section_color(rendered):
    _, html, _ = rendered
    # cada seção com a própria cor clareada no escuro (não um azul único para todas)
    assert re.search(r"\.sc-1\{color:#[0-9A-F]{6}!important;border-left-color:#[0-9A-F]{6}!important;\}", html)
    assert '<p class="sc sc-1" style=' in html
    assert ".cv-a{background:#6F86FF!important;}" in html

