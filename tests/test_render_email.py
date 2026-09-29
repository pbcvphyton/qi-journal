"""Testes do e-mail diário (qijournal/render/email.py)."""

from __future__ import annotations

import copy
import re

import pytest

from qijournal.render.email import render_email, story_url
from tests.fixtures.render.helpers import LEAD_ID, MALICIOUS_ID, load_edition, parse, pbcv_config, qi_config

BASE = "https://pbcvphyton.github.io/qi-journal/"
PAGE = BASE + "edicoes/2026-09-29.html"  # links das matérias: cópia arquivada da edição


@pytest.fixture(scope="module")
def config():
    return qi_config()


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


def test_default_subject(rendered):
    subject, _, _ = rendered
    assert subject == "QI Journal — Terça-feira, 29 de setembro de 2026"


def test_custom_subject_template_with_short_date(edition):
    cfg = qi_config()
    cfg.email.subject_template = "[{brand}] Edição de {date}"
    assert render_email(edition, cfg)[0] == "[QI Journal] Edição de 29/09/2026"


@pytest.mark.parametrize("template", ["{brand} {desconhecido}", "{0}", "{brand"])
def test_invalid_subject_template_falls_back(edition, template):
    cfg = qi_config()
    cfg.email.subject_template = template
    assert render_email(edition, cfg)[0] == "QI Journal — Terça-feira, 29 de setembro de 2026"


def test_subject_has_no_line_breaks(edition):
    cfg = qi_config()
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
    assert '<span style="color:#57D9FF;">Q</span>' in html
    assert "Terça-feira, 29 de setembro de 2026 · 05:07 BRT" in html
    assert (
        'Dólar <b style="color:#ffffff;font-weight:bold;">R$ 5,22</b> <span style="color:#57D9FF;">+0,19%</span>'
        in html
    )
    assert '<span style="color:#FF2F80;">-0,27%</span>' in html
    assert "São Paulo</b> 19°C ↓19° ↑33° | Amanhã" in html
    assert "<strong>prêmio eleitoral</strong>" in html
    assert "Em 1 minuto" in html and "<strong>R$ 212,4 bi</strong>" in html


def test_lead_with_image_and_link(rendered, edition):
    _, html, _ = rendered
    lead = edition.story(LEAD_ID)
    lead_url = f"{PAGE}#s-{LEAD_ID}"
    assert f'<img src="{lead.image}" width="600" alt="{lead.headline}"' in html
    assert (
        f'<a href="{lead_url}" class="lnk" style="color:#1C49A5;text-decoration:none;">Ler na edição &rarr;</a>' in html
    )
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
    titles = re.findall(r'<p class="sc" style="margin:0;padding:2px 0 2px 10px;[^"]*">([^<]+)</p>', html)
    assert len(titles) == len({section_of[i] for i in ids})


def test_max_stories_zero_keeps_only_the_lead(edition):
    cfg = qi_config()
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
    cfg = qi_config()
    cfg.email.max_stories = 50
    _, html, text = render_email(edition, cfg)
    assert MALICIOUS_ID in linked_story_ids(html)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "Teste &lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;aspas&#34; no título" in html
    assert "javascript:" not in html.lower() and "javascript:" not in text.lower()
    assert '<a href="https://example.com/materia?a=1&amp;b=2"' in html
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
    assert subject.startswith("PBCV Advogados — ")
    assert "<svg" not in html.lower()
    assert '<span style="color:#A3B4E0;">PBCV</span>' in html
    assert "background:#1B2745;" in html


# ── texto puro ───────────────────────────────────────────────────────────────


def test_text_version_is_plain_and_complete(rendered, edition):
    _, html, text = rendered
    assert "**" not in text
    assert not re.search(r"</?(strong|a|p|b|span|div|br)\b", text)
    assert text.startswith("QI JOURNAL\nTerça-feira, 29 de setembro de 2026 · 05:07 BRT\n")
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
