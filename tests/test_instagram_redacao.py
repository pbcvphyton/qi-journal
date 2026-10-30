"""Regras de texto e lint das peças do Instagram (§4.1 e §5.4, versão sem IA)."""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from qijournal.instagram import estatistica as E
from qijournal.instagram import redacao as R
from qijournal.instagram import selecao as S
from qijournal.instagram.conteudo import Peca, Slide
from qijournal.models import Edition

DATA = Path(__file__).resolve().parent.parent / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-25.json").is_file(), reason="sem os JSONs reais em data/")


def peca(*slides: Slide, caption: str = "Linha 1\n\n▸ fato\n#triangula") -> Peca:
    return Peca(slot="noite", kind="carousel", title="Teste", slides=list(slides), caption=caption)


def test_palavras_e_verbos_proibidos():
    assert R.palavras_proibidas("Dólar dispara e Bolsa despenca em meio a cenário de incerteza") == [
        "dispara", "despenca", "em meio a", "cenário",
    ]
    # Palavra parecida não conta: desabamento, disparada, tiros disparados.
    assert R.palavras_proibidas("Desabamento em SP; disparada do ouro; tiros disparados") == []
    assert R.verbos_proibidos("O jornal omitiu o dado e foi o primeiro a noticiar o furo") == ["omitiu", "primeiro a noticiar", "furo"]
    assert R.verbos_proibidos("Folha destacou o custo; g1 publicou às 16h00") == []
    assert "destacou" in R.VERBOS_VEICULO and "ignorou" in R.VERBOS_PROIBIDOS


def test_assinatura_sem_handle_nunca_falha():
    assert R.assinatura(None) == "▲ triangula"
    assert R.assinatura("") == "▲ triangula"
    assert R.assinatura("@triangula") == "▲ @triangula"
    assert R.assinatura("triangula.jornal") == "▲ @triangula.jornal"


def test_lint_aprova_peca_limpa():
    p = peca(
        Slide("capa", {"gancho": "11 VEÍCULOS, MESMO FATO", "destaque": "COMPLEXIDADE DE GOVERNANÇA",
                       "section": {"id": "tecnologia"}}, alt="Raio-X da cobertura nº 14."),
        Slide("lado", {"outlets": [{"name": "Valor Econômico", "framing": "Destaca o cenário de sinergia", "stance": "a"}]},
              alt="Valor Econômico: Destaca o cenário de sinergia"),
    )
    # O enquadramento do veículo é citado como está no JSON: "cenário" ali não reprova.
    assert R.lint(p, election=True) == []


def test_lint_aponta_palavra_proibida_no_texto_da_casa_e_na_legenda():
    p = peca(Slide("fato", {"text": "Dólar dispara"}, alt="Dólar dispara"), caption="Em meio a tudo\n\nx")
    problemas = R.lint(p, election=False)
    assert "slide 1: palavra proibida “dispara”" in problemas
    assert "slide 1 (alt): palavra proibida “dispara”" in problemas
    assert "legenda: palavra proibida “em meio a”" in problemas


def test_lint_eleitoral_so_no_periodo_e_respeita_mundo():
    tse = peca(Slide("fato", {"text": "TSE intima Meta", "section": {"id": "juridico"}}))
    assert R.lint(tse, election=True) == ["slide 1: termo eleitoral no período eleitoral"]
    assert R.lint(tse, election=False) == []
    mundo = peca(Slide("fato", {"text": "Candidato republicano lidera em Nova York", "section": {"id": "mundo"}}))
    assert R.lint(mundo, election=True) == []
    legenda = peca(Slide("fato", {"text": "ok"}), caption="Mercado sobe\n\n▸ Lula viaja")
    assert R.lint(legenda, election=True) == ["legenda: nome de político ou partido no período eleitoral"]


