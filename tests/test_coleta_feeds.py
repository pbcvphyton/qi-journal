"""Testes da coleta de feeds (qijournal.collect.feeds) — sem rede, com fixtures reais."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qijournal import net, text
from qijournal.collect import feeds
from qijournal.collect.feeds import (
    MAX_ITEMS_PER_FEED,
    SUMMARY_MAX_CHARS,
    article_id,
    canonical_url,
    collect_feeds,
    dedupe_articles,
    is_usable_image_url,
    parse_feed,
)
from qijournal.config import SourceConfig, load_config
from qijournal.models import Article

FEEDS_DIR = Path(__file__).parent / "fixtures" / "coleta" / "feeds"
NOW = datetime(2026, 9, 29, 8, 7, tzinfo=timezone.utc)
GLOBAL_EXCLUDE = load_config(env={}).edition.exclude_url_patterns


def feed_bytes(name: str) -> bytes:
    return (FEEDS_DIR / name).read_bytes()


def make_source(**overrides) -> SourceConfig:
    base = dict(id="valor", name="Valor Econômico", url="https://valor.globo.com/rss/valor/", lang="pt",
                weight=1.3, topics=["brasil"])
    base.update(overrides)
    return SourceConfig(**base)


def parse(name: str, source: SourceConfig | None = None, **kwargs) -> list[Article]:
    options = dict(now=NOW, max_age_hours=30, exclude=GLOBAL_EXCLUDE)
    options.update(kwargs)
    return parse_feed(feed_bytes(name), source or make_source(), **options)


def by_title(articles: list[Article], fragment: str) -> Article:
    matches = [a for a in articles if fragment in a.title]
    assert len(matches) == 1, [a.title for a in articles]
    return matches[0]


class FakeFetch:
    """Fetch injetável: URL → bytes, exceção ou função; registra as chamadas (thread-safe)."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, **kwargs) -> net.Response:
        with self._lock:
            self.calls.append((url, kwargs))
        if url not in self.routes:
            raise net.FetchError(url, "HTTP 404", status=404)
        result = self.routes[url]
        if isinstance(result, BaseException):
            raise result
        return net.Response(url=url, status=200, content=result, headers={"content-type": "application/rss+xml"})


def rss(items: list[str]) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
        "<link>https://example.com/</link>" + "".join(items) + "</channel></rss>"
    ).encode("utf-8")


def rss_item(title: str, link: str, when: datetime | None, description: str = "Resumo.") -> str:
    date = f"<pubDate>{when.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>" if when else ""
    return f"<item><title>{title}</title><link>{link}</link>{date}<description>{description}</description></item>"


# ── canonical_url / article_id ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # redirecionador da Folha
        ("https://redir.folha.com.br/redir/online/mercado/rss091/*https://www1.folha.uol.com.br/mercado/2026/09/dolar.shtml",
         "https://www1.folha.uol.com.br/mercado/2026/09/dolar.shtml"),
        # FT: ?syn-xxxx=1
        ("https://www.ft.com/content/c7685a7e-7745-4cbc-8053-4958d0ea449b?syn-25a6b1a6=1",
         "https://www.ft.com/content/c7685a7e-7745-4cbc-8053-4958d0ea449b"),
        # BBC: http → https, at_* e fragmento
        ("http://www.bbc.co.uk/news/articles/cm5y5nynl75ko?at_medium=RSS&at_campaign=rss#comments",
         "https://www.bbc.co.uk/news/articles/cm5y5nynl75ko"),
        # utm_* removidos, outros parâmetros preservados na ordem e na codificação original
        ("https://www.moneytimes.com.br/materia/?utm_source=feed&id=77&q=a%20b&utm_medium=rss",
         "https://www.moneytimes.com.br/materia/?id=77&q=a%20b"),
        ("https://www.marketwatch.com/story/amd-bet-7f8a3b3c?mod=mw_rss_topstories",
         "https://www.marketwatch.com/story/amd-bet-7f8a3b3c"),
        ("https://example.com/a?fbclid=X&gclid=Y&ref=home&cmpid=Z&rss=1&page=2",
         "https://example.com/a?page=2"),
        ("https://example.com/a?UTM_Source=x&Mod=y", "https://example.com/a"),
        # host em minúsculas (caminho preserva caixa), porta padrão removida
        ("HTTPS://Asia.Nikkei.COM:443/Business/Toyota-Plans", "https://asia.nikkei.com/Business/Toyota-Plans"),
        ("http://example.com:80", "https://example.com/"),
        ("//cdn.example.com/x", "https://cdn.example.com/x"),
        ("  https://example.com/x  ", "https://example.com/x"),
        # esquemas não-web ficam como estão (e depois são descartados)
        ("javascript:alert(1)", "javascript:alert(1)"),
        ("", ""),
    ],
)
def test_canonical_url(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://redir.folha.com.br/redir/online/poder/rss091/*https://www1.folha.uol.com.br/poder/x.shtml?utm_source=a",
        "http://WWW.Example.com/a/B?x=1&&utm_term=z#frag",
        "https://www.ft.com/content/abc?syn-25a6b1a6=1&page=3",
        "https://example.com",
    ],
)
def test_canonical_url_is_idempotent(raw: str) -> None:
    once = canonical_url(raw)
    assert canonical_url(once) == once


def test_article_id_is_hash_of_canonical_url() -> None:
    a = "https://valor.globo.com/x.ghtml?utm_source=rss&utm_medium=feed"
    b = "http://VALOR.globo.com/x.ghtml#topo"
    assert article_id(a) == article_id(b) == text.short_hash("https://valor.globo.com/x.ghtml")
    assert len(article_id(a)) == 12
    assert article_id("https://valor.globo.com/y.ghtml") != article_id(a)


