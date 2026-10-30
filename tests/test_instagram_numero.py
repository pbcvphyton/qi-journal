"""Número do dia (§4.4): a peça de 3 slides, com os JSONs reais de data/ (sem Chromium)."""

from __future__ import annotations

import functools
import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from qijournal.config import load_config
from qijournal.instagram import conteudo, estatistica as E, gerar, redacao
from qijournal.instagram.formatos import numero as N
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


def materia(day: str, prefixo: str):
    return next(s for s in edicao(day).stories if s.id.startswith(prefixo))


def peca(day: str, raiox: str | None = None, *, numero_serie: int = 3, usados=None):
    e = edicao(day)
    exclude = {e.lead} | ({materia(day, raiox).id} if raiox else set())
    return N.numero_do_dia(e, {}, historia(day), election=True, numero_serie=numero_serie, exclude=exclude, usados=usados)


def veiculos(e: Edition) -> list[str]:
    nomes = {i.source for t in e.index for i in t.items if i.source}
    for s in e.stories:
        nomes |= {x.name for x in s.sources if x.name}
    return sorted(nomes)


# (dia, story do Raio-X, tipo, qualificador, número da capa, tese, prova da capa): a tabela do §4.4
CASOS = [
    ("15", None, "mercado", "", "−2,36%", "Bitcoin cai 2,36%", "leitura das 06h55 · 2,6 vezes a variação típica"),
    ("16", None, "triangulado", "", "0,3%", "IBC-Br sobe 0,3% em agosto com recuo do agro", "no título de 5 de 5 veículos"),
    ("18", None, "concentracao", "", "12", None, "o assunto mais coberto entre 663 do dia"),
    ("21", "spacex", "acumulado", "", "+13,8%", "Ibovespa acumula +13,8% desde 28/09", "fech. 28/09 → fech. 20/10 · 14 leituras"),
    ("22", None, "triangulado", "ATÉ", "R$ 90 bi", None, "no título de 5 de 6 veículos"),
    ("24", "ipca-15", "triangulado", "", "US$ 35,7 bi", None, "no título de 5 de 5 veículos"),
]
GRAFICO = {"mercado": "barras", "triangulado": "checklist", "concentracao": "matriz", "acumulado": "linha", "juro_real": "juros"}


@real
@pytest.mark.parametrize("dia, raiox, tipo, qual, valor, tese, prova", CASOS)
def test_capa_dos_dias_da_tabela(dia, raiox, tipo, qual, valor, tese, prova):
    p = peca(dia, raiox)
    assert (p.slot, p.kind, p.serie, p.numero_serie, p.title) == ("numero", "carousel", "numero", 3, "Número do dia")
    assert [s.template for s in p.slides] == ["numero_capa", "numero_prova", "numero_metodo"]
    capa = p.slides[0].ctx
    assert (capa["qual"], capa["valor"], capa["prova"]) == (qual, valor, prova)
    assert capa["selo"] == f"NÚMERO DO DIA Nº 003 · {conteudo.kicker_date(date(2026, 10, int(dia)))}"
    assert N.NUM_MIN <= capa["tam"] <= N.NUM_MAX
    if tese:
        assert capa["tese"] == tese
    assert len(capa["tese"].split()) <= E.TESE_MAX_PALAVRAS
    assert p.chave and p.chave == E.numero_do_dia(
        edicao(dia), historia(dia), election=True, exclude={edicao(dia).lead} | ({materia(dia, raiox).id} if raiox else set())
    ).chave


@real
def test_concentracao_separa_a_unidade_do_numero():
    capa = peca("18").slides[0].ctx
    assert (capa["valor"], capa["unidade"], capa["tam"]) == ("12", "veículos", N.NUM_MAX)