def test_lint_destaque_e_gancho_sem_politico_partido_adjetivo_ou_veiculo_sempre():
    p = peca(
        Slide("capa", {"destaque": "LULA", "gancho": "MESMO FATO, 4 VEÍCULOS"}),
        Slide("capa", {"destaque": ["FUSÃO HISTÓRICA"], "gancho": "O PL DESTACOU"}),
        Slide("capa", {"destaque": "FOLHA DE S.PAULO"}),
    )
    problemas = R.lint(p, election=False, veiculos=["Folha de S.Paulo"])
    assert problemas == [
        "slide 1: destaque com nome de político ou partido (“LULA”)",
        "slide 2: destaque com adjetivo da casa (“FUSÃO HISTÓRICA”)",
        "slide 2: gancho com nome de político ou partido (“O PL DESTACOU”)",
        "slide 3: destaque com nome de veículo (“FOLHA DE S.PAULO”)",
    ]


def test_lint_limites_e_overflow():
    linha = "x" * 111
    p = peca(Slide("fato", {"text": "ok"}, alt="a" * 1001), caption=linha + "\n\n" + "y" * 1300)
    problemas = R.lint(p, election=False, overflow=[3, "capa: gancho"])
    assert problemas == [
        "slide 1: texto alternativo com 1001 caracteres (máx. 1000)",
        "legenda: 1ª linha com 111 caracteres (máx. 110)",
        f"legenda: {len(p.caption)} caracteres (máx. 1300)",
        "slide 3: texto transbordou",
        "transbordou: capa: gancho",
    ]
    story = Peca(slot="stories", kind="story", title="Stories", slides=[Slide("story_bomdia", {"x": "Bom dia"})])
    assert R.lint(story, election=False) == []  # story não tem legenda


def test_lint_reprova_primeira_linha_repetida_dos_ultimos_dias():
    p = peca(Slide("fato", {"text": "ok"}), caption="Ibovespa acumula +13,8% desde 28/09\n\nx")
    assert R.lint(p, election=False) == []  # sem histórico, não confere
    assert R.lint(p, election=False, recentes=["Ibovespa acumula +12,1% desde 28/09"]) == [
        "legenda: 1ª linha repete uma dos últimos dias (“Ibovespa acumula +12,1% desde 28/09”)"
    ]
    a = "11 veículos, mesmo fato: 6 destacaram sinergia em IA; só 2, complexidade de governança."
    b = "10 veículos, mesmo fato: 8 destacaram desafio de Zelenski; só 2, coesão europeia."
    assert R.similaridade(a, b) < R.REPETICAO_MAX  # o mesmo molde com outro assunto passa


@functools.lru_cache(maxsize=None)
def edicao(day: int) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day:02d}.json").read_text(encoding="utf-8")))


@real
def test_ganchos_e_teses_reais_de_14_a_25_10_passam_no_lint():
    """O que esta base escreve nos 12 dias: gancho e destaque do Raio-X e tese do Número."""
    recentes: list[list[str]] = []
    for day in range(14, 26):
        e = edicao(day)
        story = S.escolher_raiox(e, election=True, entidades_recentes=recentes[-3:])
        slides = []
        if story is not None:
            g = S.gancho_raiox(story.coverage)
            slides.append(Slide("raiox_capa", {"gancho": g.texto, "destaque": g.destaque, "section": {"id": story.section}},
                                alt=g.legenda))
            recentes.append(S.entidades(story))
        else:
            recentes.append([])
        exclude = {e.lead} | ({story.id} if story else set())
        n = E.numero_do_dia(e, E.load_history(DATA, E.date(2026, 10, day), 90), election=True, exclude=exclude)
        slides.append(Slide("numero_capa", {"valor": n.valor, "tese": n.tese, "selo": n.selo, "section": {"id": n.section or ""}},
                            alt=f"Número do dia: {n.valor}. {n.tese}."))
        p = Peca(slot="noite", kind="carousel", title="dia", slides=slides, caption=(g.legenda if story else n.tese) + "\n\nx")
        assert R.lint(p, election=True) == [], day
