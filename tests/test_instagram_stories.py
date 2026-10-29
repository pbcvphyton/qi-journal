"""Stories da manhã (§4.6): as 4 telas com os JSONs reais de data/ (sem Chromium)."""

from __future__ import annotations

import functools
import json
from datetime import date
from pathlib import Path

import pytest

from qijournal.config import load_config
from qijournal.instagram import conteudo, estatistica as E, gerar, redacao
from qijournal.instagram.conteudo import Peca
from qijournal.instagram.formatos import stories as ST
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-26.json").is_file(), reason="sem os JSONs reais em data/")
AGENDA = [("07h", "Em 1 minuto"), ("12h", "Número do dia"), ("18h30", "Raio-X")]
DIAS_IA = [f"{d:02d}" for d in range(14, 27)]


@functools.lru_cache(maxsize=None)
def edicao(day: str) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day}.json").read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def historia(day: str) -> list[Edition]:
    return E.load_history(DATA, date(2026, 10, int(day)), 90)


def eleicao(day: str) -> bool:
    return date(2026, 10, int(day)) <= date(2026, 10, 27)


def stories(day: str, *, election: bool | None = None, agenda=AGENDA, **kw) -> Peca | None:
    el = eleicao(day) if election is None else election
    return ST.stories_manha(edicao(day), {}, historia(day), election=el, agenda=agenda, **kw)


def tela(p: Peca, template: str):
    return next((s for s in p.slides if s.template == template), None)