@real
@pytest.mark.parametrize("dia, raiox, tipo", [(c[0], c[1], c[2]) for c in CASOS])
def test_slide_2_traz_o_grafico_do_tipo_e_nao_repete_a_capa(dia, raiox, tipo):
    p = peca(dia, raiox)
    capa, prova = p.slides[0].ctx, p.slides[1].ctx
    assert str(prova["grafico"]).startswith(f"<svg class='graf graf-{GRAFICO[tipo]}'")
    assert prova["titulo"] != capa["tese"] and capa["tese"] not in prova["titulo"]
    assert prova["destaque"] and prova["destaque"].lower() in prova["titulo"].lower()
    assert "Gráfico: " in p.slides[1].alt
    assert prova["pe"].startswith("Fonte: ")
    assert prova["selo"] == "NÚMERO DO DIA Nº 003"


@real
def test_slide_2_do_triangulado_lista_os_veiculos_sem_titulo():
    p = peca("22")
    prova = p.slides[1].ctx
    assert prova["titulo"] == "5 de 6 veículos puseram R$ 90 bi no título" and prova["destaque"] == "5 de 6"
    svg = str(prova["grafico"])
    for nome in ("Valor Econômico", "g1", "Folha de S.Paulo", "Agência Brasil", "Estadão", "Money Times"):
        assert nome in svg
    titulos = [i.title for t in edicao("22").index for i in t.items if i.title]
    assert not any(t in svg for t in titulos)  # nenhum título de veículo na arte
    assert prova["contexto"][0] == conteudo.plain(materia("22", "governo-preve").dek)


@real
def test_lista_de_12_veiculos_cabe_inteira_e_o_contexto_cede_espaco():
    e = edicao("22")
    meta = next(n for n in E.triangulados(e, election=True) if n.chave == "25bi")
    p = N.peca_numero(e, meta, historia("22"), election=True, numero_serie=9)
    prova = p.slides[1].ctx
    svg = str(prova["grafico"])
    assert prova["titulo"] == "7 de 12 veículos puseram US$ 25 bi no título"
    assert all(nome in svg for nome in ("Valor Econômico", "Al Jazeera", "MarketWatch", "The Guardian"))
    assert "+" not in re.sub(r"<[^>]+>", "", svg)  # nenhum "+k veículos": as 12 linhas couberam
    assert prova["contexto"] == []


@real
def test_slide_2_do_mercado_e_da_concentracao():
    btc = peca("15").slides[1].ctx
    assert btc["titulo"] == "2,6 vezes a variação típica do bitcoin" and btc["destaque"] == "2,6 vezes"
    assert btc["contexto"][0] == "Variação típica do bitcoin: 0,90% por dia, nas 16 leituras anteriores (desde 29/09)."
    assert btc["pe"] == "Fonte: Yahoo Finance · uma leitura por dia · hoje: leitura das 06h55"
    conc = peca("18").slides[1].ctx
    assert conc["titulo"] == "1º entre 663 assuntos; 589 saíram em uma fonte só" and conc["destaque"] == "1º"
    assert "graf-matriz" in str(conc["grafico"])


@real
def test_legenda_e_alt_do_acumulado_seguem_o_molde_da_spec():
    p = peca("21", "spacex")
    assert p.caption == (
        "+13,8%: Ibovespa acumula alta desde 28/09, quando a série da Triangula começa.\n"
        "\n"
        "▸ De 182.991 pontos (fech. 28/09) a 208.296 (fech. 20/10), em 14 leituras.\n"
        "▸ Máxima da série: 209.067 pontos, fech. 09/10.\n"
        "▸ No mesmo período: Brent +10,1%, dólar −4,1%.\n"
        "Como medimos: uma leitura por dia (Yahoo Finance), variação ponta a ponta; não é soma de variações diárias.\n"
        "Mande para quem acha que leu tudo.\n"
        "#triangula #dados #mercadofinanceiro"
    )
    assert p.slides[1].alt.startswith(
        "Número do dia nº 3: +13,8%. Ibovespa acumula alta desde 28/09. Gráfico: linha com 14 leituras, "
        "de 182.991 (28/09) a 208.296 (20/10); máxima de 209.067 em 09/10"
    )
    assert p.slides[0].alt == "Número do dia nº 3: +13,8%. Ibovespa acumula +13,8% desde 28/09. fech. 28/09 → fech. 20/10 · 14 leituras."
    assert p.slides[1].ctx["titulo"] == "De 182.991 a 208.296 pontos em 14 leituras"