@pytest.mark.parametrize(
    "url, width, height, usable",
    [
        ("https://s2-valor.glbimg.com/abc=/1200x/foto.jpg", None, None, True),
        ("http://example.com/foto.jpg", None, None, True),
        ("ftp://example.com/foto.jpg", None, None, False),
        ("data:image/gif;base64,R0lGOD", None, None, False),
        ("https://feeds.feedburner.com/~r/infomoney/~4/abc", None, None, False),
        ("https://pixel.wp.com/b.gif?host=x", None, None, False),
        ("https://example.com/tracking/open.png", None, None, False),
        ("https://example.com/img_1x1.png", None, None, False),
        ("https://example.com/foto.jpg", 1, 1, False),
        ("https://example.com/animacao.gif", None, None, False),
        ("https://example.com/animacao.gif", 80, 60, False),
        ("https://example.com/grafico.gif", 1200, 675, True),
        ("https://f.i.uol.com.br/hunting/folha/1/common/logo-folha.png", None, None, False),
        ("https://site.com/static/share-default.png", None, None, False),
        ("https://site.com/img/facebook-share.jpg", None, None, False),
        ("https://site.com/img/default-image.jpg", None, None, False),
        ("https://images.example.com/google-pixel-10-review.jpg", None, None, True),
    ],
)
def test_is_usable_image_url(url: str, width: int | None, height: int | None, usable: bool) -> None:
    assert is_usable_image_url(url, width, height) is usable


# ── parse_feed: formatos reais ───────────────────────────────────────────────


def test_valor_rss2_media_content_long_html_description() -> None:
    source = make_source()
    articles = parse("valor.xml", source)
    titles = [a.title for a in articles]
    # item de 26/09 (velho) e item do Eu& (/eu-e/, excluído globalmente) ficam de fora
    assert titles == [
        "Brasil tem pouca tradição de aprender com o que deu errado, diz Malan",
        "Anthropic prevê que revolução da IA será mais profunda que industrialização e web",
        "AGU processa bets e pede R$ 1 bilhão por prejuízos à saúde da população e ao SUS",
    ]
    malan = articles[0]
    assert malan.published == "2026-09-29T01:29:25+00:00"
    assert malan.image.startswith("https://s2-valor.glbimg.com/") and malan.image.endswith("/malan.jpg")
    # descrição completa truncada em ~1500 caracteres, em texto puro, sem boilerplate
    assert 1200 < len(malan.summary) <= SUMMARY_MAX_CHARS
    assert malan.summary.endswith("…")
    assert malan.summary.startswith("O ex-ministro da Fazenda Pedro Malan afirmou")
    assert '"pouca tradição"' in malan.summary  # &quot; decodificado
    for unwanted in ("<", "&quot;", "Foto: Claudio Belli", "Leia mais", "Assine"):
        assert unwanted not in malan.summary
    # campos vindos da fonte
    assert (malan.source_id, malan.source_name, malan.lang, malan.weight) == ("valor", "Valor Econômico", "pt", 1.3)
    assert malan.topics == ["brasil"] and malan.topics is not source.topics
    assert malan.feed_url == source.url
    assert all(a.id == article_id(a.url) for a in articles)
    agu = articles[2]
    assert agu.url == ("https://valor.globo.com/politica/noticia/2026/09/28/"
                       "agu-processa-bets-e-pede-r-1-bilho-por-prejuzos-sade-da-populao-e-ao-sus.ghtml")


def test_folha_rss091_iso_8859_1_redirect_links() -> None:
    articles = parse("folha_mercado.xml", make_source(id="folha", name="Folha de S.Paulo",
                                                      url="https://feeds.folha.uol.com.br/mercado/rss091.xml"))
    assert len(articles) == 4  # item de /esporte/ excluído
    assert all(a.url.startswith("https://www1.folha.uol.com.br/") for a in articles)
    assert not any("redir.folha" in a.url or "/esporte/" in a.url for a in articles)
    scooter = by_title(articles, "Scooter")
    assert scooter.title.endswith("usos urbano e rodoviário")  # acentos corretos (ISO-8859-1)
    assert scooter.published == "2026-09-29T02:00:00+00:00"  # 23:00 -0300 → UTC
    motta = by_title(articles, "Motta")
    assert "Câmara" in motta.title and "'sociedade'" in motta.title
    # Folha não traz imagem; o logo dentro da descrição é ignorado
    assert all(a.image is None for a in articles)
    dolar = by_title(articles, "Dólar")
    assert dolar.summary.startswith("A moeda americana avançou 0,19%")


def test_wordpress_content_encoded_jota() -> None:
    articles = parse("jota.xml", make_source(id="jota", name="JOTA", url="https://www.jota.info/feed", weight=1.2,
                                             topics=["juridico"]))
    zanin = by_title(articles, "Zanin")
    # content:encoded (texto completo) vence a description curta
    assert zanin.summary.startswith("O ministro Cristiano Zanin, do Supremo Tribunal Federal")
    assert "plenário virtual" in zanin.summary
    for unwanted in ("apareceu primeiro em", "Leia também", "Crédito: Gustavo Moreno", "<em>"):
        assert unwanted not in zanin.summary
    assert "zero rating" in zanin.summary
    # media:content (1600px) tem prioridade sobre a <img> do conteúdo
    assert zanin.image == "https://images.jota.info/wp-content/uploads/2026/09/zanin-stf-plenario.jpg"
    assert zanin.topics == ["juridico"]


