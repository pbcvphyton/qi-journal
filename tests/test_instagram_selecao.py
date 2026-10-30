"""Escolha das matérias das publicações do Instagram."""

from __future__ import annotations

from qijournal.instagram.selecao import coverage_story, divergence_score, diverse_stories, ordered_stories
from qijournal.models import Coverage, CoverageOutlet, Edition, Story


def story(sid, section="brasil", importance=3, stances=None):
    coverage = None
    if stances is not None:
        coverage = Coverage(
            topic=sid,
            conclusion="",
            outlets=[CoverageOutlet(name=f"V{i}", stance=st) for i, st in enumerate(stances)],
            side_a="lado A" if "a" in stances else "",
            side_b="lado B" if "b" in stances else "",
        )
    return Story(id=sid, section=section, headline=sid, dek="", body=[], sources=[], article_ids=[], importance=importance, coverage=coverage)


def edition(stories, lead, secondary=(), highlights=()):
    return Edition(
        date="2026-10-20", date_label="", generated_at="", mode="ai", model=None, editorial="", briefing=[],
        lead=lead, secondary=list(secondary), highlights=list(highlights), sections=[], stories=stories, quotes=[], weather=[],
    )


def test_ordem_editorial_manchete_chamadas_destaques_e_importancia():
    e = edition([story("x", importance=2), story("y", importance=5), story("lead"), story("sec"), story("hl")], "lead", ["sec"], ["hl"])
    assert [s.id for s in ordered_stories(e)] == ["lead", "sec", "hl", "y", "x"]


def test_diversidade_uma_por_editoria_e_completa_se_faltar():
    e = edition([story("a1", "brasil"), story("a2", "brasil"), story("b1", "mundo"), story("c1", "esporte")], "a1", ["a2", "b1", "c1"])
    assert [s.id for s in diverse_stories(e, 3)] == ["a1", "b1", "c1"]
    assert [s.id for s in diverse_stories(e, 4)] == ["a1", "a2", "b1", "c1"]
    assert [s.id for s in diverse_stories(e, 2, exclude=["a1"])] == ["a2", "b1"]


def test_divergencia_prefere_lados_equilibrados_e_engajados():
    assert divergence_score(None) == 0
    assert divergence_score(story("s", stances=["neutro"] * 5).coverage) == 0
    assert divergence_score(story("s", stances=["a"] * 5).coverage) == 0
    equilibrado = divergence_score(story("s", stances=["a", "a", "b", "b"]).coverage)
    morno = divergence_score(story("s", stances=["a", "b"] + ["neutro"] * 8).coverage)
    torto = divergence_score(story("s", stances=["a", "a", "a", "b"]).coverage)
    assert equilibrado > torto > 0 and equilibrado > morno > 0


def test_materia_de_cobertura_ignora_poucos_veiculos_e_excluidas():
    e = edition(
        [story("lead", stances=["a", "b", "a", "b"]), story("dois", stances=["a", "b"]), story("ok", stances=["a", "b", "b"])],
        "lead",
    )
    assert coverage_story(e).id == "lead"
    assert coverage_story(e, exclude=["lead"]).id == "ok"
    assert coverage_story(e, exclude=["lead", "ok"]) is None


# ── Raio-X 2.0 (dados reais de data/, com IA de 14 a 25/10) ─────────────────

import functools  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from qijournal.config import LimiaresConfig  # noqa: E402
from qijournal.instagram import estatistica as E  # noqa: E402
from qijournal.instagram import selecao as S  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-25.json").is_file(), reason="sem os JSONs reais em data/")


@functools.lru_cache(maxsize=None)
def edicao(day: int) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day:02d}.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "lado, rotulo",
    [
        ("Enfatiza a sinergia em IA e o salto de receita da SpaceX", "sinergia em IA"),
        ("Destaca a complexidade de governança e incerteza no fechamento", "complexidade de governança"),
        ("Destaca o impacto comercial e risco de embargos externos", "impacto comercial"),
        ("Prioriza a contenção sanitária e o monitoramento oficial", "contenção sanitária"),
        ("Destaca alívio e dado abaixo das projeções", "alívio"),
        ("Enfatiza persistência de alimentos e teto da meta", "persistência de alimentos"),
    ],
)
def test_rotulo_de_cada_lado(lado, rotulo):
    assert S.rotulo_lado(lado) == rotulo


def test_rotulo_corta_em_quatro_palavras_e_recusa_o_que_nao_serve():
    assert S.rotulo_lado("Foco na estratégia de pressão política do governo") == "estratégia"  # recua até a preposição
    assert S.rotulo_lado("Concentra-se nos riscos fiscais") == "riscos fiscais"
    assert S.rotulo_lado("Alega que limites de sinal foram manobra deliberada") == "limites de sinal"
    assert S.rotulo_lado("Destaca a redenção histórica do goleiro") is None  # adjetivo da casa
    assert S.rotulo_lado("Destaca a atuação heroica do time") is None  # lista de bloqueio
    assert S.rotulo_lado("Enfatiza a recuperação de Lula") is None  # político
    assert S.rotulo_lado("Destaca a manobra do PT") is None  # partido
    assert S.rotulo_lado("Afasta interferência do governo dos EUA") is None  # o verbo inverte o sentido
    assert S.rotulo_lado("") is None