def textos(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in textos(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in textos(v)]
    return []


def veiculos(e: Edition) -> list[str]:
    nomes = {i.source for t in e.index for i in t.items if i.source}
    for s in e.stories:
        nomes |= {x.name for x in s.sources if x.name}
        if s.coverage:
            nomes |= {o.name for o in s.coverage.outlets}
    return sorted(nomes)


# ── Agenda ────────────────────────────────────────────────────────────────


def test_hora_curta():
    assert ST.hora_curta("07:00") == "07h"
    assert ST.hora_curta("18:30") == "18h30"
    assert ST.hora_curta("08:30") == "08h30"
    assert ST.hora_curta("12:00") == "12h"


def test_agenda_so_com_as_pecas_geradas_e_em_ordem_de_horario():
    pecas = [
        Peca("noite", "carousel", "Raio-X da cobertura", []),
        Peca("stories", "story", "Stories da manhã", []),
        Peca("manha", "carousel", "Em 1 minuto", []),
        Peca("lancamento", "carousel", "Manifesto", []),
        Peca("numero", "carousel", "Número do dia", []),
    ]
    semana = {"manha": "07:00", "numero": "12:00", "noite": "18:30"}
    assert ST.agenda_das_pecas(pecas, semana) == AGENDA
    fds = {"manha": "08:30", "numero": "12:00", "noite": "18:30"}
    assert ST.agenda_das_pecas(pecas, fds)[0] == ("08h30", "Em 1 minuto")
    # o que não foi gerado não entra
    sem_numero = [p for p in pecas if p.slot != "numero"]
    assert ST.agenda_das_pecas(sem_numero, semana) == [("07h", "Em 1 minuto"), ("18h30", "Raio-X")]
    placar = [Peca("noite", "carousel", "Placar do dia", [])]
    assert ST.agenda_das_pecas(placar, semana) == [("18h30", "Placar do dia")]


# ── As 4 telas ────────────────────────────────────────────────────────────


@real
def test_21_10_tem_as_4_telas_na_ordem():
    p = stories("21")
    assert (p.slot, p.kind, p.title) == ("stories", "story", "Stories da manhã")
    assert [s.template for s in p.slides] == ["st_bomdia", "st_manchete", "st_mercado", "st_feed"]
    assert p.story_ids == [next(s.id for s in edicao("21").stories if s.id.startswith("ifood"))]


@real
def test_bom_dia_sem_clima_com_numero_vivo_e_agenda():
    p = stories("21")
    ctx = p.slides[0].ctx
    assert ctx["selo"] == "Bom dia · quarta, 21/10"
    assert ctx["titulo"] == "Hoje lemos 1.640 notícias de 46 fontes"
    assert ctx["destaque"] == "1.640 notícias" and ctx["destaque"] in ctx["titulo"]
    assert ctx["funil"] == "→ 1.159 assuntos → 14 matérias na edição"
    assert [(i["hora"], i["nome"]) for i in ctx["agenda"]] == AGENDA
    assert all(i["desc"] for i in ctx["agenda"])
    assert "Hoje no feed: 07h Em 1 minuto · 12h Número do dia · 18h30 Raio-X." in p.slides[0].alt
    # sai o clima: nada de tempo nem de cidade em nenhuma tela
    for s in p.slides:
        assert "weather" not in s.ctx
        assert not any(w in t.lower() for t in textos(s.ctx) + [s.alt] for w in ("clima", "°c", "temperatura"))


@real
def test_bom_dia_so_mostra_o_que_foi_gerado():
    p = stories("21", agenda=[("07h", "Em 1 minuto"), ("18h30", "Placar do dia")])
    assert [i["nome"] for i in p.slides[0].ctx["agenda"]] == ["Em 1 minuto", "Placar do dia"]
    p = stories("21", agenda=())
    assert p.slides[0].ctx["agenda"] == [] and "Hoje no feed" not in p.slides[0].alt


@real
def test_manchete_no_modo_eleitoral_pula_a_politica_e_corta_o_adjetivo():
    e = edicao("21")
    ctx = tela(stories("21"), "st_manchete").ctx
    # a manchete da edição (Willer Tomaz) é eleitoral; a 1ª não política é a do iFood, sem o "gigante"
    assert e.story(e.lead).section == "politica"
    assert ctx["titulo"] == "iFood Benefícios compra a Caju por cerca de R$ 1 bi"
    assert (ctx["qual"], ctx["numero"]) == ("CERCA DE", "R$ 1 bi")
    assert ctx["selo"] == "Destaque · QUA 21 OUT"
    assert ctx["selo_cob"] == "8 veículos · sem divisão"
    assert ctx["rotulo_por_que"] == "Por que importa" and ctx["por_que"].startswith("A aquisição reconfigura")
    assert ST.NUM_MIN <= ctx["tam_num"] <= ST.NUM_MAX


@real
def test_manchete_fora_do_periodo_eleitoral_e_a_da_edicao():
    e = edicao("21")
    ctx = tela(stories("21", election=False), "st_manchete").ctx
    assert ctx["titulo"].startswith("Willer Tomaz movimentou R$ 5 bilhões")
    assert ctx["selo"] == "Manchete · QUA 21 OUT"
    assert stories("21", election=False).story_ids == [e.lead]


@real
def test_manchete_usa_a_capa_do_em_1_minuto_e_o_selo_do_raiox():
    e = edicao("21")
    spacex = next(s for s in e.stories if s.id.startswith("spacex"))
    ifood = next(s for s in e.stories if s.id.startswith("ifood"))
    p = stories("21", capa=ifood, raiox_story_id=ifood.id)
    assert tela(p, "st_manchete").ctx["selo_cob"].endswith("· Raio-X às 18h30")
    outra = next(s for s in e.stories[2:] if ST.textos_manchete(e, s, election=True, nomes=E.NOMES_ELEICAO))
    assert stories("21", capa=outra).story_ids == [outra.id]
    # a capa do SpaceX não serve: o título é a linha fina (o "histórica" não sai) e o "por que importa"
    # tem "gigantes" (adjetivo da casa); volta à escolha normal
    assert stories("21", capa=spacex).story_ids == [ifood.id]
    # a capa política não passa no modo eleitoral: volta à escolha normal
    p = stories("21", capa=e.story(e.lead))
    assert p.story_ids == [ifood.id]


@real
def test_24_10_numero_em_destaque_e_26_10_sem_tela_da_manchete():
    ctx = tela(stories("24"), "st_manchete").ctx
    assert ctx["titulo"].startswith("Petróleo supera US$ 116 o barril")
    assert (ctx["qual"], ctx["numero"]) == ("", "US$ 116")
    # 26/10: tudo é eleitoral, menos o esporte, cujo "por que importa" tem "cenário" e cujo título
    # é a linha fina (o "histórica" não sai): sem matéria que sirva, a tela some e ficam 3
    p = stories("26")
    assert [s.template for s in p.slides] == ["st_bomdia", "st_mercado", "st_feed"]
    assert p.story_ids == []


@real
def test_textos_manchete_recusa_palavra_proibida_e_eleitoral():
    e = edicao("25")
    for story in e.stories:
        t = ST.textos_manchete(e, story, election=True, nomes=E.NOMES_ELEICAO)
        if t is None:
            continue
        titulo, frase = t
        assert not redacao.palavras_proibidas(titulo) and not redacao.palavras_proibidas(frase)
        assert not E.is_electoral(f"{titulo} {frase}", story.section)
        assert "eleição" not in f"{titulo} {frase}".lower()
    # "Eleição presidencial deixa fatura fiscal pesada…" (brasil): o reforço local a tira
    fatura = next(s for s in e.stories if "fatura fiscal" in s.headline)
    assert ST.textos_manchete(e, fatura, election=True, nomes=E.NOMES_ELEICAO) is None
    assert ST.textos_manchete(e, fatura, election=False, nomes=E.NOMES_ELEICAO) is not None


@real
def test_mercado_com_acumulado_desde_o_inicio_da_serie_e_hora_da_leitura():
    ctx = tela(stories("21"), "st_mercado").ctx
    por_id = {x["id"]: x for x in ctx["linhas"]}
    assert list(por_id) == ["usd", "ibov", "spx", "brent", "btc"]
    assert (por_id["ibov"]["change"], por_id["ibov"]["selo"], por_id["ibov"]["acum"], por_id["ibov"]["desde"]) == (
        "+1,57%", "fech. 20/10", "+13,8%", "28/09")
    assert (por_id["brent"]["selo"], por_id["brent"]["acum"], por_id["brent"]["desde"]) == ("leitura das 06h45", "+10,1%", "29/09")
    assert (por_id["usd"]["selo"], por_id["usd"]["acum"]) == ("leitura das 06h00", "−4,1%")
    # o início da série não é o mesmo para todos (28/09 e 29/09): a data vai em cada linha
    assert (ctx["col_acum"], ctx["por_linha"]) == ("acumulado", True)
    assert ctx["faixa"] == "Selic 13,75% · IPCA 12m 4,58% (set) · juro real ex-post 8,77%"
    assert ctx["titulo"] == "Mercado antes da abertura"
    assert "Ibovespa 208.296 pts (+1,57% no dia, fech. 20/10; +13,8% desde 28/09)" in tela(stories("21"), "st_mercado").alt


@real
def test_mercado_sem_variacao_na_leitura_parada_e_titulo_do_fim_de_semana():
    dolar = next(x for x in tela(stories("19"), "st_mercado").ctx["linhas"] if x["id"] == "usd")
    # 19/10: o dólar das 06h veio com 0,00% (leitura parada): sem variação, e o acumulado vai até a anterior
    assert (dolar["change"], dolar["selo"]) == ("", "leitura sem variação")
    assert dolar["acum"] and dolar["ate"] == "17/10, 06h58"
    assert tela(stories("24"), "st_mercado").ctx["titulo"] == "Mercado: as últimas leituras"  # sábado


@real
def test_chamada_so_com_o_em_1_minuto_e_com_o_horario_da_agenda():
    ctx = tela(stories("24", agenda=[("08h30", "Em 1 minuto"), ("12h", "Número do dia")]), "st_feed").ctx
    assert ctx["kicker"] == "No feed às 08h30"
    assert ctx["destaque"] in ctx["titulo"]
    assert tela(stories("21", agenda=[("07h", "Terminal da manhã")]), "st_feed") is None


@real
def test_dia_sem_ia_fica_com_bom_dia_e_mercado():
    p = ST.stories_manha(edicao("10"), {}, historia("10"), election=True,
                         agenda=[("08h30", "Terminal da manhã"), ("12h", "Número do dia"), ("18h30", "Mercado em linha")])
    assert [s.template for s in p.slides] == ["st_bomdia", "st_mercado"]
    assert p.slides[0].ctx["titulo"] == "Hoje lemos 1.521 notícias de 43 fontes"


@real
@pytest.mark.parametrize("dia", DIAS_IA)
def test_lint_aprova_todos_os_dias_e_nada_de_frase_falsa(dia):
    p = stories(dia)
    e = edicao(dia)
    assert redacao.lint(p, election=eleicao(dia), veiculos=veiculos(e)) == []
    assert redacao.lint(p, election=False, veiculos=veiculos(e)) == []
    tudo = " ".join(t for s in p.slides for t in textos(s.ctx) + [s.alt]).lower()
    for frase in ("um robô", "sem opinião", "mais de 1.600", "mais de 100 fontes", "mais de 40 veículos"):
        assert frase not in tudo
    # "fontes" (o rationale conta órgãos públicos), nunca "veículos", no número do dia
    assert "fontes" in p.slides[0].ctx["titulo"] and "veículos" not in p.slides[0].ctx["titulo"]
    for s in p.slides:
        assert len(s.alt) <= redacao.ALT_MAX


@real
@pytest.mark.parametrize("handle", [None, "triangula"])
def test_templates_renderizam_com_assinatura_em_todas_as_telas(handle):
    """Sem Chromium: só o Jinja (StrictUndefined pega chave faltando no ctx)."""
    env = gerar.environment()
    brand = {**gerar.brand_context(load_config(ROOT)), "handle": handle or ""}
    for dia in ("19", "21", "24", "26"):
        p = stories(dia)
        for i, s in enumerate(p.slides, 1):
            html = env.get_template(f"{s.template}.html.j2").render(
                brand=brand, s=conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
            )
            assert "t-dark v2 story" in html
            assert 'class="assin"' in html
            assert ("@triangula" in html) == bool(handle)
            assert handle or "<span>triangula</span>" in html
            assert "°C" not in html