def test_wordpress_infomoney_inline_images_and_boilerplate() -> None:
    articles = parse("infomoney.xml", make_source(id="infomoney", name="InfoMoney"))
    ipo = by_title(articles, "Anthropic")
    assert ipo.image == ("https://www.infomoney.com.br/wp-content/uploads/2026/09/anthropic-ipo-sede.jpg"
                         "?w=1024&quality=70&strip=info")  # &amp; decodificado
    assert ipo.summary.startswith("A Anthropic divulgou")  # "(Reuters) - " removido
    for unwanted in ("appeared first on", "The post", "Leia mais", "(Reuters)"):
        assert unwanted not in ipo.summary
    bets = by_title(articles, "casas de apostas")
    # pixel de rastreamento ignorado; imagem com lazy loading (data-src) aproveitada
    assert bets.image == "https://www.infomoney.com.br/wp-content/uploads/2026/09/comite-bets-planalto.jpg?w=1200"


def test_guardian_picks_widest_media_content_and_filters() -> None:
    articles = parse("guardian_world.xml", make_source(id="guardian", name="The Guardian", lang="en"))
    # só a matéria recente de /world/: a de 27/09 é velha; /football/ e /lifeandstyle/ excluídas
    assert [a.title for a in articles] == [
        "DRC politician beaten to death after radio appearance about Ebola outbreak"]
    drc = articles[0]
    assert "width=460" in drc.image and "&amp;" not in drc.image
    assert "Continue reading" not in drc.summary
    assert drc.summary.endswith("less than two years.")
    assert drc.lang == "en"


def test_scmp_media_content_and_thumbnails() -> None:
    articles = parse("scmp_property.xml", make_source(id="scmp", lang="en"))
    office = by_title(articles, "Hong Kong office")
    assert "/styles/1280x720/" in office.image  # media:content 1280 > thumbnail 768
    homes = by_title(articles, "new-home prices")
    assert "/styles/1020x680/" in homes.image  # maior das duas thumbnails
    assert homes.title.startswith("China’s")


def test_nyt_media_content_and_short_description() -> None:
    articles = parse("nyt_business.xml", make_source(id="nyt", lang="en"))
    assert len(articles) == 3
    assert [a.published for a in articles] == sorted((a.published for a in articles), reverse=True)
    home = by_title(articles, "Homeownership")
    assert home.image.endswith("28biz-birthrate-housing-lqkp-mediumSquareAt3X.jpg")
    assert home.summary.startswith("A proposed federal mortgage program")
    assert "Jim Wilson" not in home.summary  # media:credit não entra no resumo


def test_ft_links_without_syn_param() -> None:
    articles = parse("ft_home.xml", make_source(id="ft", lang="en"))
    assert {a.url for a in articles} == {
        "https://www.ft.com/content/c7685a7e-7745-4cbc-8053-4958d0ea449b",
        "https://www.ft.com/content/488cb467-3cb7-4d06-9a5f-c0749c729d91",
    }
    assert all(a.image and a.image.startswith("https://www.ft.com/__origami/") for a in articles)


def test_bbc_http_link_tracking_and_thumbnail() -> None:
    articles = parse("bbc_world.xml", make_source(id="bbc", lang="en"))
    openai = by_title(articles, "OpenAI")
    assert openai.url == "https://www.bbc.co.uk/news/articles/cm5y5nynl75ko"
    assert openai.image.startswith("https://ichef.bbci.co.uk/ace/standard/240/")
    yemen = by_title(articles, "Yemen")
    assert yemen.url == "https://www.bbc.co.uk/news/articles/cw98005ndz7no"


def test_estadao_empty_description_uses_content_encoded() -> None:
    articles = parse("estadao_economia.xml", make_source(id="estadao", name="Estadão"))
    fuel = by_title(articles, "combustíveis")
    assert fuel.summary.startswith("O Ministério de Minas e Energia publicou")
    assert "Estreito de Ormuz" in fuel.summary
    assert fuel.image.startswith("https://www.estadao.com.br/resizer/v2/ZK4LQYV7BNEWJLY6X3T2QG5H4M.jpg?quality=80&auth=")
    assert "&amp;" not in fuel.image
    opinion = by_title(articles, "moeda importa")
    assert opinion.image is None and len(opinion.summary) > 100


def test_atom_without_dates() -> None:
    articles = parse("nikkei_atom.xml", make_source(id="nikkei", lang="en"))
    # entrada sem link http é descartada; as outras ficam com published=None
    assert len(articles) == 2
    assert all(a.published is None for a in articles)
    toyota = by_title(articles, "Toyota")
    assert toyota.title == "Toyota to invest $10bn in US battery plants"  # tags removidas do título
    assert toyota.url == "https://asia.nikkei.com/Business/Automobiles/Toyota-to-invest-10bn-in-US-battery-plants"
    assert toyota.summary.startswith("The carmaker will expand")


def test_entertainment_and_sports_are_filtered() -> None:
    articles = parse("cnnbrasil.xml", make_source(id="cnnbrasil", name="CNN Brasil", weight=0.7))
    assert [a.title for a in articles] == ["Renegociação de dívidas de adimplentes é prorrogada até 26 de outubro"]


