"""Integração no modo "online" com os coletores reais e um ``fetch`` falso.

Os feeds da configuração são servidos a partir de ``tests/fixtures/coleta``
(RSS/Atom reais, cotações e clima); o resto falha como na vida real (HTTP 403,
timeout, XML quebrado, canal vazio). Verifica os critérios de aceite:

- falhas parciais de rede nunca derrubam a edição;
- falha total não sobrescreve a edição anterior (e a coleta crua fica salva).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qijournal import net, pipeline
from qijournal.collect.market import BCB_SGS_URL, COINGECKO_URL, YAHOO_URL
from qijournal.collect.weather import forecast_url
from tests.fixtures.pipeline.factory import make_config

COLETA = Path(__file__).parent / "fixtures" / "coleta"
NOW = datetime(2026, 9, 29, 8, 7, tzinfo=timezone.utc)

FEEDS = {  # URL do feed na configuração → fixture
    "https://valor.globo.com/rss/valor/": "valor.xml",
    "https://feeds.folha.uol.com.br/mercado/rss091.xml": "folha_mercado.xml",
    "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/": "estadao_economia.xml",
    "https://www.infomoney.com.br/feed/": "infomoney.xml",
    "https://www.jota.info/feed": "jota.xml",
    "https://www.cnnbrasil.com.br/feed/": "cnnbrasil.xml",
    "https://www.ft.com/rss/home": "ft_home.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml": "nyt_business.xml",
    "https://www.theguardian.com/world/rss": "guardian_world.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml": "bbc_world.xml",
    "https://www.scmp.com/rss/96/feed": "scmp_property.xml",
    "https://www.moneytimes.com.br/feed/": "edge_cases.xml",  # <script> no título, itens inválidos
    "https://www.conjur.com.br/rss.xml": "broken.xml",  # XML inválido
    "https://g1.globo.com/rss/g1/economia/": "empty_channel.xml",  # 0 itens
}
MARKET = {
    YAHOO_URL.format(symbol="USDBRL%3DX"): "yahoo_usdbrl.json",
    YAHOO_URL.format(symbol="EURBRL%3DX"): "yahoo_eurbrl.json",
    YAHOO_URL.format(symbol="CNYBRL%3DX"): "yahoo_cnybrl.json",
    YAHOO_URL.format(symbol="%5EBVSP"): "yahoo_bvsp.json",
    YAHOO_URL.format(symbol="IFIX.SA"): "yahoo_ifix.json",
    YAHOO_URL.format(symbol="%5EGSPC"): "yahoo_gspc.json",
    YAHOO_URL.format(symbol="BZ%3DF"): "yahoo_bzf.json",
    YAHOO_URL.format(symbol="GC%3DF"): "yahoo_gcf.json",
    YAHOO_URL.format(symbol="BTC-USD"): "yahoo_btc_error.json",
    COINGECKO_URL.format(coin="bitcoin"): "coingecko_bitcoin.json",
    BCB_SGS_URL.format(series=432, n=2): "bcb_432_selic.json",
    BCB_SGS_URL.format(series=13522, n=2): "bcb_13522_ipca12m.json",
    BCB_SGS_URL.format(series=189, n=12): "bcb_189_igpm.json",
    BCB_SGS_URL.format(series=192, n=12): "bcb_192_incc.json",
}


class Internet:
    """``fetch`` falso: fixtures para as URLs conhecidas, falhas variadas para o resto."""

    def __init__(self, config, *, down: bool = False) -> None:
        self.down = down
        self.routes: dict[str, Path] = {url: COLETA / "feeds" / name for url, name in FEEDS.items()}
        self.routes.update({url: COLETA / "market" / name for url, name in MARKET.items()})
        for city, name in zip(config.weather, ("openmeteo_sao_paulo.json", "openmeteo_montevideo.json"), strict=True):
            self.routes[forecast_url(city)] = COLETA / "weather" / name
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, **kwargs) -> net.Response:
        with self._lock:
            self.calls.append(url)
            n = len(self.calls)
        if self.down:
            raise net.FetchError(url, "URLError: <urlopen error [Errno -3] Temporary failure in name resolution>")
        path = self.routes.get(url)
        if path is None:  # feeds sem fixture e páginas de matérias
            if n % 3 == 0:
                raise net.FetchError(url, "TimeoutError: timed out")
            raise net.FetchError(url, "HTTP 403", status=403)
        kind = "application/json" if path.suffix == ".json" else "application/rss+xml"
        return net.Response(url=url, status=200, content=path.read_bytes(), headers={"content-type": kind})


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch):
    for key in ("ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "GITHUB_ACTIONS", "QIJ_NO_LLM"):
        monkeypatch.delenv(key, raising=False)
    # A "internet" de teste tem 14 feeds com conteúdo (de 64): mínimos na escala dela.
    return make_config()


def test_partial_network_failures_still_publish_a_complete_edition(tmp_path: Path, config):
    internet = Internet(config)
    result = pipeline.run(config, out_dir=tmp_path, now=NOW, fetch=internet, send_email=False, env={})
    edition = result.edition

    # coleta: cada feed independente; erros curtos e legíveis
    assert edition.stats.sources_total == len({s.url for s in config.sources})
    assert edition.stats.sources_ok == len(FEEDS) - 2
    errors = {f["error"] for f in edition.stats.sources_failed}
    assert {"HTTP 403", "timeout", "XML inválido", "0 itens"} <= errors
    assert len(edition.quotes) == len(config.market)  # BTC pelo fallback (CoinGecko)
    assert len(edition.weather) == 2
    assert edition.stats.articles_collected >= config.edition.min_articles

    # edição completa em modo automático (sem chave) e publicada
    assert edition.mode == "heuristic" and len(edition.stories) >= 8 and len(edition.sections) >= 4
    assert any("feeds com erro" in w for w in result.warnings)
    for key in ("index", "edition", "data", "email_html", "email_text", "latest", "archive", "bundle"):
        assert result.outputs[key].is_file(), key
    assert result.outputs["bundle"] == tmp_path / "build" / "bundle-2026-09-29.json"
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert f"{edition.stats.sources_ok} de {edition.stats.sources_total} feeds ok" in page
    # HTML de feed (inclusive <script> escapado duas vezes no título) nunca chega à edição nem à página
    bundle = pipeline.load_bundle(result.outputs["bundle"])
    titles = [a.title for a in bundle.articles]
    assert "Ibovespa fecha em queda com cautela antes do 1º turno" in titles
    assert not any(c in t for t in titles for c in "<>") and not any("alert(1)" in t for t in titles)
    for html in (page, (tmp_path / "edicoes" / "email.html").read_text(encoding="utf-8")):
        assert "<script>alert" not in html and "alert(1)" not in html
        assert "javascript:" not in html

    # o enriquecimento das páginas usou o mesmo fetch (e tolerou as falhas)
    article_urls = {a.url for a in bundle.articles}
    assert article_urls & set(internet.calls)


def test_total_network_failure_keeps_the_previous_edition(tmp_path: Path, config):
    first = pipeline.run(config, out_dir=tmp_path, now=NOW, fetch=Internet(config), send_email=False, env={})
    published = {p: p.read_bytes() for p in first.outputs.values() if p.parent != tmp_path / "build"}

    with pytest.raises(pipeline.InsufficientData) as info:
        pipeline.run(
            config,
            out_dir=tmp_path,
            now=datetime(2026, 9, 30, 8, 7, tzinfo=timezone.utc),
            fetch=Internet(config, down=True),
            send_email=False,
            env={},
        )
    assert info.value.articles == 0 and info.value.sources_ok == 0
    for path, content in published.items():  # nada da edição anterior foi tocado
        assert path.read_bytes() == content, path
    assert not (tmp_path / "edicoes" / "2026-09-30.html").exists()
    latest = json.loads((tmp_path / "edicoes" / "latest.json").read_text(encoding="utf-8"))
    assert latest["date"] == "2026-09-29"
    # a coleta crua do dia com problema fica salva para diagnóstico
    saved = json.loads((tmp_path / "build" / "bundle-2026-09-30.json").read_text(encoding="utf-8"))
    assert saved["articles"] == [] and not any(s["ok"] for s in saved["sources"])
