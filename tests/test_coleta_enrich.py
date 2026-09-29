"""Testes do enriquecimento pela página da matéria (qijournal.collect.enrich) — sem rede."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from qijournal import net
from qijournal.collect.enrich import TEXT_MAX_CHARS, PageInfo, enrich, fetch_page_info, parse_page
from qijournal.models import Article

PAGES = Path(__file__).parent / "fixtures" / "coleta" / "pages"


def page(name: str) -> bytes:
    return (PAGES / name).read_bytes()


class FakeFetch:
    """Fetch injetável: URL → (bytes, content-type) ou exceção; registra chamadas."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, **kwargs) -> net.Response:
        with self._lock:
            self.calls.append((url, kwargs))
        result = self.routes.get(url)
        if result is None:
            raise net.FetchError(url, "HTTP 404", status=404)
        if isinstance(result, BaseException):
            raise result
        content, content_type = result if isinstance(result, tuple) else (result, "text/html; charset=utf-8")
        return net.Response(url=url, status=200, content=content, headers={"content-type": content_type})


def make_article(n: int, url: str | None = None) -> Article:
    return Article(id=f"id{n:010d}", url=url or f"https://ex.com/materia-{n}", title=f"Matéria {n}",
                   summary="Resumo", source_id="ex", source_name="Exemplo", lang="pt")


def html_page(body: str, head: str = "") -> str:
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'>{head}</head><body>{body}</body></html>"


# ── parse_page com páginas reais ─────────────────────────────────────────────


def test_jsonld_article_body_og_image_with_entities() -> None:
    info = parse_page(page("valor_jsonld.html").decode("utf-8"), base_url="https://valor.globo.com/politica/x.ghtml")
    # &amp; na URL do og:image decodificado; og:image tem prioridade sobre a imagem do JSON-LD
    assert info.image.endswith("/agu.jpg?ims=1200x630&quality=85")
    assert info.description.startswith("Ações civis públicas foram ajuizadas na Justiça Federal do DF contra sete")
    # articleBody com quebras de linha cruas (JSON não estrito) e entidades HTML
    assert info.text.startswith("A Advocacia-Geral da União (AGU) ajuizou")
    assert '"publicidade abusiva"' in info.text
    assert info.text.count("\n") == 2
    assert "Parágrafo curto do lide" not in info.text


def test_jsonld_graph_image_reference_and_article_paragraphs() -> None:
    info = parse_page(page("bloomberglinea_graph.html").decode("utf-8"),
                      base_url="https://www.bloomberglinea.com.br/negocios/fundos-imobiliarios-avancam/")
    # og:image/twitter:image são logos → descartados; imagem vem do @graph via @id
    assert info.image == ("https://www.bloomberglinea.com.br/resizer/v2/K7QX3ZVJ5NHGFLW2Y4P6T8R1AE.jpg"
                          "?auth=4f8a2c1e9b7d3f5a&width=1200&height=800")
    assert info.description.startswith("Negócios anunciados em setembro somam R$ 3,5 bilhões")
    # sem articleBody: parágrafos (≥ 60 chars) de <article>, sem aside/figcaption/script/assinatura
    paragraphs = info.text.split("\n")
    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("Fundos imobiliários (FIIs) de shoppings anunciaram")
    assert paragraphs[-1].endswith("mesmo com a Selic em 13,75% ao ano.")
    for unwanted in ("Por Redação", "Leia também", "Shopping em Campinas", "trackParagraph", "Assine"):
        assert unwanted not in info.text


def test_jsonld_list_with_image_list_and_html_body() -> None:
    info = parse_page(page("jota_list.html").decode("utf-8"), base_url="https://www.jota.info/stf/x")
    assert info.image == "https://images.jota.info/wp-content/uploads/2026/09/zanin-stf-plenario-1200x675.jpg"
    assert info.description.startswith("Ministro atendeu a pedido do TSE")  # <meta name=description>
    assert info.text.startswith("O ministro Cristiano Zanin")
    assert '"condição para o exercício do voto"' in info.text
    assert "<p>" not in info.text and "&quot;" not in info.text
    assert "Texto da página que não será usado" not in info.text


def test_broken_jsonld_relative_og_image_and_main_paragraphs() -> None:
    info = parse_page(page("conjur_main.html").decode("utf-8"),
                      base_url="https://www.conjur.com.br/2026-set-28/stj-afasta-improbidade/")
    assert info.image == "https://www.conjur.com.br/wp-content/uploads/2026/09/stj-fachada.jpg"
    assert info.description.startswith("Para a 1ª Turma")
    assert info.text.startswith("A 1ª Turma do Superior Tribunal de Justiça afastou")
    assert "Curto demais" not in info.text
    assert "texto quebrado" not in info.text


def test_paywall_page_yields_nothing() -> None:
    response = net.Response(url="https://www1.folha.uol.com.br/mercado/x.shtml", status=200,
                            content=page("folha_paywall.html"), headers={"content-type": "text/html"})
    info = parse_page(response.text(), base_url=response.url)  # página em ISO-8859-1
    assert info == PageInfo()
    assert info.is_empty()


@pytest.mark.parametrize(
    "paragraph",
    [
        "O Congresso aprovou a proposta e agora cabe ao presidente decidir se assine o decreto ainda nesta semana.",
        "Investors rushed to subscribe to the IPO, which was priced above the indicated range on Monday night.",
    ],
)
def test_legit_prose_mentioning_subscriptions_is_kept(paragraph: str) -> None:
    info = parse_page(html_page(f"<article><p>{paragraph}</p><p>{paragraph}</p></article>"))
    assert info.text == f"{paragraph}\n{paragraph}"