def test_edge_cases_feed() -> None:
    articles = parse("edge_cases.xml", make_source(id="moneytimes", name="Money Times"))
    titles = [a.title for a in articles]
    # sem título, sem link, link javascript: e título só com <script> são descartados;
    # os dois itens do Ibovespa têm a mesma URL canônica e viram um só
    assert len(articles) == 4
    assert all("<" not in t and "script" not in t and "alert" not in t for t in titles)

    ibov = by_title(articles, "Ibovespa")
    assert ibov.title == "Ibovespa fecha em queda com cautela antes do 1º turno"
    assert ibov.url == "https://www.moneytimes.com.br/ibovespa-fecha-em-queda-com-cautela-antes-do-1o-turno/?id=77"
    # ficou o resumo mais longo, sem "(Reuters)", "Leia também" e "Assine"; imagem herdada do duplicado
    assert ibov.summary == ("O Ibovespa recuou 0,26% nesta segunda-feira, a 182.991 pontos, em sessão marcada por "
                            "cautela antes do primeiro turno das eleições.")
    assert ibov.image == "https://www.moneytimes.com.br/uploads/2026/09/b3-painel.gif"

    petro = by_title(articles, "Petrobras")
    assert petro.title == "Petrobras aprova pagamento de R$ 12 bilhões em dividendos intermediários"
    assert petro.published == NOW.isoformat()  # data no futuro (30/09) → now
    assert petro.url == "https://www.moneytimes.com.br/petrobras-aprova-dividendos-intermediarios/"
    assert petro.image == "https://www.moneytimes.com.br/uploads/2026/09/petrobras-sede.jpg"  # enclosure image/jpeg

    vale = by_title(articles, "Vale")
    assert vale.published == "2026-09-29T08:40:00+00:00"  # até 1 h no futuro: mantém
    assert vale.image is None  # enclosure de áudio não é imagem

    podcast = by_title(articles, "Podcast")
    assert podcast.published is None
    assert podcast.image == "https://www.moneytimes.com.br/uploads/2026/09/copom-thumb.jpg"  # vídeo ignorado
    assert articles[-1] is podcast  # itens sem data vão para o fim


def test_script_in_title_never_survives_even_double_escaped() -> None:
    content = rss([
        rss_item("Alta &amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt; do dólar", "https://ex.com/a", NOW),
        rss_item("<![CDATA[<img src=x onerror=alert(1)>Juros sobem]]>", "https://ex.com/b", NOW),
    ])
    articles = parse_feed(content, make_source(), now=NOW, max_age_hours=30, exclude=[])
    assert sorted(a.title for a in articles) == ["Alta do dólar", "Juros sobem"]


def test_max_age_boundaries() -> None:
    content = rss([
        rss_item("Dentro da janela", "https://ex.com/in", NOW - timedelta(hours=29, minutes=59)),
        rss_item("Fora da janela", "https://ex.com/out", NOW - timedelta(hours=30, minutes=1)),
        rss_item("Sem data", "https://ex.com/nodate", None),
    ])
    articles = parse_feed(content, make_source(), now=NOW, max_age_hours=30, exclude=[])
    assert [a.title for a in articles] == ["Dentro da janela", "Sem data"]
    short = parse_feed(content, make_source(), now=NOW, max_age_hours=6, exclude=[])
    assert [a.title for a in short] == ["Sem data"]


def test_limit_of_items_per_feed_keeps_most_recent() -> None:
    items = [rss_item(f"Notícia número {i}", f"https://ex.com/n{i}", NOW - timedelta(minutes=10 * i))
             for i in range(MAX_ITEMS_PER_FEED + 30)]
    items.reverse()  # feed fora de ordem
    articles = parse_feed(rss(items), make_source(), now=NOW, max_age_hours=30, exclude=[])
    assert len(articles) == MAX_ITEMS_PER_FEED
    assert articles[0].title == "Notícia número 0"
    assert articles[-1].title == f"Notícia número {MAX_ITEMS_PER_FEED - 1}"


def test_exclude_patterns_match_canonical_url_case_insensitive() -> None:
    content = rss([
        rss_item("Via redirecionador", "https://redir.folha.com.br/redir/online/x/rss091/*https://www1.folha.uol.com.br/Esporte/a.shtml", NOW),
        rss_item("Patrocinado", "https://ex.com/patrocinado/b", NOW),
        rss_item("Normal", "https://ex.com/economia/c", NOW),
    ])
    source = make_source(exclude_url_patterns=["/patrocinado/"])
    articles = parse_feed(content, source, now=NOW, max_age_hours=30, exclude=["/esporte/", *source.exclude_url_patterns])
    assert [a.title for a in articles] == ["Normal"]


def test_naive_now_is_treated_as_utc() -> None:
    articles = parse("valor.xml", now=NOW.replace(tzinfo=None))
    assert len(articles) == 3


@pytest.mark.parametrize("name", ["broken.xml", "empty_channel.xml"])
def test_unusable_feed_returns_empty_list(name: str) -> None:
    assert parse(name) == []


def test_empty_bytes_return_empty_list() -> None:
    assert parse_feed(b"   ", make_source(), now=NOW, max_age_hours=30, exclude=[]) == []


def test_truncated_xml_keeps_recoverable_items() -> None:
    articles = parse("truncated.xml", make_source(id="neofeed", name="NeoFeed"))
    assert [a.title for a in articles] == [
        "Tellus e Rio Bravo reciclam portfólio e miram fundo logístico de R$ 10 bilhões",
        "Consolidação começa a chacoalhar o mercado de carros elétricos na China",
    ]


def test_summary_is_title_only_becomes_empty() -> None:
    content = rss([rss_item("Copom mantém a Selic", "https://ex.com/a", NOW, "<p>Copom mantém a Selic</p>")])
    [article] = parse_feed(content, make_source(), now=NOW, max_age_hours=30, exclude=[])
    assert article.summary == ""


