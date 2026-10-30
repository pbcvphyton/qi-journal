"""Manifesto (§4.7, o "Comece aqui 2.0"): 6 slides com os JSONs reais de data/ (sem Chromium)."""

from __future__ import annotations

import functools
import json
import re
from datetime import date
from pathlib import Path

import pytest

from qijournal.config import load_config
from qijournal.instagram import conteudo, estatistica as E, gerar, redacao
from qijournal.instagram.formatos import manifesto as MF
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-26.json").is_file(), reason="sem os JSONs reais em data/")
FRASES_FALSAS = ("um robô", "sem opinião", "mais de 1.600", "1.600 notícias todo dia", "mais de 100 fontes", "mais de 40 veículos")


@functools.lru_cache(maxsize=None)
def edicao(day: str) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day}.json").read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def historia(day: str) -> list[Edition]:
    return E.load_history(DATA, date(2026, 10, int(day)), 90)


@functools.lru_cache(maxsize=None)
def manifesto(day: str):
    return MF.manifesto(edicao(day), {}, historia(day))


def textos(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for k, v in value.items() if k != "grafico" for t in textos(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in textos(v)]
    return []


# ── Contas ────────────────────────────────────────────────────────────────


def test_em_cada_10_arredonda_para_baixo():
    assert MF.em_cada_10(10549, 11898) == "mais de 8"  # 88,7%: nunca "9 em cada 10"
    assert MF.em_cada_10(878, 1051) == "mais de 8"  # 83,5%
    assert MF.em_cada_10(9, 10) == "9"
    assert MF.em_cada_10(90, 100) == "9"
    assert MF.em_cada_10(100, 100) == "10"
    assert MF.em_cada_10(899, 1000) == "mais de 8"
    assert MF.em_cada_10(5, 100) == "menos de 1"


def test_proporcao_so_com_base_de_20_ou_mais():
    assert MF.proporcao(10549, 11898) == "mais de 8 em cada 10"
    assert MF.proporcao(878, 1051, "de") == "mais de 8 de cada 10"
    assert MF.proporcao(17, 19) == "17 de 19"  # base abaixo de 20: a contagem crua


@real
def test_historico_de_14_a_25_10_bate_com_a_spec():
    """§4.7: "hoje dá 10.549 de 11.898, 88,7%" e "878 de 1.051 sem divisão, 83,5%"."""
    h = MF.historico(edicao("25"), historia("25"))
    assert (h.inicio, h.fim, h.edicoes) == (date(2026, 10, 14), date(2026, 10, 25), 12)
    assert (h.uma_fonte, h.assuntos) == (10549, 11898)
    assert (h.sem_divisao, h.coberturas, h.divididas) == (878, 1051, 173)
    assert h.periodo == "de 14/10 a 25/10" and h.periodo_frase == "De 14/10 a 25/10"


@real
def test_historico_ignora_dia_sem_ia_e_inclui_a_edicao():
    h = MF.historico(edicao("26"), [e for e in historia("26") if e.date != "2026-10-26"])
    assert (h.inicio, h.fim, h.edicoes) == (date(2026, 10, 14), date(2026, 10, 26), 13)
    assert (h.uma_fonte, h.assuntos, h.sem_divisao, h.coberturas) == (11417, 12850, 895, 1069)
    # as edições sem IA (29/09 a 13/10) estão no histórico, mas não entram na conta
    assert any(e.mode != "ai" for e in historia("26"))


# ── Slides ────────────────────────────────────────────────────────────────


@real
def test_peca_tem_6_slides_na_ordem():
    p = manifesto("26")
    assert (p.slot, p.kind, p.title, p.serie, p.numero_serie) == ("lancamento", "carousel", "Manifesto", "", 0)
    assert [s.template for s in p.slides] == [
        "manifesto_capa", "manifesto_funil", "manifesto_dados", "manifesto_dados", "manifesto_texto", "manifesto_cta",
    ]


@real
def test_capa_laranja_com_o_bordao():
    ctx = manifesto("26").slides[0].ctx
    assert ctx["titulo"] == "Mesmo fato, várias versões."
    assert ctx["sub"] == "O que aconteceu, quanto o mercado andou e como cada veículo contou."
    assert 180 <= ctx["tam"] <= 236


@real
def test_funil_com_os_numeros_da_edicao_e_a_data():
    s = MF.manifesto(edicao("21"), {}, historia("21")).slides[1]
    assert [(x["n"], x["rotulo"]) for x in s.ctx["etapas"]] == [
        ("1.640", "notícias lidas"), ("1.159", "assuntos"), ("85", "coberturas comparadas"), ("14", "matérias"),
    ]
    assert s.ctx["kicker"] == "O funil de 21/10"
    assert "Em 21/10: 1.640 notícias → 1.159 assuntos → 85 coberturas comparadas → 14 matérias." in s.alt
    assert s.ctx["etapas"][0]["desc"] == "de 46 fontes"


@real
def test_pauta_solitaria_com_periodo_e_n_no_rodape():
    s = manifesto("26").slides[2]
    assert s.ctx["kicker"] == "Pauta solitária"
    assert s.ctx["titulo"] == "Mais de 8 em cada 10 assuntos saem em uma fonte só"
    assert s.ctx["destaque"] == "8 em cada 10" and s.ctx["destaque"] in s.ctx["titulo"]
    assert s.ctx["pe"].startswith("De 14/10 a 26/10: 11.417 de 12.850 assuntos (88,8%) em uma fonte só, em 13 edições com IA")
    assert "superestimar exclusivos" in s.ctx["pe"]
    # a matriz do dia: um ponto por assunto de 26/10 (952), e a frase do gráfico vai no alt
    assert s.ctx["grafico"].count("<circle") >= 952
    assert "Gráfico: matriz com 952 pontos, um por assunto" in s.alt


@real
def test_divisao_com_waffle_de_100_e_a_definicao():
    s = manifesto("26").slides[3]
    assert s.ctx["titulo"] == "Em mais de 8 de cada 10 assuntos comparados, os veículos contaram sem divisão."
    assert s.ctx["texto"] == "Nos outros, a gente mostra a diferença."
    assert s.ctx["pe"].startswith("De 14/10 a 26/10: 895 de 1.069 coberturas comparadas (83,7%) sem divisão")
    assert "Dividida = pelo menos 1 veículo de cada lado" in s.ctx["pe"]
    svg = str(s.ctx["grafico"])
    # 100 quadrados, cada um 1% das 1.069: 16 divididas (174/1.069) em laranja
    assert svg.count("fill='var(--g-hi)'") == 16
    assert "Gráfico: grade de 100 quadrados, um por 1% das 1.069 coberturas comparadas; 16 divididas e 84 sem divisão." in s.alt


@real
def test_como_fazemos_e_cta():
    p = manifesto("26")
    ctx = p.slides[4].ctx
    assert ctx["regras"] == ["Sem adjetivo, sempre com fonte.", "Sem foto de jornal.", "Sem ranking de veículos."]
    assert ctx["frase"] == "Enquadramento é escolha do que destacar, não erro."
    cta = p.slides[5].ctx
    assert [(i["hora"], i["nome"]) for i in cta["agenda"]] == [("07h", "Em 1 minuto"), ("12h", "Número do dia"), ("18h30", "Raio-X")]
    assert cta["agenda"][0]["obs"] == "sáb. e dom., 08h30"  # o horário do fim de semana, para não ficar falso
    assert cta["cta"] == "Siga."


@real
def test_legenda_no_molde():
    p = manifesto("26")
    linhas = p.caption.split("\n")
    assert linhas[0] == "Mesmo fato, várias versões: como a Triangula lê a imprensa"
    assert linhas[1] == ""
    assert 3 <= sum(1 for x in linhas if x.startswith("▸ ")) <= 5
    assert linhas[-1] == "#triangula #jornalismo #imprensa #dados"
    assert any(x.startswith("Como medimos:") for x in linhas)
    assert "Siga: todo dia, 3 posts." in linhas
    assert len(linhas[0]) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX
    assert "▸ Pauta solitária: de 14/10 a 26/10, 11.417 de 12.850 assuntos (88,8%) saíram em uma fonte só" in p.caption


@real
@pytest.mark.parametrize("dia", ["21", "25", "26"])
def test_lint_e_nada_de_frase_falsa(dia):
    p = MF.manifesto(edicao(dia), {}, historia(dia))
    assert redacao.lint(p, election=True) == []
    assert redacao.lint(p, election=False) == []
    tudo = " ".join([p.caption] + [t for s in p.slides for t in textos(s.ctx) + [s.alt]]).lower()
    for frase in FRASES_FALSAS:
        assert frase not in tudo
    assert not re.search(r"\b9 em cada 10\b", tudo)  # 88,7%/88,8% arredondado para baixo é "mais de 8"
    for s in p.slides:
        assert s.alt and len(s.alt) <= redacao.ALT_MAX


@real
@pytest.mark.parametrize("handle", [None, "triangula"])
def test_templates_renderizam(handle):
    """Sem Chromium: só o Jinja (StrictUndefined pega chave faltando no ctx)."""
    env = gerar.environment()
    brand = {**gerar.brand_context(load_config(ROOT)), "handle": handle or ""}
    p = manifesto("26")
    temas = ["t-orange v2", "t-paper v2", "t-paper v2", "t-paper v2", "t-dark v2", "t-orange v2"]
    for i, (s, tema) in enumerate(zip(p.slides, temas), 1):
        html = env.get_template(f"{s.template}.html.j2").render(
            brand=brand, s=conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
        )
        assert f'class="{tema}"' in html
        assert 'class="assin"' in html and ("@triangula" in html) == bool(handle)
        assert ("%02d/06" % i in html) == (i > 1)  # contador em todos menos a capa
