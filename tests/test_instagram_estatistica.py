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
                _topic("Protestos", [(f"W{i}", "Protestos", None) for i in range(13)], section="mundo"),
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
    # Com menos de 15 leituras não há escala confiável: nada de "fora do comum".
    assert E.numero_do_dia(hoje, history[:10] + [hoje], election=True) is None


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
