"""Mercado em linha (§4.8): o slide do Em 1 minuto e o substituto da noite (sem Chromium)."""

from __future__ import annotations

import functools
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from qijournal.config import load_config
from qijournal.instagram import conteudo, estatistica as E, gerar, redacao
from qijournal.instagram.formatos import mercado as M
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-26.json").is_file(), reason="sem os JSONs reais em data/")


@functools.lru_cache(maxsize=None)
def edicao(day: str) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day}.json").read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def historia(day: str) -> list[Edition]:
    return E.load_history(DATA, date(2026, 10, int(day)), 90)


def _sintetica(day: date, quotes: list[dict]) -> Edition:
    return Edition.from_dict(
        {
            "date": day.isoformat(), "date_label": day.isoformat(), "generated_at": f"{day}T09:00:00+00:00",
            "mode": "ai", "model": None, "editorial": "", "briefing": [], "lead": "", "secondary": [],
            "highlights": [], "sections": [], "stories": [], "quotes": quotes, "weather": [], "compared": [], "index": [],
        }
    )


def _quote(qid: str, value: float, change: float | None, as_of: datetime) -> dict:
    return {"id": qid, "label": {"ibov": "Ibovespa", "usd": "Dólar"}.get(qid, qid), "value": value, "display": "",
            "change_pct": change, "kind": "index", "as_of": as_of.isoformat(), "source": "Yahoo Finance"}


def _render(slide, kind: str = "image") -> str:
    env = gerar.environment()
    brand = gerar.brand_context(load_config(ROOT))
    return env.get_template(f"{slide.template}.html.j2").render(
        brand=brand, s=conteudo.polish(slide.ctx), page=7, pages=8, kind=kind, slot="manha"
    )


@real
def test_slide_de_21_10_titulo_paineis_e_faixa():
    s = M.mercado_slide(edicao("21"), historia("21"))
    c = s.ctx
    assert s.template == "mercado_linha"
    assert (c["tema"], c["cor"], c["contador"], c["selo"]) == ("t-dark", "em1min", False, "MERCADO EM LINHA · QUA 21 OUT")
    assert (c["titulo"], c["destaque"]) == ("Ibovespa +13,8% desde 28/09", "+13,8%")
    assert [p["id"] for p in c["paineis"]] == ["ibov", "usd", "brent", "btc"]
    p = {x["id"]: x for x in c["paineis"]}
    assert (p["ibov"]["valor"], p["ibov"]["selo"], p["ibov"]["change"], p["ibov"]["trend"]) == ("208.296", "fech. 20/10", "+1,57%", "up")
    assert (p["usd"]["valor"], p["usd"]["selo"], p["usd"]["acumulado"]) == ("R$ 5,01", "leitura das 06h00", "−4,1% desde 28/09")
    assert (p["brent"]["valor"], p["brent"]["acumulado"]) == ("US$ 110,14", "+10,1% desde 29/09")
    assert p["btc"]["valor"] == "US$ 83.366"
    assert all(str(x["grafico"]).startswith("<svg class='graf graf-linha'") for x in c["paineis"])
    assert c["faixa"] == "Selic 13,75% · IPCA 12m 4,58% (set) · juro real ex-post 8,77%"
    assert c["pe"].startswith("Yahoo Finance e BCB · uma leitura por dia")


@real
def test_alt_tem_os_valores_e_a_frase_de_cada_grafico():
    alt = M.mercado_slide(edicao("21"), historia("21")).alt
    assert alt.startswith("Mercado em linha, 21/10: Ibovespa +13,8% desde 28/09. ")
    assert "Ibovespa 208.296 (fech. 20/10, +1,57% no dia, +13,8% desde 28/09)" in alt
    assert "Gráficos: Ibovespa: linha com 14 leituras" in alt and "2 pregões sem leitura, tracejado" in alt
    assert len(alt) <= redacao.ALT_MAX


@real
def test_evitar_tira_o_ativo_do_titulo():
    c = M.mercado_slide(edicao("21"), historia("21"), evitar={"ibov"}).ctx
    assert (c["titulo"], c["destaque"]) == ("Brent +10,1% desde 29/09", "+10,1%")


