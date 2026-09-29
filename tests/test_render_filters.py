"""Testes dos filtros de apresentação (qijournal/render/filters.py)."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from markupsafe import Markup

from qijournal.render import filters

# ── md_lite ──────────────────────────────────────────────────────────────────


def test_md_lite_converts_bold_and_returns_markup():
    out = filters.md_lite("Receita de **R$ 212,4 bi** e alta de **6,1%**.")
    assert isinstance(out, Markup)
    assert out == "Receita de <strong>R$ 212,4 bi</strong> e alta de <strong>6,1%</strong>."


def test_md_lite_escapes_html_before_formatting():
    out = filters.md_lite('**<script>alert(1)</script>** & "aspas"')
    assert "<script>" not in out
    assert out == "<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong> &amp; &#34;aspas&#34;"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("negrito sem fechar **aqui", "negrito sem fechar aqui"),
        ("asterisco * solto", "asterisco  solto"),
        ("*itálico* não suportado", "itálico não suportado"),
        ("** espaço interno **", " espaço interno "),
        ("****", ""),
    ],
)
def test_md_lite_never_leaks_asterisks(raw, expected):
    out = filters.md_lite(raw)
    assert "*" not in out
    assert out == expected


@pytest.mark.parametrize("value", [None, ""])
def test_md_lite_empty(value):
    assert filters.md_lite(value) == Markup("")


def test_plain_removes_markdown():
    assert filters.plain("**Copom** mantém *Selic* em [15%](https://x.y)") == "Copom mantém Selic em 15%"
    assert filters.plain(None) == ""


# ── URLs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://valor.globo.com/a.ghtml",
        "http://example.com/x?a=1&b=2",
        "HTTPS://EXAMPLE.COM/",
    ],
)
def test_safe_url_accepts_http_and_https(url):
    assert filters.safe_url(url) == url


def test_safe_url_strips_whitespace_and_encodes_inner_spaces():
    assert filters.safe_url("  https://x.com/a b.jpg \n") == "https://x.com/a%20b.jpg"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "javascript:alert(1)",
        " JavaScript:alert(1)",
        "java\tscript:alert(1)",
        "https://x.com/\nabc",
        "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:msgbox(1)",
        "//evil.example/x.js",
        "ftp://example.com/file",
        "/relativo/imagem.jpg",
        "https://",
        "http://[::1",
    ],
)
def test_safe_url_rejects_everything_else(url):
    assert filters.safe_url(url) is None


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("./", "./"),
        ("../", "../"),
        ("2026-09-29.html", "2026-09-29.html"),
        ("edicoes/", "edicoes/"),
        ("https://x.com/", "https://x.com/"),
        ("javascript:alert(1)", None),
        ("//evil.example/", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_href_allows_relative_paths(href, expected):
    assert filters.safe_href(href) == expected


# ── datas e horas ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("iso", "tz", "expected"),
    [
        ("2026-09-29T08:07:00+00:00", "America/Sao_Paulo", "05:07"),
        ("2026-09-29T08:07:00Z", "America/Sao_Paulo", "05:07"),
        ("2026-09-29T08:07:00", "America/Sao_Paulo", "05:07"),  # sem fuso = UTC
        ("2026-09-29T02:30:00+00:00", "America/Montevideo", "23:30"),
        ("2026-09-29T08:07:00+00:00", "Fuso/Inexistente", "08:07"),  # cai para UTC
        ("2026-08", "America/Sao_Paulo", ""),
        ("ontem", "America/Sao_Paulo", ""),
        (None, "America/Sao_Paulo", ""),
    ],
)
def test_local_time(iso, tz, expected):
    assert filters.local_time(iso, tz) == expected


NOW = "2026-09-29T08:07:00+00:00"


@pytest.mark.parametrize(
    ("iso", "expected"),
    [
        ("2026-09-29T08:06:40+00:00", "agora"),
        ("2026-09-29T08:10:00+00:00", "agora"),  # no futuro
        ("2026-09-29T07:42:00+00:00", "há 25 min"),
        ("2026-09-29T07:07:00+00:00", "há 1 h"),
        ("2026-09-29T05:00:00+00:00", "há 3 h"),
        ("2026-09-28T08:08:00+00:00", "há 23 h"),
        ("2026-09-28T02:07:00+00:00", "ontem"),
        ("2026-09-26T08:07:00+00:00", "há 3 dias"),
        (None, ""),
        ("inválido", ""),
    ],
)
def test_rel_age(iso, expected):
    assert filters.rel_age(iso, NOW) == expected


def test_rel_age_without_reference_is_empty():
    assert filters.rel_age("2026-09-29T05:00:00+00:00", None) == ""


def test_tz_label():
    assert filters.tz_label("America/Sao_Paulo") == "BRT"
    assert filters.tz_label("UTC", NOW) == "UTC"
    assert filters.tz_label("America/Montevideo", NOW) == "UTC-03"


# ── números e cores ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [(18.5, "19°"), (18.49, "18°"), (32.6, "33°"), (-0.4, "0°"), (-2.6, "-3°"), (None, "–"), (float("nan"), "–")],
)
def test_temp_rounds_half_up(value, expected):
    assert filters.temp(value) == expected


@pytest.mark.parametrize(
    ("pct", "expected"),
    [(0.19, "u"), (-1.16, "d"), (0.0, "f"), (0.004, "f"), (-0.004, "f"), (None, "")],
)
def test_change_class(pct, expected):
    assert filters.change_class(pct) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#1C49A5", "#1C49A5"),
        ("#abc", "#abc"),
        (" #0A2051 ", "#0A2051"),
        ("#0A2051CC", "#0A2051CC"),
        ("red", "#000000"),
        ("#12345", "#000000"),
        ("#fff;}</style><script>", "#000000"),
        (None, "#000000"),
    ],
)
def test_safe_color(value, expected):
    assert filters.safe_color(value, "#000000") == expected


def test_rgb_triplet():
    assert filters.rgb_triplet("#0A2051") == "10,32,81"
    assert filters.rgb_triplet("#fff") == "255,255,255"
    assert filters.rgb_triplet("#0A2051CC") == "10,32,81"


# ── SVG da marca ─────────────────────────────────────────────────────────────


def test_svg_data_uri_is_encoded():
    svg = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect fill="#394A7A"/></svg>'
    uri = filters.svg_data_uri(svg)
    assert uri.startswith("data:image/svg+xml,")
    payload = uri.split(",", 1)[1]
    for char in '<>#"':
        assert char not in payload
    assert unquote(payload) == '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#394A7A"/></svg>'


def test_initial_favicon_escapes_the_letter():
    svg = filters.initial_favicon("&", "#0A2051", "#57D9FF")
    assert "&amp;</text>" in svg
    assert filters.initial_favicon("qi journal", "#000", "#fff").endswith(">Q</text></svg>")


def test_clean_svg():
    assert filters.clean_svg(None) is None
    assert filters.clean_svg("não é svg") is None
    out = filters.clean_svg('<?xml version="1.0"?>\n<!-- logo --><svg viewBox="0 0 1 1"></svg>')
    assert isinstance(out, Markup)
    assert out == '<svg viewBox="0 0 1 1"></svg>'


def test_environment_autoescapes_and_exposes_filters():
    env = filters.environment()
    template = env.from_string("{{ x }}|{{ y|md_lite }}|{{ z|safe_url }}")
    out = template.render(x="<b>&</b>", y="**<i>**", z="javascript:alert(1)")
    assert out == "&lt;b&gt;&amp;&lt;/b&gt;|<strong>&lt;i&gt;</strong>|None"


@pytest.mark.parametrize(
    ("iso", "expected"),
    [
        ("2026-09-29T06:00:00+00:00", "03:00"),  # mesmo dia da edição (29/09 em Brasília)
        ("2026-09-28T12:00:00+00:00", "ontem, 09:00"),  # "09:00" sozinho parecia horário futuro
        ("2026-09-27T21:30:00+00:00", "27/09, 18:30"),
    ],
)
def test_local_time_names_the_day_when_it_is_not_the_edition_day(iso, expected):
    assert filters.local_time(iso, "America/Sao_Paulo", "2026-09-29T08:07:00+00:00") == expected