def test_relative_image_resolved_against_feed_url() -> None:
    item = ("<item><title>Com imagem relativa</title><link>https://ex.com/a</link>"
            "<description><![CDATA[<img src=\"/uploads/foto.jpg\"> Texto]]></description></item>")
    [article] = parse_feed(rss([item]), make_source(url="https://ex.com/feed/"), now=NOW, max_age_hours=30, exclude=[])
    assert article.image == "https://ex.com/uploads/foto.jpg"


# ── collect_feeds ────────────────────────────────────────────────────────────


def test_collect_feeds_statuses_and_isolated_failures() -> None:
    sources = [
        make_source(url="https://valor.globo.com/rss/valor/"),
        make_source(url="https://valor.globo.com/rss/valor/financas/", topics=["mercados"]),
        make_source(id="folha", name="Folha", url="https://feeds.folha.uol.com.br/mercado/rss091.xml"),
        make_source(id="wsj", name="WSJ", url="https://feeds.content.dowjones.io/public/rss/RSSWorldNews"),
        make_source(id="imobireport", name="Imobi Report", url="https://www.imobireport.com.br/feed/"),
        make_source(id="secovi", name="Secovi-SP", url="https://www.secovi.com.br/feed"),
        make_source(id="im", name="InfoMoney", url="https://www.infomoney.com.br/mercados/feed/"),
        make_source(id="boom", name="Boom", url="https://boom.example.com/feed"),
        make_source(id="cert", name="STF", url="https://portal.stf.jus.br/rss/noticias.asp"),
    ]
    fetch = FakeFetch({
        sources[0].url: feed_bytes("valor.xml"),
        sources[1].url: feed_bytes("valor.xml"),
        sources[2].url: feed_bytes("folha_mercado.xml"),
        sources[3].url: net.FetchError(sources[3].url, "HTTP 403", status=403),
        sources[4].url: net.FetchError(sources[4].url, "TimeoutError: The read operation timed out"),
        sources[5].url: feed_bytes("broken.xml"),
        sources[6].url: feed_bytes("empty_channel.xml"),
        sources[7].url: RuntimeError("bug inesperado"),
        sources[8].url: net.FetchError(sources[8].url, "URLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
    })
    articles, statuses = collect_feeds(sources, now=NOW, max_age_hours=30, global_exclude=GLOBAL_EXCLUDE,
                                       fetch=fetch, max_workers=4)

    assert [s.url for s in statuses] == [s.url for s in sources]  # um status por URL, na ordem
    summary = {s.url: (s.ok, s.items, s.error) for s in statuses}
    assert summary[sources[0].url] == (True, 3, None)
    assert summary[sources[1].url] == (True, 3, None)
    assert summary[sources[2].url] == (True, 4, None)
    assert summary[sources[3].url] == (False, 0, "HTTP 403")
    assert summary[sources[4].url] == (False, 0, "timeout")
    assert summary[sources[5].url] == (False, 0, "XML inválido")
    assert summary[sources[6].url] == (False, 0, "0 itens")
    assert summary[sources[7].url] == (False, 0, "erro inesperado: RuntimeError")
    assert summary[sources[8].url] == (False, 0, "erro de certificado SSL")
    assert all(isinstance(s.elapsed_ms, int) and s.elapsed_ms >= 0 for s in statuses)
    assert statuses[3].source_id == "wsj" and statuses[3].source_name == "WSJ"

    # artigos não são deduplicados entre feeds: o mesmo item aparece pelos dois feeds do Valor
    assert len(articles) == 3 + 3 + 4
    assert {a.feed_url for a in articles} == {sources[0].url, sources[1].url, sources[2].url}
    assert len(dedupe_articles(articles)) == 3 + 4
    # parâmetros de rede enviados ao fetch
    assert all(kwargs == {"timeout": feeds.FEED_TIMEOUT, "retries": feeds.FEED_RETRIES} for _, kwargs in fetch.calls)


def test_collect_feeds_skips_disabled_and_repeated_urls() -> None:
    url = "https://valor.globo.com/rss/valor/"
    sources = [make_source(url=url), make_source(url=url, topics=["mercados"]),
               make_source(url="https://off.example.com/feed", enabled=False)]
    fetch = FakeFetch({url: feed_bytes("valor.xml")})
    articles, statuses = collect_feeds(sources, now=NOW, max_age_hours=30, global_exclude=[], fetch=fetch)
    assert [s.url for s in statuses] == [url]
    assert [call[0] for call in fetch.calls] == [url]
    # sem exclusões globais, o item do Eu& volta a aparecer
    assert statuses[0].items == 4 and len(articles) == 4


def test_collect_feeds_with_real_config_sources() -> None:
    config = load_config(env={})
    fetch = FakeFetch({s.url: feed_bytes("nyt_business.xml") for s in config.sources})
    articles, statuses = collect_feeds(config.sources, now=NOW, max_age_hours=config.edition.max_age_hours,
                                       global_exclude=config.edition.exclude_url_patterns, fetch=fetch)
    assert len(statuses) == len({s.url for s in config.sources}) == len(config.sources)
    assert all(s.ok and s.items == 3 for s in statuses)
    assert len(articles) == 3 * len(config.sources)
    assert {a.source_id for a in articles} == {s.id for s in config.sources}


def test_collect_feeds_with_no_sources() -> None:
    assert collect_feeds([], now=NOW, max_age_hours=30, global_exclude=[], fetch=FakeFetch({})) == ([], [])


# ── dedupe_articles ──────────────────────────────────────────────────────────


def make_article(**overrides) -> Article:
    base = dict(id="abc123abc123", url="https://ex.com/a", title="Título", summary="Resumo curto",
                source_id="valor", source_name="Valor", lang="pt", published="2026-09-29T01:00:00+00:00",
                image=None, topics=["brasil"], weight=1.0, feed_url="https://valor.globo.com/rss/valor/")
    base.update(overrides)
    return Article(**base)


def test_dedupe_prefers_higher_weight_and_merges() -> None:
    low = make_article(weight=0.8, summary="Resumo bem mais longo que o outro", image="https://ex.com/i.jpg",
                       topics=["mercados"])
    high = make_article(weight=1.3, summary="Curto", topics=["brasil", "mercados"], published=None)
    other = make_article(id="zzz", url="https://ex.com/b")
    result = dedupe_articles([low, other, high])
    assert [a.id for a in result] == ["abc123abc123", "zzz"]  # ordem da primeira ocorrência
    kept = result[0]
    assert kept.weight == 1.3 and kept.summary == "Curto"
    assert kept.image == "https://ex.com/i.jpg"  # herdada
    assert kept.published == "2026-09-29T01:00:00+00:00"  # herdada
    assert kept.topics == ["brasil", "mercados"]
    # entradas não são alteradas
    assert high.image is None and high.published is None and low.topics == ["mercados"]


def test_dedupe_tie_prefers_longer_summary_and_unions_topics() -> None:
    first = make_article(summary="Curto", topics=["brasil"], image="https://ex.com/1.jpg")
    second = make_article(summary="Resumo mais longo", topics=["politica"], image="https://ex.com/2.jpg")
    [kept] = dedupe_articles([first, second])
    assert kept.summary == "Resumo mais longo"
    assert kept.image == "https://ex.com/2.jpg"  # o escolhido mantém a própria imagem
    assert kept.topics == ["politica", "brasil"]


def test_dedupe_keeps_distinct_ids_and_is_noop_without_duplicates() -> None:
    articles = [make_article(id=f"id{i}", url=f"https://ex.com/{i}") for i in range(5)]
    assert dedupe_articles(articles) == articles
    assert dedupe_articles([]) == []
    copy = dedupe_articles(articles)[0]
    copy.topics.append("mundo")
    assert articles[0].topics == ["brasil"]  # cópia independente


def test_malformed_entry_does_not_invalidate_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = feeds._entry_image
    calls = {"n": 0}

    def flaky_image(entry):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("estrutura inesperada")
        return original(entry)

    monkeypatch.setattr(feeds, "_entry_image", flaky_image)
    assert len(parse("valor.xml")) == 2


def test_feed_with_all_items_filtered_is_ok_with_zero_items() -> None:
    source = make_source(id="guardian", url="https://www.theguardian.com/world/rss", lang="en")
    fetch = FakeFetch({source.url: feed_bytes("guardian_world.xml")})
    articles, [status] = collect_feeds([source], now=NOW, max_age_hours=1, global_exclude=[], fetch=fetch)
    assert articles == []
    assert (status.ok, status.items, status.error) == (True, 0, None)


# ── regressões da edição real de 29/09/2026 ─────────────────────────────────

VALOR_PAYWALL = "Matéria exclusiva para assinantes. Para ter acesso completo, acesse o link da matéria e faça o seu cadastro."


@pytest.mark.parametrize(
    "summary, expected",
    [
        # rodapé do Valor numa linha própria
        (f"A Anthropic planeja alertar investidores sobre riscos da IA.\n{VALOR_PAYWALL}",
         "A Anthropic planeja alertar investidores sobre riscos da IA."),
        # colado na última frase
        (f"Fachin quer julgar o caso rapidamente de forma colegiada. {VALOR_PAYWALL}",
         "Fachin quer julgar o caso rapidamente de forma colegiada."),
        # só a primeira frase do aviso
        ("A Nvidia lança o maior programa de recompra já anunciado. Matéria exclusiva para assinantes.",
         "A Nvidia lança o maior programa de recompra já anunciado."),
        # resumo que era só paywall
        (VALOR_PAYWALL, ""),
        # JOTA
        ("O Confaz decide sobre ICMS.\nEsta reportagem foi antecipada a assinantes JOTA PRO Tributos em 24/9. "
         "Conheça a plataforma do JOTA de monitoramento tributário\nPara ela, isso importa.",
         "O Confaz decide sobre ICMS.\nPara ela, isso importa."),
    ],
)
def test_paywall_footers_are_removed_from_summaries(summary: str, expected: str) -> None:
    assert feeds.clean_summary(summary) == expected


@pytest.mark.parametrize(
    "line",
    [
        "✅ Clique e siga o canal do g1 GO no WhatsApp",
        "✅ Siga o canal de notícias internacionais do g1 no WhatsApp",
        "📱Favorite o g1 no Google e acompanhe as principais notícias do dia",
        "Clique aqui para seguir o canal de Loterias do g1 no WhatsApp",
        "🗒️ Tem alguma sugestão de reportagem? Mande para o g1",
        "🗒️ Tem alguma sugestão de reportagem? Envie para o g1",
        "Agora no g1",
        "VÍDEOS: agora no g1",
        "Volte ao topo",
        "Seguir leyendo",
        "Conheça o JOTA PRO Eleições, cobertura eleitoral especial do JOTA que oferece transparência e previsibilidade para empresas",
        "Quer receber reportagens do JOTA com mais frequência? Clique aqui e selecione o JOTA como fonte de informação no Google Notícias",
        "Follow the day’s news live",
        "Get our breaking news email, free app or daily news podcast",
        "Initial plugin text",
    ],
)
def test_portal_boilerplate_lines_are_removed(line: str) -> None:
    summary = f"Primeiro parágrafo da notícia.\n{line}\nSegundo parágrafo da notícia."
    assert feeds.clean_summary(summary) == "Primeiro parágrafo da notícia.\nSegundo parágrafo da notícia."


def test_g1_call_to_action_glued_to_the_next_paragraph() -> None:
    summary = (
        "A decisão foi tomada após testes internos.\n📱Favorite o g1 no Google e acompanhe as principais notícias "
        "do dia O diretor-executivo da OpenAI, Sam Altman, falou."
    )
    assert feeds.clean_summary(summary) == (
        "A decisão foi tomada após testes internos. O diretor-executivo da OpenAI, Sam Altman, falou."
    )


def test_abr_related_news_block_is_removed_but_prose_is_kept() -> None:
    summary = (
        "O dólar subiu.\nNotícias relacionadas:\nMercado eleva projeção de inflação para 4,99% e reduz PIB.\n"
        "Fazenda mantém subsídio de R$ 2,12 ao óleo diesel.\n"
        "O petróleo também terminou o dia em alta, embora distante das máximas alcançadas durante a sessão."
    )
    assert feeds.clean_summary(summary) == (
        "O dólar subiu.\nO petróleo também terminou o dia em alta, embora distante das máximas alcançadas durante a sessão."
    )


def test_photo_captions_and_credits_are_removed() -> None:
    assert feeds.clean_summary("Texto sobre o ouro.\nBarras de ouro\nPixabay") == "Texto sobre o ouro."
    assert feeds.clean_summary("Quaest, 1º turno: região\nArte/g1\nO levantamento ouviu 2 mil eleitores.") == (
        "O levantamento ouviu 2 mil eleitores."
    )
    assert feeds.clean_summary("Fato.\nAidan Howe/Pixabay") == "Fato."
    # linha com barra que é conteúdo (tem dígito ou é longa) fica
    assert feeds.clean_summary("Fato.\nPreço médio: R$ 5/kg") == "Fato.\nPreço médio: R$ 5/kg"


def test_g1_first_line_caption_or_related_headline_is_dropped() -> None:
    caption = "O logotipo da OpenAI é visto em um celular\nAP/Michael Dwyer, Arquivo\nA OpenAI afirmou nesta segunda."
    assert feeds.clean_summary(caption, source_id="g1") == "A OpenAI afirmou nesta segunda."
    related = "Greve da Caixa: veja como ficam pagamentos do Bolsa Família\nO TST analisa o dissídio nesta terça."
    assert feeds.clean_summary(related, source_id="g1") == "O TST analisa o dissídio nesta terça."
    # só no g1: em outro veículo a primeira linha sem ponto final pode ser conteúdo
    assert feeds.clean_summary(related, source_id="valor").startswith("Greve da Caixa")
    # item do g1 que começa com a foto: a legenda some mesmo com pontuação
    html = '<img src="https://s2.glbimg.com/x.jpg"><br>Foto do evento.<p>O texto de verdade começa aqui.</p>'
    assert feeds.clean_summary(html, source_id="g1") == "O texto de verdade começa aqui."


def test_continue_reading_is_only_removed_as_a_trailing_link() -> None:
    assert feeds.clean_summary("Investors continue reading the Fed minutes.") == "Investors continue reading the Fed minutes."
    assert feeds.clean_summary("Continue reading the minutes.") == "Continue reading the minutes."
    assert feeds.clean_summary("It took two years. Continue reading...") == "It took two years."
    assert feeds.clean_summary("x. Continue reading…\nnext line") == "x.\nnext line"


def test_abr_tracking_pixel_is_never_the_photo() -> None:
    assert not is_usable_image_url("https://agenciabrasil.ebc.com.br/ebc.png?id=1703575&o=rss")
    html = (
        '<img src="https://agenciabrasil.ebc.com.br/ebc.png?id=1&amp;o=rss" width="1px" height="1px">'
        '<img src="https://agenciabrasil.ebc.com.br/sites/default/files/foto.jpg">'
    )
    assert feeds._first_html_image([html]) == "https://agenciabrasil.ebc.com.br/sites/default/files/foto.jpg"
    # dimensões com unidade ou só no style também valem
    assert feeds._to_int("1px") == 1 and feeds._to_int("300.0") == 300 and feeds._to_int(None) is None
    styled = '<img src="https://ex.com/p.png" style="width:1px;height:1px"><img src="https://ex.com/foto.jpg">'
    assert feeds._first_html_image([styled]) == "https://ex.com/foto.jpg"


def test_site_exclusions_cover_sponsored_service_and_sports_without_false_positives() -> None:
    config = load_config(env={})
    urls = {
        "https://neofeed.com.br/negocios/esporte-movimenta-bilhoes/": True,  # "/esporte" era falso positivo
        "https://ex.com/patrocinado/banco-x-lanca-conta": False,
        "https://g1.globo.com/loterias/noticia/mega-sena.ghtml": False,
        "https://g1.globo.com/esporte/futebol/jogo.ghtml": False,
        "https://www.bbc.com/news/videos/c1234": False,
        "https://g1.globo.com/economia/noticia/bbbrasil-fundo.ghtml": True,  # "/bbb" era falso positivo
    }
    for url, kept in urls.items():
        assert (not feeds._is_excluded(canonical_url(url), config.edition.exclude_url_patterns)) is kept, url

    pattern = feeds.compile_title_patterns(config.edition.exclude_title_patterns)
    service = [
        "Candidatos a deputado estadual no Mato Grosso do Sul (MS): veja número e nome na lista de 2026",
        "Mega-Sena: resultado do concurso 3064",
        "Dia de Sorte: confira o resultado do concurso 1309",
        "Horóscopo do dia",
        "Onde assistir ao jogo do Brasil",
    ]
    assert all(feeds.is_excluded_title(t, pattern) for t in service)
    assert not feeds.is_excluded_title("Ibovespa ao vivo: bolsa cai com cautela", pattern)
    assert not feeds.is_excluded_title("Candidatos ao Senado prometem reforma tributária", pattern)


def test_sponsored_items_are_dropped_by_the_collector() -> None:
    content = rss([
        rss_item("Global markets outlook", "https://www.scmp.com/presented/x", NOW,
                 "[The content of this article has been produced by our advertising partner.] Markets..."),
        rss_item("Advertorial sem marcação na URL", "https://www.scmp.com/news/y", NOW,
                 "[The content of this article has been produced by our advertising partner.]<p>Texto.</p>"),
        rss_item("Notícia normal", "https://www.scmp.com/news/z", NOW),
    ])
    articles = parse_feed(content, make_source(), now=NOW, max_age_hours=30, exclude=GLOBAL_EXCLUDE)
    assert [a.title for a in articles] == ["Notícia normal"]


def test_title_exclusions_apply_at_collection() -> None:
    content = rss([
        rss_item("Candidatos a deputado federal em SP: veja número e nome", "https://g1.globo.com/a", NOW),
        rss_item("Copom mantém a Selic", "https://g1.globo.com/b", NOW),
    ])
    pattern = feeds.compile_title_patterns(load_config(env={}).edition.exclude_title_patterns)
    articles = parse_feed(content, make_source(), now=NOW, max_age_hours=30, exclude=[], exclude_titles=pattern)
    assert [a.title for a in articles] == ["Copom mantém a Selic"]


def test_traffic_source_is_a_tracking_param() -> None:
    assert canonical_url("https://www.cnnbrasil.com.br/x/?traffic_source=rss&id=3") == "https://www.cnnbrasil.com.br/x/?id=3"


def test_general_feeds_get_the_section_hint_from_the_url() -> None:
    capa = make_source(topics=[])
    content = rss([
        rss_item("Trump fala sobre a China", "https://www.cnnbrasil.com.br/internacional/trump-china/", NOW),
        rss_item("Dino envia caso ao plenário", "https://valor.globo.com/politica/noticia/2026/09/28/x.ghtml", NOW),
        rss_item("Sem seção na URL", "https://www.aljazeera.com/news/2026/9/29/x", NOW),
    ])
    articles = parse_feed(content, capa, now=NOW, max_age_hours=30, exclude=[])
    assert [a.topics for a in articles] == [["mundo"], ["politica"], []]
    # feed com topics definidos mantém os seus
    assert parse_feed(content, make_source(topics=["brasil"]), now=NOW, max_age_hours=30, exclude=[])[0].topics == ["brasil"]


def test_saved_bundles_are_cleaned_with_the_current_rules() -> None:
    dirty = Article(
        id="x", url="https://valor.globo.com/brasil/noticia/x.ghtml", title="Dívida pública sobe",
        summary=f"A dívida subiu 0,04%.\n{VALOR_PAYWALL}", source_id="valor", source_name="Valor Econômico",
        lang="pt", image="https://agenciabrasil.ebc.com.br/ebc.png?id=1&o=rss",
    )
    service = Article(
        id="y", url="https://g1.globo.com/politica/eleicoes/x.ghtml", title="Candidatos no MS: veja número e nome",
        summary="Lista.", source_id="g1", source_name="g1", lang="pt",
    )
    config = load_config(env={})
    cleaned = feeds.sanitize_articles(
        [dirty, service],
        exclude_url_patterns=config.edition.exclude_url_patterns,
        exclude_title_patterns=config.edition.exclude_title_patterns,
    )
    assert [a.id for a in cleaned] == ["x"]
    assert cleaned[0].summary == "A dívida subiu 0,04%." and cleaned[0].image is None
    assert dirty.summary.endswith("cadastro.")  # não altera o original


def test_related_headline_runs_without_header_are_removed() -> None:
    """Valor: chamadas para outras matérias no meio do resumo, sem "Leia também"."""
    summary = (
        "O presidente do TSE reclamou de interferência do STF.\n"
        "Quaest: Lula retoma liderança no 1º turno e mantém empate com Flávio no 2º\n"
        "Gilmar quer suspender análise sobre Moraes no caso Master\n"
        "“Venho manifestar minha elevada preocupação”, diz Nunes Marques no ofício."
    )
    assert feeds.clean_summary(summary) == (
        "O presidente do TSE reclamou de interferência do STF.\n"
        "“Venho manifestar minha elevada preocupação”, diz Nunes Marques no ofício."
    )
    # listas com pontuação (cotações) e itens que continuam a frase ficam
    quotes = "Confira os números:\nDólar à vista: R$ 5,226 (+0,83%);\nIbovespa: 182.991 pontos (-0,26%)."
    assert feeds.clean_summary(quotes) == quotes
    items = "Os eleitos:\nKast venceu o segundo turno chileno em 2025,\nEspriella venceu a disputa colombiana em 2026, e\nFim."
    assert feeds.clean_summary(items) == items
    # o subtítulo do Guardian (sem ponto) fica; a chamada para a newsletter sai
    guardian = "Outrage over treatment of alleged offenders has grown\nSign up for the Breaking News US newsletter email\nText."
    assert feeds.clean_summary(guardian) == "Outrage over treatment of alleged offenders has grown\nText."