@real
@pytest.mark.parametrize("dia, raiox", [(c[0], c[1]) for c in CASOS] + [("26", None)])
def test_legenda_no_molde_comum(dia, raiox):
    p = peca(dia, raiox)
    linhas = p.caption.split("\n")
    assert len(linhas[0]) <= redacao.LINHA1_MAX and linhas[1] == ""
    assert len(p.caption) <= redacao.LEGENDA_MAX
    marcadas = [x for x in linhas if x.startswith("▸ ")]
    assert 3 <= len(marcadas) <= 5
    assert linhas[-3].startswith("Como medimos: ") and linhas[-2] == N.CTA
    tags = linhas[-1].split()
    assert tags[:2] == ["#triangula", "#dados"] and 3 <= len(tags) <= 4
    assert all(0 < len(s.alt) <= redacao.ALT_MAX for s in p.slides)
    assert "recorde" not in p.caption.lower() and "histórico" not in p.caption.lower()


@real
@pytest.mark.parametrize("dia, raiox", [(c[0], c[1]) for c in CASOS] + [("26", None)])
def test_lint_aprova_as_pecas_dos_dias_de_previa(dia, raiox):
    p = peca(dia, raiox)
    assert redacao.lint(p, election=True, veiculos=veiculos(edicao(dia))) == []


@real
def test_slide_3_metodo_ficha_ordem_de_escolha_e_cta():
    p = peca("21", "spacex")
    m = p.slides[2].ctx
    assert m["cta"] == "Mande para quem acha que leu tudo." and p.slides[2].alt.endswith(m["cta"])
    assert [f["k"] for f in m["ficha"]] == ["Fonte", "Período", "Base", "Limite"]
    assert [c["n"] for c in m["cadeia"] if c["atual"]] == [5] == [m["passo"]]
    assert len(m["cadeia"]) == 7
    assert m["ficha"][1]["v"] == "fech. 28/09 a fech. 20/10"
    assert m["ficha"][2]["v"] == "14 leituras; 2 pregões sem leitura"


@real
def test_acumulado_fora_do_fechamento_escreve_as_datas_das_leituras():
    p = peca("26", usados={"acum:ibov", "acum:brent"})
    capa = p.slides[0].ctx
    assert capa["valor"] == "−4,4%" and capa["tese"] == "Dólar acumula −4,4% desde 28/09"
    assert capa["prova"] == "leitura de 28/09, 23h34 → leitura das 04h30 · 24 leituras"
    assert p.caption.startswith("−4,4%: Dólar acumula queda desde 28/09")


@real
def test_juro_real_tem_barras_proprias_e_a_conta_escrita():
    e = edicao("21")
    p = N.peca_numero(e, E._juro_real_numero(e), historia("21"), election=True, numero_serie=8)
    capa, prova = p.slides[0].ctx, p.slides[1].ctx
    assert (capa["valor"], capa["tese"]) == ("8,77%", "Juro real ex-post de 8,77%")
    svg = str(prova["grafico"])
    assert "graf-juros" in svg and all(v in svg for v in ("13,75%", "4,58%", "8,77%"))
    assert "(1 + 13,75%) ÷ (1 + 4,58%) − 1 = 8,77%" in svg
    assert p.caption.split("\n")[0] == "8,77%: o juro real ex-post com a Selic a 13,75% e o IPCA de 12 meses a 4,58%."
    assert redacao.lint(p, election=True) == []


@real
def test_sem_ia_o_numero_e_de_mercado_e_sem_numero_de_serie():
    assert edicao("10").mode != "ai"
    p = N.numero_do_dia(edicao("10"), {}, historia("10"), election=True)
    assert p.slides[0].ctx["selo"] == "NÚMERO DO DIA · SÁB 10 OUT"
    assert p.slides[0].alt.startswith("Número do dia: ")
    assert p.numero_serie == 0 and p.chave.startswith(("acum:", "usd:", "btc:", "ibov:", "brent:", "spx:", "gold:", "juroreal"))


