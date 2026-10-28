"""Contas dos posts (placar, concentração, Número do dia, mercado, relógio) em edições sintéticas."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from qijournal.instagram import estatistica as E
from qijournal.models import Edition


def _edition(day: str, *, stories=(), compared=(), index=(), quotes=(), mode: str = "ai") -> Edition:
    return Edition.from_dict(
        {
            "date": day,
            "date_label": day,
            "generated_at": f"{day}T09:00:00+00:00",
            "mode": mode,
            "model": None,
            "editorial": "",
            "briefing": [],
            "lead": stories[0]["id"] if stories else "",
            "secondary": [],
            "highlights": [],
            "sections": [],
            "stories": list(stories),
            "quotes": list(quotes),
            "weather": [],
            "compared": list(compared),
            "index": list(index),
        }
    )


def _story(sid: str, headline: str, *, section: str = "brasil", dek: str = "", importance: int = 3, coverage=None) -> dict:
    return {
        "id": sid,
        "section": section,
        "headline": headline,
        "dek": dek,
        "body": [],
        "sources": [],
        "article_ids": [],
        "importance": importance,
        "coverage": coverage,
    }


def _coverage(a: int, b: int, n: int = 0, *, topic: str = "Assunto", section: str | None = None) -> dict:
    outlets = [{"name": f"A{i}", "stance": "a", "framing": "x"} for i in range(a)]
    outlets += [{"name": f"B{i}", "stance": "b", "framing": "y"} for i in range(b)]
    outlets += [{"name": f"N{i}", "stance": "neutro", "framing": "z"} for i in range(n)]
    return {"topic": topic, "conclusion": "", "outlets": outlets, "side_a": "lado a", "side_b": "lado b", "section": section}


def _topic(title: str, items: list[tuple[str, str, str | None]], *, story_id: str | None = None, section: str = "brasil") -> dict:
    return {
        "section": section,
        "title": title,
        "story_id": story_id,
        "items": [{"source": s, "title": t, "url": f"https://x/{i}", "published": p} for i, (s, t, p) in enumerate(items)],
    }


def _quote(qid: str, value: float, change: float | None, as_of: str, label: str = "") -> dict:
    return {"id": qid, "label": label or qid, "value": value, "display": str(value), "change_pct": change, "as_of": as_of}


def test_fracao_so_mostra_porcentagem_com_base_suficiente():
    assert E.fracao(5, 19) == "5 de 19"
    assert E.fracao(13, 85) == "13 de 85 (15%)"


def test_horario_e_duracao():
    at = datetime(2026, 10, 20, 16, 17, tzinfo=timezone.utc)  # 13h17 BRT
    assert E.hora(at) == "13h17"
    assert E.hora(at, date(2026, 10, 21)) == "20/10 13h17"
    assert E.duracao(timedelta(hours=16, minutes=44)) == "16h44"
    assert E.duracao(timedelta(minutes=38)) == "38 min"


def test_placar_conta_rachas_com_dois_de_cada_lado_e_tira_politica_na_eleicao():
    edition = _edition(
        "2026-10-21",
        stories=[
            _story("s1", "Racha", coverage=_coverage(3, 2)),
            _story("s2", "Um contra um", coverage=_coverage(1, 1, 2)),  # 4 veículos, mas não é racha
            _story("s3", "Poucos", coverage=_coverage(2, 1)),  # menos de 4 veículos: fora da base
            _story("s4", "Política", section="politica", coverage=_coverage(2, 2)),
        ],
        compared=[_coverage(2, 2, 1, section="mundo")],
    )
    eleicao = E.placar(edition, election=True)
    assert (eleicao.rachas, eleicao.base) == (2, 3)
    assert eleicao.texto == "2 de 3"
    assert eleicao.por_secao["mundo"] == (1, 1)
    depois = E.placar(edition, election=False)
    assert (depois.rachas, depois.base) == (3, 4)


def test_concentracao_conta_fontes_e_o_topo_ignora_orgaos_publicos():
    many = [(f"Veículo {i}", "Assunto grande", None) for i in range(12)]
    edition = _edition(
        "2026-10-21",
        index=[
            _topic("Assunto grande", many + [("STF", "Assunto grande", None)]),
            _topic("Só o STF e o STJ", [("STF", "x", None), ("STJ", "x", None), ("Valor Econômico", "x", None)]),
            _topic("Solo", [("g1", "x", None)]),
            _topic("Solo 2", [("Folha de S.Paulo", "x", None), ("Folha de S.Paulo", "y", None)]),
        ],
    )
    c = E.concentracao(edition, election=True)
    assert (c.assuntos, c.uma_fonte, c.com3, c.com10) == (4, 2, 2, 1)
    assert c.topo.title == "Assunto grande" and c.topo_veiculos == 12
    assert c.uma_fonte_pct == 50


def test_numero_triangulado_exige_tres_veiculos_e_prefere_o_da_maioria():
    edition = _edition(
        "2026-10-16",
        stories=[
            _story("ibc", "IBC-Br sobe 0,3% em agosto", importance=3),
            _story("mei", "Câmara aprova teto de R$ 140 mil para MEI", importance=5),
        ],
        index=[
            _topic(
                "IBC-Br",
                [(s, "IBC-Br sobe 0,3% em agosto", None) for s in ("Valor Econômico", "g1", "Folha de S.Paulo")],
                story_id="ibc",
            ),
            _topic(
                "MEI",
                [(f"V{i}", "Teto de R$ 140 mil" if i < 3 else "Câmara aprova MEI", None) for i in range(10)],
                story_id="mei",
            ),
        ],
    )
    numeros = E.triangulados(edition, election=True)
    assert [n.valor for n in numeros] == ["0,3%", "R$ 140 mil"]
    assert numeros[0].selo == "no título de 3 de 3 veículos"
    n = E.numero_do_dia(edition, [edition], election=True)
    assert n.tipo == "triangulado" and n.valor == "0,3%" and n.story_id == "ibc"


def test_numero_triangulado_normaliza_bilhao_e_nao_confunde_com_outro_numero():
    edition = _edition(
        "2026-10-21",
        stories=[_story("caju", "iFood compra a Caju por R$ 1 bilhão")],
        index=[
            _topic(
                "Caju",
                [
                    ("Brazil Journal", "iFood paga R$ 1 bilhão pela Caju", None),
                    ("Estadão", "iFood compra Caju por cerca de R$ 1 bi", None),
                    ("Folha de S.Paulo", "iFood compra a Caju por R$ 1 bi", None),
                    ("g1", "iFood compra Caju por R$ 11 bi", None),  # outro número: não confirma
                ],
                story_id="caju",
            )
        ],
    )
    (n,) = E.triangulados(edition, election=True)
    assert n.valor == "R$ 1 bi"
    assert n.selo == "no título de 3 de 4 veículos"


def test_numero_triangulado_fica_fora_de_politica_e_pesquisa_na_eleicao():
    stories = [
        _story("pol", "Candidato movimentou R$ 5 bilhões", section="politica"),
        _story("pesq", "Pesquisa Datafolha mostra 46% em empate técnico", section="brasil"),
    ]
    index = [
        _topic("pol", [(f"V{i}", "R$ 5 bilhões", None) for i in range(5)], story_id="pol"),
        _topic("pesq", [(f"V{i}", "46%", None) for i in range(5)], story_id="pesq"),
    ]
    edition = _edition("2026-10-21", stories=stories, index=index)
    assert E.triangulados(edition, election=True) == []
    assert {n.valor for n in E.triangulados(edition, election=False)} == {"R$ 5 bi", "46%"}


def test_numero_do_dia_nao_repete_o_da_semana_e_cai_para_o_assunto_mais_coberto():
    def day(d: str) -> Edition:
        return _edition(
            d,
            stories=[_story("apple", "Apple vale US$ 5 trilhões", section="tecnologia")],
            index=[
                _topic("Apple", [(f"V{i}", "Apple vale US$ 5 trilhões", None) for i in range(4)], story_id="apple"),
                _topic("Protestos", [(f"W{i}", f"Protestos tomam as ruas ({i})", None) for i in range(13)], section="mundo"),
            ],
        )

    ontem, hoje = day("2026-10-14"), day("2026-10-15")
    assert E.numero_do_dia(ontem, [ontem], election=True).valor == "US$ 5 tri"
    n = E.numero_do_dia(hoje, [ontem, hoje], election=True)
    assert n.tipo == "concentracao" and n.valor == "13 veículos" and n.tese == "Protestos"


def test_sem_ia_nao_ha_numero():
    edition = _edition("2026-10-05", mode="heuristic")
    assert E.numero_do_dia(edition, [edition], election=True) is None


def _history_usd(changes: list[float]) -> list[Edition]:
    start = date(2026, 9, 29)
    out = []
    value = 5.0
    for i, ch in enumerate(changes):
        d = start + timedelta(days=i)
        value *= 1 + ch / 100
        out.append(_edition(d.isoformat(), quotes=[_quote("usd", round(value, 4), ch, f"{d.isoformat()}T13:00:00+00:00", "Dólar")]))
    return out


def test_mercado_fora_do_comum_vira_o_numero_com_a_hora_da_leitura():
    history = _history_usd([0.3, -0.2, 0.25, -0.35, 0.1, -0.15, 0.2, -0.3, 0.4, -0.1, 0.15, -0.25, 0.3, -0.2, 0.1, 0.05])
    hoje = _edition("2026-10-15", quotes=[_quote("usd", 4.8, -4.42, "2026-10-15T17:06:00+00:00", "Dólar")])
    n = E.numero_do_dia(hoje, history + [hoje], election=True)
    assert n.tipo == "mercado"
    assert n.valor == "−4,42%" and n.tese == "Dólar cai 4,42%"
    assert n.selo.endswith("leitura das 14h06")  # o dólar não tem fechamento: diz a hora
    # Com menos de 15 leituras não há escala confiável: nada de "fora do comum"; sai o acumulado.
    n = E.numero_do_dia(hoje, history[:10] + [hoje], election=True)
    assert n.tipo == "acumulado" and n.passo == 5 and n.tese == "Dólar acumula −4,3% desde 29/09"


def test_serie_uma_leitura_por_dia_e_acumulado_ponta_a_ponta():
    eds = [
        _edition("2026-10-17", quotes=[_quote("brent", 105.0, 1.0, "2026-10-17T20:00:00+00:00")]),
        # Fim de semana: a mesma leitura de sexta repetida não vira ponto novo.
        _edition("2026-10-18", quotes=[_quote("brent", 105.0, 1.0, "2026-10-17T20:00:00+00:00")]),
        _edition("2026-10-19", quotes=[_quote("brent", 110.0, 4.76, "2026-10-19T12:00:00+00:00")]),
        _edition("2026-10-20", quotes=[_quote("brent", 100.0, -9.1, "2026-10-20T12:00:00+00:00")]),
    ]
    pontos = E.serie(eds, "brent")
    assert [p.value for p in pontos] == [105.0, 110.0, 100.0]
    a = E.acumulado(pontos)
    assert round(a.pct, 2) == -4.76
    assert a.high.value == 110.0 and a.low.value == 100.0


def test_leitura_de_fechamento_so_para_pregao_depois_das_17h():
    assert E.is_close("ibov", datetime(2026, 10, 20, 20, 19, tzinfo=timezone.utc))
    assert not E.is_close("ibov", datetime(2026, 10, 5, 16, 51, tzinfo=timezone.utc))  # 13h51: intradiário
    assert not E.is_close("usd", datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc))


def test_relogio_da_noticia_primeiro_horario_de_cada_veiculo():
    edition = _edition(
        "2026-10-21",
        stories=[_story("spx", "SpaceX e Tesla avaliam fusão", coverage=_coverage(1, 1))],
        index=[
            _topic(
                "SpaceX",
                [
                    ("A0", "x", "2026-10-20T16:17:00+00:00"),
                    ("B0", "x", "2026-10-21T09:01:00+00:00"),
                    ("A0", "x", "2026-10-20T18:00:00+00:00"),  # 2º texto do mesmo veículo: fica o 1º
                    ("STF", "x", "2026-10-20T10:00:00+00:00"),  # órgão público fora
                    ("C", "x", None),  # sem horário
                ],
                story_id="spx",
            )
        ],
    )
    r = E.relogio(edition, edition.story("spx"))
    assert [(b.source, b.stance) for b in r.batidas] == [("A0", "a"), ("B0", "b")]
    assert E.duracao(r.span) == "16h44"


def test_historico_le_so_o_periodo_pedido(tmp_path):
    for d in ("2026-09-01", "2026-10-20", "2026-10-21", "2026-10-22"):
        (tmp_path / f"{d}.json").write_text(json.dumps(_edition(d).to_dict()), encoding="utf-8")
    (tmp_path / "latest.json").write_text("{}", encoding="utf-8")
    eds = E.load_history(tmp_path, date(2026, 10, 21), days=30)
    assert [e.date for e in eds] == ["2026-10-20", "2026-10-21"]


# ── Dados reais (data/AAAA-MM-DD.json, com IA de 14 a 26/10) ───────────────

import functools  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-21.json").is_file(), reason="sem os JSONs reais em data/")


@functools.lru_cache(maxsize=None)
def edicao(day: str) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day}.json").read_text(encoding="utf-8")))


def historia(day: str) -> list[Edition]:
    return E.load_history(DATA, date(2026, 10, int(day)), 90)


def materia(day: str, prefixo: str):
    return next(s for s in edicao(day).stories if s.id.startswith(prefixo))


@real
def test_filtro_eleitoral_por_conteudo_nos_casos_reais():
    tse = materia("20", "tse-intima-meta")
    assert tse.section == "juridico" and E.story_is_electoral(tse)
    iss = materia("22", "stf-decide-que-iss")
    assert not E.story_is_electoral(iss)  # "6 votos a 5": voto de ministro não é eleição
    alberta = next(c for c in edicao("21").compared if "Alberta" in c.topic)
    assert alberta.section == "mundo" and not E.is_electoral(E.coverage_text(alberta), alberta.section)
    assert not E.is_electoral("Flávio Dino suspende emendas e cobra transparência do Congresso", "juridico")
    assert E.is_electoral("Flávio Bolsonaro visita o Nordeste", "brasil")
    # A IBC-Br de 16/10 cita "pesquisa da Reuters" num enquadramento: pesquisa de mercado não é eleitoral.
    assert not E.story_is_electoral(materia("16", "ibc-br"))


def test_filtro_eleitoral_familias_e_excecoes():
    nomes = ["Lula", "Flávio Bolsonaro", "Bolsonaro"]
    assert E.is_electoral("Candidatos ao governo debatem na TV", "brasil", nomes)
    assert not E.is_electoral("Candidatos ao governo debatem na TV", "mundo", nomes)  # forte não vale em Mundo
    assert E.is_electoral("Lula viaja à Ásia", "mundo", nomes)  # nome vale em toda seção
    assert not E.is_electoral("Lulas gigantes são filmadas no Pacífico", "natureza", nomes)  # borda de palavra
    assert E.is_electoral("Deputado do PL apresenta emenda", "brasil", nomes)
    assert not E.is_electoral("Câmara aprova o PL 2338 sobre IA", "brasil", nomes)  # PL = projeto de lei
    assert not E.is_electoral("Deputado do PL apresenta emenda", "mundo", nomes)  # partido só nas 4 seções
    assert not E.is_electoral("Ministro dá voto decisivo e eleição no Canadá termina", "juridico", nomes)
    assert E.is_electoral("Datafolha mostra empate técnico", "brasil", nomes)
    assert not E.is_electoral("Pesquisa do IBGE mostra alta de 0,5 ponto percentual", "brasil", nomes)


@real
def test_placar_real_tira_o_eleitoral_e_vai_sem_porcentagem():
    p21 = E.placar(edicao("21"), election=True)
    p19 = E.placar(edicao("19"), election=True)
    # A spec simulou 4 de 26 e 0 de 16 sem o filtro por conteúdo; com ele saem
    # "Nunes Marques diz que TSE..." (21/10) e duas coberturas com Lula e Flávio Bolsonaro (19/10).
    assert (p21.rachas, p21.base) == (4, 25) and p21.texto_dia == "4 de 25"
    assert (p19.rachas, p19.base) == (0, 14) and p19.texto_dia == "0 de 14"
    assert "%" not in p21.texto_dia and p21.texto == "4 de 25 (16%)"
    assert len(p21.itens) == p21.base
    consenso, _story = E.maior_consenso(edicao("19"), election=True)
    assert consenso.topic.startswith("Protestos") and len(consenso.outlets) == 8
    assert consenso.count("a") == consenso.count("b") == 0
    rachas = E.rachas_do_dia(edicao("21"), election=True)
    assert len(rachas) == 4 and all(E.is_racha(c) for c, _ in rachas)


@real
def test_concentracao_real_de_21_10():
    c = E.concentracao(edicao("21"), election=True)
    assert (c.assuntos, c.uma_fonte, c.com3, c.com5, c.com10) == (1159, 1039, 60, 22, 1)
    assert c.topo_veiculos == 12 and c.topo.story_id.startswith("spacex")
    assert c.linha == "1.159 assuntos · 60 com 3+ · 22 com 5+ · 1 com 10+"


@real
def test_relogio_real_da_spacex_em_21_10():
    e = edicao("21")
    r = E.relogio(e, materia("21", "spacex"))
    assert len(r.batidas) == 12
    first, last = r.batidas[0], r.batidas[-1]
    assert first.source == "The New York Times" and E.hora(first.at, date(2026, 10, 21)) == "20/10 13h17"
    assert first.lang == "en"
    assert last.source == "The Wall Street Journal" and E.hora(last.at) == "06h01" and last.stance is None
    assert E.duracao(r.span) == "16h43" and E.relogio_valido(r)
    assert not E.relogio_valido(E.Relogio(r.batidas[:3]))  # menos de 4 veículos
    assert not E.relogio_valido(E.Relogio([r.batidas[0], E.Batida("X", r.batidas[0].at + timedelta(minutes=10))] * 2))


@real
def test_acumulados_e_juro_real_ate_21_10():
    h = historia("21")
    brent = E.serie(h, "brent")
    a = E.acumulado(brent)
    assert (a.first.value, a.last.value, round(a.pct, 1)) == (100.03, 110.14, 10.1)
    ibov = E.serie(h, "ibov")
    a = E.acumulado(ibov)
    assert round(a.pct, 1) == 13.8 and len(ibov) == 14
    assert round(a.high.value) == 209067 and a.high.day == date(2026, 10, 9)
    assert round(E.acumulado(E.serie(h, "usd")).pct, 1) == -4.1
    assert round(E.juro_real(edicao("21")), 2) == 8.77
    # Dias úteis sem leitura do Ibovespa; 12/10 é feriado e não conta.
    assert E.pregoes_faltantes(ibov, "ibov") == [date(2026, 9, 30), date(2026, 10, 13)]
    assert E.pregoes_faltantes(brent, "brent") == []


def test_selo_de_leitura_da_cotacao():
    close = E.Ponto(datetime(2026, 10, 20, 20, 19, tzinfo=timezone.utc), 208296, 1.5)
    assert E.selo_leitura("ibov", close) == "fech. 20/10"
    intraday = E.Ponto(datetime(2026, 10, 5, 16, 51, tzinfo=timezone.utc), 200000, 8.25)
    assert E.selo_leitura("ibov", intraday) == "leitura das 13h51"
    btc = E.Ponto(datetime(2026, 10, 15, 9, 55, tzinfo=timezone.utc), 81198, -2.36)
    assert E.selo_leitura("btc", btc) == "leitura das 06h55"
    assert E.selo_leitura("btc", btc, date(2026, 10, 16)) == "leitura de 15/10, 06h55"
    stale = E.Ponto(datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc), 5.03, 0.0)  # 06h00 com 0,00%
    assert E.selo_leitura("usd", stale) == ""


def test_placar_texto_dia_nunca_tem_porcentagem():
    assert E.Placar(5, 36).texto_dia == "5 de 36" and E.Placar(5, 36).texto == "5 de 36 (14%)"


def test_adjetivo_da_casa_corta_a_manchete_ou_usa_o_dek():
    caju = "iFood Benefícios compra a Caju por cerca de R$ 1 bi e cria gigante de benefícios"
    assert E.sem_adj_casa(caju, ["iFood compra Caju"] * 8) == "iFood Benefícios compra a Caju por cerca de R$ 1 bi"
    titulos = ["SpaceX e Tesla avaliam fusão histórica"] + ["SpaceX e Tesla avaliam fusão"] * 11  # 1 de 12
    assert E.sem_adj_casa("SpaceX e Tesla avaliam fusão histórica após salto", titulos, dek="O dek.") == "O dek."
    # Com o adjetivo no título de metade ou mais dos veículos, ele fica.
    assert E.sem_adj_casa("Embraer bate recorde de pedidos", ["recorde"] * 3 + ["outro"] * 3) == "Embraer bate recorde de pedidos"
    assert E.sem_adj_casa("Sem adjetivo nenhum aqui", []) == "Sem adjetivo nenhum aqui"
    assert E.sem_adj_casa("1,8 mil presos e onda histórica", []) == ""  # não corta na vírgula decimal


@real
def test_capa_sem_adjetivo_com_os_titulos_reais():
    e = edicao("21")
    caju = materia("21", "ifood-beneficios")
    assert E.capa_sem_adj(e, caju) == "iFood Benefícios compra a Caju por cerca de R$ 1 bi"
    spacex = materia("21", "spacex")
    assert E.capa_sem_adj(e, spacex) == E._plain(spacex.dek)


def test_numero_de_destaque_com_qualificador():
    assert E.numero_destaque("iFood Benefícios compra a Caju por cerca de R$ 1 bi e cria gigante") == ("CERCA DE", "R$ 1 bi")
    assert E.numero_destaque("Governo prevê renegociação de até R$ 90 bilhões") == ("ATÉ", "R$ 90 bi")
    assert E.numero_destaque("IBC-Br sobe 0,3% em agosto de 2026") == ("", "0,3%")
    assert E.numero_destaque("Lei de 2025 muda regras do setor") is None  # ano solto não é número de capa


@real
@pytest.mark.parametrize(
    "dia, raiox, passo, tipo, qualificador, valor, tese, selo",
    [
        ("15", None, 4, "mercado", "", "−2,36%", "Bitcoin cai 2,36%", "Yahoo Finance · leitura das 06h55"),
        ("16", None, 2, "triangulado", "", "0,3%", "IBC-Br sobe 0,3% em agosto com recuo do agro", "no título de 5 de 5 veículos"),
        ("18", None, 3, "concentracao", "", "12 veículos", None, "o assunto mais coberto entre 663 do dia"),
        ("21", "spacex", 5, "acumulado", "", "+13,8%", "Ibovespa acumula +13,8% desde 28/09", "fech. 28/09 → fech. 20/10"),
        ("22", None, 2, "triangulado", "ATÉ", "R$ 90 bi", None, "no título de 5 de 6 veículos"),
        ("24", "ipca-15", 2, "triangulado", "", "US$ 35,7 bi", None, "no título de 5 de 5 veículos"),
    ],
)
def test_numero_do_dia_nos_dias_reais(dia, raiox, passo, tipo, qualificador, valor, tese, selo):
    e = edicao(dia)
    exclude = {e.lead} | ({materia(dia, raiox).id} if raiox else set())
    n = E.numero_do_dia(e, historia(dia), election=True, exclude=exclude)
    assert (n.passo, n.tipo, n.qualificador, n.valor, n.selo) == (passo, tipo, qualificador, valor, selo)
    if tese:
        assert n.tese == tese
    assert len(n.tese.split()) <= E.TESE_MAX_PALAVRAS


@real
def test_numero_do_dia_prova_por_tipo():
    btc = E.numero_do_dia(edicao("15"), historia("15"), election=True)
    assert btc.prova["qid"] == "btc" and round(btc.prova["z"], 1) == 2.6 and btc.prova["variacoes"][-1]["hoje"]
    assert len(btc.prova["variacoes"]) <= 30 and btc.prova["anotacao"] == "hoje: −2,36%, 2,6 vezes a variação típica"
    ibc = E.numero_do_dia(edicao("16"), historia("16"), election=True)
    assert ibc.prova["confirmados"] == ibc.prova["total"] == 5
    assert ibc.prova["frase"] == "5 de 5 veículos puseram 0,3% no título"
    assert all({"nome", "confirmado", "hora", "at"} <= set(v) for v in ibc.prova["veiculos"])
    conc = E.numero_do_dia(edicao("18"), historia("18"), election=True)
    assert conc.prova["assuntos"] == 663 and len(conc.prova["contagens"]) == 663
    assert conc.prova["contagens"][conc.prova["destaque"]] >= 12
    ibov = E.numero_do_dia(edicao("21"), historia("21"), election=True, exclude={materia("21", "spacex").id})
    p = ibov.prova
    assert (p["primeiro"]["valor_fmt"], p["ultimo"]["valor_fmt"], p["leituras"]) == ("182.991", "208.296", 14)
    assert (p["max"]["valor_fmt"], p["max"]["dia"]) == ("209.067", "09/10")
    assert [f["dia"] for f in p["faltantes"]] == ["30/09", "13/10"]
    assert {o["qid"]: o["pct_fmt"] for o in p["outros"]}["brent"] == "+10,1%"


@real
def test_numero_do_dia_usa_as_chaves_publicadas_e_nunca_fica_vazio():
    e, h = edicao("21"), historia("21")
    sem_spacex = {materia("21", "spacex").id}
    n = E.numero_do_dia(e, h, election=True, exclude=sem_spacex, usados={"acum:ibov"})
    assert n.chave == "acum:brent" and n.passo == 5
    todos = {f"acum:{q}" for q in E.ACUMULADO_IDS}
    n = E.numero_do_dia(e, h, election=True, exclude=sem_spacex, usados=todos)
    assert n.tipo == "juro_real" and n.valor == "8,77%" and n.tese == "Juro real ex-post de 8,77%"
    assert n.prova["ipca_ref"] == "set/26" and n.prova["selic"] == 13.75
    n = E.numero_do_dia(e, h, election=True, exclude=sem_spacex, usados=todos | {"juroreal"})
    assert n.passo == 7 and n.chave == "acum:ibov"  # o 12:00 nunca fica vazio


@real
def test_numero_do_dia_sem_ia_so_mercado():
    e = edicao("10")
    assert e.mode != "ai"
    n = E.numero_do_dia(e, historia("10"), election=True)
    assert n.tipo in ("mercado", "acumulado", "juro_real")


@real
def test_selo_de_cobertura_do_em_1_minuto():
    e = edicao("21")
    spacex = materia("21", "spacex")
    assert E.selo_cobertura(e, spacex, spacex.id) == "12 veículos · dividida 6 × 2 · Raio-X às 18h30"
    assert E.selo_cobertura(e, materia("21", "ifood-beneficios"), spacex.id) == "8 veículos · sem divisão"