def test_twitter_image_and_protocol_relative_urls() -> None:
    head = ('<meta name="twitter:image" content="//cdn.example.com/fotos/materia.jpg">'
            '<meta name="twitter:description" content="Descrição &amp; contexto">')
    info = parse_page(html_page("", head))
    assert info.image == "https://cdn.example.com/fotos/materia.jpg"
    assert info.description == "Descrição & contexto"
    assert info.text is None


def test_text_is_capped_at_3000_chars() -> None:
    body = "\n".join(f"Parágrafo {i} com conteúdo suficiente para ser considerado corpo da matéria original."
                     for i in range(120))
    script = json.dumps({"@type": "NewsArticle", "articleBody": body})
    info = parse_page(html_page("", f'<script type="application/ld+json">{script}</script>'))
    assert len(info.text) <= TEXT_MAX_CHARS + 1
    assert info.text.endswith("…")


def test_paragraph_fallback_stops_near_limit() -> None:
    paragraph = "Texto longo da matéria " * 20
    info = parse_page(html_page("<article>" + f"<p>{paragraph}</p>" * 40 + "</article>"))
    assert TEXT_MAX_CHARS * 0.9 < len(info.text) <= TEXT_MAX_CHARS + 1


def test_short_jsonld_body_falls_back_to_paragraphs() -> None:
    script = json.dumps({"@type": ["NewsArticle"], "articleBody": "Resumo curto."})
    paragraph = "Parágrafo completo da matéria com mais de sessenta caracteres de conteúdo útil."
    info = parse_page(html_page(f"<article><p>{paragraph}</p><p>{paragraph}</p></article>",
                                f'<script type="application/ld+json">{script}</script>'))
    assert info.text == f"{paragraph}\n{paragraph}"


def test_non_article_jsonld_is_ignored() -> None:
    script = json.dumps({"@type": "Organization", "description": "Empresa de mídia", "image": "https://ex.com/org.jpg"})
    info = parse_page(html_page("", f'<script type="application/ld+json">{script}</script>'))
    assert info == PageInfo()


def test_garbage_html_never_raises() -> None:
    for garbage in ("", "<<<>>>", "<html><body><article><p>sem fechar", "\x00\x01binário", "<script>"):
        assert isinstance(parse_page(garbage), PageInfo)


# ── fetch_page_info ──────────────────────────────────────────────────────────


def test_fetch_page_info_uses_short_timeout_and_single_retry() -> None:
    url = "https://www.jota.info/stf/do-supremo/zanin"
    fetch = FakeFetch({url: page("jota_list.html")})
    info = fetch_page_info(url, fetch=fetch, timeout=7.5)
    assert info.text and info.image
    [(called_url, kwargs)] = fetch.calls
    assert called_url == url
    assert kwargs["timeout"] == 7.5 and kwargs["retries"] == 1


def test_fetch_page_info_decodes_charset_from_meta() -> None:
    url = "https://www1.folha.uol.com.br/mercado/x.shtml"
    paragraph = "<p>Ação da Petrobras sobe após anúncio de dividendos intermediários pela estatal.</p>"
    body = html_page(f"<article>{paragraph}{paragraph}</article>").replace("charset='utf-8'", "charset='iso-8859-1'")
    fetch = FakeFetch({url: (body.encode("iso-8859-1"), "text/html")})
    assert fetch_page_info(url, fetch=fetch).text.startswith("Ação da Petrobras")


@pytest.mark.parametrize(
    "result",
    [
        net.FetchError("https://ex.com/x", "HTTP 403", status=403),
        TimeoutError("timed out"),
        RuntimeError("falha inesperada"),
        (b"%PDF-1.7 ...", "application/pdf"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
    ],
)
def test_fetch_page_info_never_raises(result: object) -> None:
    fetch = FakeFetch({"https://ex.com/x": result})
    assert fetch_page_info("https://ex.com/x", fetch=fetch) == PageInfo()


# ── enrich ───────────────────────────────────────────────────────────────────


def test_enrich_maps_article_ids_and_respects_limit() -> None:
    articles = [make_article(i) for i in range(6)]
    routes = {a.url: page("jota_list.html") for a in articles}
    routes[articles[1].url] = page("folha_paywall.html")  # página sem nada aproveitável
    routes[articles[2].url] = net.FetchError(articles[2].url, "HTTP 500", status=500)
    fetch = FakeFetch(routes)

    result = enrich(articles, fetch=fetch, limit=4, max_workers=3)

    assert sorted(url for url, _ in fetch.calls) == sorted(a.url for a in articles[:4])
    assert set(result) == {articles[0].id, articles[3].id}  # vazios/falhos ficam de fora
    assert all(isinstance(info, PageInfo) and info.text for info in result.values())


def test_enrich_skips_duplicates_and_non_http_urls() -> None:
    first = make_article(1)
    duplicate = make_article(1)
    ftp = make_article(2, url="ftp://ex.com/arquivo")
    other = make_article(3)
    fetch = FakeFetch({first.url: page("conjur_main.html"), other.url: page("valor_jsonld.html")})
    result = enrich([first, duplicate, ftp, other], fetch=fetch, limit=3)
    assert [url for url, _ in sorted(fetch.calls)] == sorted([first.url, other.url])
    assert set(result) == {first.id, other.id}


@pytest.mark.parametrize("limit", [0, -5])
def test_enrich_with_non_positive_limit_does_nothing(limit: int) -> None:
    fetch = FakeFetch({})
    assert enrich([make_article(1)], fetch=fetch, limit=limit) == {}
    assert fetch.calls == []


def test_enrich_empty_list() -> None:
    assert enrich([], fetch=FakeFetch({})) == {}