@real
@pytest.mark.parametrize("dia, raiox", [(c[0], c[1]) for c in CASOS])
def test_templates_renderizam_com_o_contexto_da_peca(dia, raiox):
    """Sem Chromium: só o Jinja (StrictUndefined pega chave faltando no ctx)."""
    env = gerar.environment()
    brand = gerar.brand_context(load_config(ROOT))
    p = peca(dia, raiox)
    for i, s in enumerate(p.slides, 1):
        html = env.get_template(f"{s.template}.html.j2").render(
            brand=brand, s=conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
        )
        assert "triangula" in html and "<svg" in html
        assert ("t-orange v2" if i == 1 else "t-paper v2") in html
    capa = env.get_template("numero_capa.html.j2").render(
        brand=brand, s=conteudo.polish(p.slides[0].ctx), page=1, pages=3, kind="carousel", slot="numero"
    )
    assert 'data-fit="numero"' in capa and 'class="sb-row"><span class="pill"' in capa


def test_tamanho_do_numero_e_colunas_da_matriz():
    assert N.tamanho_numero("0,3%") == N.NUM_MAX
    assert N.NUM_MIN <= N.tamanho_numero("US$ 35,7 bi") < 300
    assert N.tamanho_numero("US$ 1,99 trilhões de dólares") == N.NUM_MIN
    assert N.colunas_matriz(663, 600) == 34
    assert N.colunas_matriz(1159, 600) > 34


def test_altura_do_grafico_respeita_os_limites():
    curto = N.altura_grafico("2 de 3", 104, [], "Fonte: x", teto=500)
    assert curto == 500
    longo = N.altura_grafico("título " * 30, 72, ["contexto " * 60] * 3, "pé " * 80)
    assert longo == N.GRAFICO_MIN


def test_frase_da_serie_so_quando_o_primeiro_ponto_e_o_comeco_dos_dados():
    n = SimpleNamespace(tipo="acumulado", prova={"primeiro": {"data": date(2026, 9, 28)}})
    curta = [SimpleNamespace(date="2026-09-28"), SimpleNamespace(date="2026-10-21")]
    assert N._serie_comeca(curta, n)
    janela = [SimpleNamespace(date="2026-07-23"), SimpleNamespace(date="2026-10-21")]  # 90 dias: o corte
    n.prova["primeiro"]["data"] = date(2026, 7, 23)
    assert not N._serie_comeca(janela, n)


@real
def test_slide_2_nao_repete_a_tese_quando_ela_saiu_da_linha_fina():
    p = peca("24", "ipca-15")
    tese = p.slides[0].ctx["tese"]
    assert tese == "Volume total de encomendas avança 14% em doze meses"  # a linha fina cortada (§4.4)
    assert all(tese not in c for c in p.slides[1].ctx["contexto"])
    assert p.slides[1].ctx["contexto"][0] == "Puxado por forte demanda no segmento de defesa e na aviação executiva."
    assert "▸ O fato: Volume total de encomendas avança 14% em doze meses, puxado" in p.caption  # a legenda estende


@real
def test_metodo_da_concentracao_com_ponto_de_milhar():
    e = edicao("21")
    conc = E._concentracao_numero(e, election=True, exclude={e.lead})
    p = N.peca_numero(e, conc, historia("21"), election=True, numero_serie=8)
    assert "1.039 de 1.159 (90%)" in p.slides[2].ctx["metodo"]


def test_sem_cotacoes_nao_ha_numero():
    e = Edition.from_dict(
        {
            "date": "2026-10-21", "date_label": "", "generated_at": "2026-10-21T09:00:00+00:00", "mode": "heuristic",
            "model": None, "editorial": "", "briefing": [], "lead": "", "secondary": [], "highlights": [], "sections": [],
            "stories": [], "quotes": [], "weather": [], "compared": [], "index": [],
        }
    )
    assert N.numero_do_dia(e, {}, [e], election=True) is None