@real
def test_rotulo_sai_em_pelo_menos_95_por_cento_dos_lados_reais():
    total = com_rotulo = 0
    for day in range(14, 26):
        for cov, _story in E.coverages(edicao(day)):
            if not (cov.has_debate and cov.count("a") >= 1 and cov.count("b") >= 1):
                continue
            for side in (cov.side_a, cov.side_b):
                total += 1
                com_rotulo += S.rotulo_lado(side) is not None
    assert total == 346
    assert com_rotulo / total >= 0.95  # 329 de 346 (a spec simulou 337: aqui verbo que inverte o sentido fica sem rótulo)


def test_entidades_e_repeticao():
    s = Story(id="x", section="mundo", headline="Ucrânia ataca refinarias da Rússia", dek="Governo de Kiev amplia ofensiva no Brasil",
              body=[], sources=[], article_ids=[])
    assert S.entidades(s) == ["Ucrânia", "Rússia", "Kiev"]
    assert S.repete(s, [["Starlink", "Ucrânia", "Rússia"]])
    assert not S.repete(s, [["Ucrânia", "Papa"], ["Rússia"]])  # 2 em comum com o MESMO dia


def test_nota_do_raiox_pesa_secao_e_repeticao():
    base = story("s", "brasil", importance=4, stances=["a", "a", "a", "b", "b"])
    assert S.nota_raiox(base) == 2 * 4 + 4 * 2 + 0.5 * 5 + 2 * 3
    esporte = story("s", "esporte", importance=4, stances=["a", "a", "a", "b", "b"])
    assert S.nota_raiox(base) - S.nota_raiox(esporte) == 2 * (3 - (-2))
    base.headline = "Ucrânia e Rússia"
    assert S.nota_raiox(base) - S.nota_raiox(base, [["Ucrânia", "Rússia"]]) == 8


def test_elegibilidade_do_raiox():
    s = story("s", "brasil", stances=["a", "a", "b", "b"])
    for o in s.coverage.outlets:
        o.framing = "enquadramento"
    assert S.raiox_elegivel(s, election=True)
    um_de_um_lado = story("t", "brasil", stances=["a", "a", "a", "b"])
    for o in um_de_um_lado.coverage.outlets:
        o.framing = "x"
    assert not S.raiox_elegivel(um_de_um_lado, election=False)
    s.coverage.outlets[0].framing = ""
    assert not S.raiox_elegivel(s, election=False)  # só 3 enquadramentos
    assert S.raiox_elegivel(s, election=False, limiares=LimiaresConfig(raiox_min_framings=3))


ESCOLHAS = {
    14: ("restricoes-na-starlink", 6, 4, 10),
    15: ("papa-leao-xiv", 5, 3, None),
    16: ("ucrania-retoma-ataques", 8, 2, None),
    17: ("deficit-fiscal-dos-estados", 2, 4, None),
    18: ("trump-suspende-sancoes", 2, 4, None),
    19: None,  # Placar do dia
    20: ("governo-declara-emergencia", 2, 2, 4),
    21: ("spacex-e-tesla", 6, 2, 11),
    22: ("stf-decide-que-iss", 2, 2, None),
    23: ("bradesco", 2, 4, None),
    24: ("ipca-15", 3, 3, None),
    25: ("musk-acusa-ucrania", 2, 4, None),
}


@real
def test_escolha_do_raiox_de_14_a_25_10():
    recentes: list[list[str]] = []  # o que a fila guardaria em Post.entidades
    geopolitica = 0
    for day, esperado in ESCOLHAS.items():
        e = edicao(day)
        noite = S.escolher_noite(e, election=True, entidades_recentes=recentes[-3:])
        assert all("tse-intima" not in s.id for _, s in S.candidatos_raiox(e, election=True))
        if esperado is None:
            assert noite.tipo == "placar" and noite.story is None
            assert noite.placar.texto_dia == "0 de 14"  # a spec simulou "0 de 16" sem o filtro por conteúdo
            recentes.append([])
            continue
        prefixo, a, b, n = esperado
        s = noite.story
        assert noite.tipo == "raiox" and s.id.startswith(prefixo), (day, s.id)
        assert (s.coverage.count("a"), s.coverage.count("b")) == (a, b)
        if n:
            assert len(s.coverage.outlets) == n
        assert s.section != "esporte"
        geopolitica += s.section == "mundo"
        recentes.append(S.entidades(s))
    assert geopolitica == 4