@real
@pytest.mark.parametrize("dia", ["21", "26"])
def test_substituto_da_noite_em_papel_sem_numero(dia):
    p = M.mercado_em_linha(edicao(dia), {}, historia(dia))
    assert (p.slot, p.kind, p.serie, p.numero_serie, p.chave) == ("noite", "image", "mercado", 0, "")
    assert len(p.slides) == 1 and p.slides[0].ctx["tema"] == "t-paper" and p.slides[0].ctx["cor"] == "mercado"
    linhas = p.caption.split("\n")
    assert linhas[0] == f"Mercado em linha · {dia}/10: {p.slides[0].ctx['titulo']}" and linhas[1] == ""
    assert len(linhas[0]) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX
    assert 3 <= sum(1 for x in linhas if x.startswith("▸ ")) <= 5
    assert linhas[-2] == M.CTA and linhas[-1] == "#triangula #dados #mercadofinanceiro #economia"
    assert redacao.lint(p, election=True) == []


@real
def test_legenda_do_substituto_em_21_10():
    p = M.mercado_em_linha(edicao("21"), {}, historia("21"))
    assert "▸ Ibovespa: 208.296 pontos (fech. 20/10, +1,57% no dia, +13,8% desde 28/09)." in p.caption
    assert "▸ Dólar: R$ 5,01 (leitura das 06h00, −0,35% no dia, −4,1% desde 28/09)." in p.caption
    assert "▸ Selic 13,75% · IPCA 12m 4,58% (set) · juro real ex-post 8,77%." in p.caption


@real
def test_dia_sem_selic_nem_ipca_fica_sem_faixa_de_juros():
    s = M.mercado_slide(edicao("18"), historia("18"), tema="paper")
    assert s.ctx["faixa"] == "" and "Selic" not in s.alt
    assert 'class="faixa"' not in _render(s)


@real
def test_serie_curta_mostra_so_o_valor_com_o_acumulado():
    e = Edition.from_dict(json.loads((DATA / "2026-10-05.json").read_text(encoding="utf-8")))
    s = M.mercado_slide(e, E.load_history(DATA, date(2026, 10, 5), 90), tema="paper")
    assert not s.ctx["graficos"] and all(p["grafico"] == "" for p in s.ctx["paineis"])
    assert s.ctx["titulo"].endswith("desde 28/09") and "Gráficos" not in s.alt
    html = _render(s)
    assert 'class="frame curta"' in html and "na série: curta para o gráfico" in html


def test_leitura_sem_variacao_nao_mostra_variacao():
    dia = date(2026, 10, 21)
    cedo = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)  # 05h00 em Brasília, 0,00%
    e = _sintetica(dia, [_quote("usd", 5.0, 0.0, cedo)])
    p = M.mercado_slide(e, [e]).ctx["paineis"][0]
    assert (p["selo"], p["change"], p["trend"], p["acumulado"], p["grafico"]) == ("", "", "", "", "")


def test_linha_mostra_no_maximo_60_dias():
    fim = date(2026, 10, 21)
    hist = []
    for k in range(80, -1, -1):
        d = fim - timedelta(days=k)
        at = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)
        hist.append(_sintetica(d, [_quote("usd", 5 + k / 100, 0.1, at)]))
    p = M.mercado_slide(hist[-1], hist).ctx["paineis"][0]
    assert p["leituras"] == M.JANELA_DIAS + 1
    assert p["acumulado"] == f"{E.sinal((5.0 / 5.6 - 1) * 100)} desde {fim - timedelta(days=60):%d/%m}"


def test_unidades_no_texto():
    assert M.valor_fmt("ibov", 208295.98) == "208.296 pontos"
    assert M.valor_fmt("ibov", 208295.98, unidade=False) == "208.296"
    assert M.valor_fmt("usd", 5.0093) == "R$ 5,01" and M.valor_fmt("btc", 83365.79) == "US$ 83.366"
    assert M.nome_corrido("usd", "Dólar") == "dólar" and M.nome_corrido("brent", "Brent") == "Brent"


@real
@pytest.mark.parametrize("tema, classe", [("dark", "t-dark v2"), ("paper", "t-paper v2")])
def test_template_renderiza_nos_dois_temas(tema, classe):
    s = M.mercado_slide(edicao("21"), historia("21"), tema=tema)
    html = _render(s, "carousel")
    assert classe in html and '<em class="hl">+13,8%</em>' in html
    assert html.count("graf graf-linha") == 4 and "triangula" in html
    s.ctx.update(selo="EM 1 MINUTO Nº 031", contador=True)  # como slide 7 do Em 1 minuto
    assert '<span class="cnt">07/08</span>' in _render(s, "carousel")