@real
def test_repeticao_penaliza_a_ucrania_em_15_10_e_trump_fica_sem_alternativa_em_18_10():
    e14 = edicao(14)
    recentes = [S.entidades(S.escolher_raiox(e14, election=True))]
    ranking = S.candidatos_raiox(edicao(15), election=True, entidades_recentes=recentes)
    ucrania = next(s for _, s in ranking if s.id.startswith("ucrania"))
    assert S.repete(ucrania, recentes) and ranking[0][1].id.startswith("papa")
    e16, e17 = edicao(16), edicao(17)
    recentes = [S.entidades(S.escolher_raiox(e, election=True)) for e in (edicao(15), e16, e17)]
    ranking = S.candidatos_raiox(edicao(18), election=True, entidades_recentes=recentes)
    assert len(ranking) == 1 and S.repete(ranking[0][1], recentes)


@real
@pytest.mark.parametrize(
    "day, prefixo, formula, texto, destaque",
    [
        (21, "spacex", "G2", "11 VEÍCULOS, MESMO FATO. 6 DESTACARAM SINERGIA EM IA. SÓ 2, COMPLEXIDADE DE GOVERNANÇA.",
         "COMPLEXIDADE DE GOVERNANÇA"),
        (20, "governo-declara", "G3", "MESMO FATO, 4 VEÍCULOS: METADE DESTACOU IMPACTO COMERCIAL; METADE, CONTENÇÃO SANITÁRIA.",
         "CONTENÇÃO SANITÁRIA"),
        (24, "ipca-15", "G3", "MESMO FATO, 6 VEÍCULOS: METADE DESTACOU ALÍVIO; METADE, PERSISTÊNCIA DE ALIMENTOS.",
         "PERSISTÊNCIA DE ALIMENTOS"),
        (23, "bradesco", "compacto", "4 DE 6 DESTACARAM PERDA DE FÔLEGO; 2, CONTINUIDADE DA VIRADA OPERACIONAL.",
         "CONTINUIDADE DA VIRADA OPERACIONAL"),
    ],
)
def test_gancho_da_capa_pelos_moldes(day, prefixo, formula, texto, destaque):
    s = next(x for x in edicao(day).stories if x.id.startswith(prefixo))
    g = S.gancho_raiox(s.coverage)
    assert (g.formula, g.texto, g.destaque) == (formula, texto, destaque)
    assert len(g.texto) <= S.GANCHO_MAX and g.destaque in g.texto


def test_gancho_sem_rotulo_e_empate_com_neutros():
    s = story("s", stances=["a", "a", "b", "b", "neutro"])
    s.coverage.side_a, s.coverage.side_b = "Destaca a redenção histórica", "Destaca o custo"
    g = S.gancho_raiox(s.coverage)
    assert g.formula == "sem_rotulo" and g.destaque == ""
    assert g.texto == "5 VEÍCULOS, MESMO FATO: 2 CONTARAM DE UM JEITO, 2 DE OUTRO."
    s.coverage.side_a = "Destaca o alívio"
    g = S.gancho_raiox(s.coverage)
    assert g.formula == "G0"  # 2 × 2 com 1 neutro: "metade" seria falso
    assert g.legenda == "5 veículos, mesmo fato: 2 destacaram alívio; 2, custo."


@real
def test_virada_e_atencao_da_spacex_em_21_10():
    e = edicao(21)
    s = next(x for x in e.stories if x.id.startswith("spacex"))
    v = S.virada(e, s)
    assert v.tipo == "idioma"
    assert v.texto == ("Primeiro texto em inglês: The New York Times, 20/10 13h17. "
                       "Primeiro em português: Valor Econômico, 20/10 18h17.")
    a = S.atencao(e, s)
    assert a.linhas == ["12 VEÍCULOS, 19 TEXTOS", "1º entre 1.159 assuntos do dia; 1.039 saíram em uma fonte só",
                        "14 dos 19 textos em inglês"]


def test_virada_pelos_dois_primeiros_do_mesmo_lado():
    from datetime import datetime, timedelta, timezone

    s = story("s", stances=["a", "a", "b", "b"])
    s.coverage.side_a = "Destaca o alívio fiscal e o resto"
    t0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    r = E.Relogio([E.Batida("V0", t0, "a", "pt"), E.Batida("V1", t0 + timedelta(hours=1), "a", "en"),
                   E.Batida("V2", t0 + timedelta(hours=2), "b", "pt")])
    e = edition([s], "s")
    v = S.virada(e, s, r)
    assert v.tipo == "stance" and v.texto == "Os 2 primeiros a publicar destacaram alívio fiscal."
    r.batidas[1].stance = "b"
    assert S.virada(e, s, r) is None  # primeiro em português: sem virada


@real
def test_sem_ia_a_noite_e_mercado():
    e = edicao(10)
    assert e.mode != "ai" and S.escolher_noite(e, election=True).tipo == "mercado"
