"""Aba "Analítico Político" (``edicoes/analitico.html``): todo o histórico do termômetro.

A página é refeita a cada edição a partir de todos os registros de
``data/termometro/`` (:mod:`qijournal.termometro.historico`), nos termos da
aba analítica da especificação (:mod:`qijournal.termometro.agregacao`):

- onde estamos: a média de todo o histórico, com o intervalo de 95%;
- para onde estamos indo: a tendência mensal de T e de Q, a janela recente
  comparada à base e quem puxou a mudança (editoria e veículo);
- séries semanais, rankings por veículo e por editoria (T ajustado e qualidade),
  recortes por eixo, a tabela de cada dia e a lista de todas as análises.

Notícias e opinião não se misturam: as médias, séries e rankings são das
notícias; a opinião aparece à parte. Só entram análises da versão atual do
cálculo (a agregação filtra por versão).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from .. import text
from ..config import Config
from ..termometro import agregacao
from ..termometro.referencia import PARAMETROS
from . import filters
from .termometro import (
    ANALITICO_PAGINA,
    CLASSE,
    EIXOS,
    AnaliseView,
    DistribuicaoView,
    GaugeView,
    analise_view,
    contagem,
    distribuicao,
    fmt_dec,
    fmt_ic,
    fmt_q,
    fmt_t,
    gauge,
    todas_validas,
)
from .web import brand_view

__all__ = ["ANALITICO_PAGINA", "render_analitico_page", "analitico_view"]

log = logging.getLogger(__name__)

P = agregacao.P_ANALITICO
PUXADORES_MAX = 8
DOMINIOS = (25, 50, 100)


# ── views ────────────────────────────────────────────────────────────────────


@dataclass
class TileView:
    rotulo: str
    valor: str
    detalhe: str = ""


@dataclass
class LeituraView:
    """Uma leitura de "para onde estamos indo" (tendência ou desvio)."""

    titulo: str
    leitura: str
    texto: str
    classe: str  # "esq" | "dir" | "est" | "ins" | "melhor" | "pior"


@dataclass
class PuxadorView:
    nome: str
    contribuicao: str
    inclinacao: str
    pauta: str
    leitura: str


@dataclass
class PontoView:
    left: float
    top: float
    title: str


@dataclass
class SerieView:
    titulo: str
    aria: str
    pontos: list[PontoView]
    linha: str  # path em coordenadas 0-1000 (preserveAspectRatio="none")
    faixa: str  # polígono do intervalo de 95%
    grade: list[tuple[float, str]]  # (top %, rótulo)
    eixo_x: list[tuple[float, str]]  # (left %, rótulo)
    zero: float | None = None  # top % da linha do zero (séries de T)


@dataclass
class RankItemView:
    nome: str
    valor: str
    pct: float
    ic_left: float | None
    ic_width: float | None
    rotulo: str
    classe: str
    detalhe: str
    title: str


@dataclass
class RankingView:
    titulo: str
    itens: list[RankItemView]
    escala: list[tuple[float, str]]
    zero: float
    fora: str


@dataclass
class QualidadeItemView:
    nome: str
    valor: str
    pct: float
    rotulo: str
    detalhe: str


@dataclass
class LinhaTabelaView:
    nome: str
    href: str | None
    textos: int
    validas: int
    valor: str
    rotulo: str
    classe: str
    q: str


@dataclass
class MesView:
    rotulo: str
    analises: list[AnaliseView]
    aberto: bool = False


@dataclass
class AnaliticoView:
    vazio: bool
    periodo: str
    intro: str
    gauge: GaugeView | None
    resumo: str
    distribuicao: DistribuicaoView | None
    tiles: list[TileView]
    leituras: list[LeituraView]
    puxadores: list[tuple[str, list[PuxadorView]]]
    series: list[SerieView]
    series_msg: str
    rankings: list[RankingView]
    qualidade: list[QualidadeItemView]
    qualidade_fora: str
    eixos: list[LinhaTabelaView]
    dias: list[LinhaTabelaView]
    meses: list[MesView]
    metodo: list[str]
    versao: str = PARAMETROS["versao"]
    atualizado: str = ""


# ── auxiliares ───────────────────────────────────────────────────────────────


def _dominio(valores: Iterable[float | None]) -> int:
    maior = max((abs(v) for v in valores if v is not None), default=0)
    return next((d for d in DOMINIOS if maior <= d), 100)


def _pos(valor: float, dominio: float) -> float:
    valor = max(-dominio, min(dominio, valor))
    return round((valor + dominio) / (2 * dominio) * 100, 2)


def _escala(dominio: int) -> list[tuple[float, str]]:
    meio = dominio // 2 if dominio > 25 else None
    marcas = [-dominio, *( [-meio] if meio else []), 0, *([meio] if meio else []), dominio]
    return [(_pos(m, dominio), fmt_t(m)) for m in marcas]


def _data_label(iso: str) -> str:
    try:
        return text.pt_short_date(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def _mes_label(chave: str) -> str:
    try:
        ano, mes = chave.split("-")
        return f"{text.MONTHS_PT[int(mes) - 1].capitalize()} de {ano}"
    except (ValueError, IndexError):
        return chave


def _classe_t(leitura: str) -> str:
    return {
        "rumo à esquerda": "esq",
        "mudou para a esquerda": "esq",
        "rumo à direita": "dir",
        "mudou para a direita": "dir",
        "estável": "est",
        "dentro do histórico": "est",
        "melhorando": "melhor",
        "melhorou": "melhor",
        "piorando": "pior",
        "piorou": "pior",
    }.get(leitura, "ins")


# ── blocos ───────────────────────────────────────────────────────────────────


def _tendencias(linhas: list[dict[str, Any]], inicio: str, fim: str) -> list[LeituraView]:
    t = agregacao.tendencia(linhas, inicio, fim)
    leituras = []
    for chave, titulo, fmt_val, unidade in (
        ("T", "Tendência do termômetro", fmt_dec, "ponto"),
        ("Q", "Tendência da qualidade da apuração", fmt_dec, "ponto"),
    ):
        r = t[chave]
        if r["leitura"] == "dados insuficientes":
            texto = (
                f"{contagem(r['meses'], 'mês', 'meses')} com {P['min_n_mes']} ou mais análises; a tendência pede "
                f"{P['min_meses_tendencia']}."
            )
        else:
            texto = (
                f"{fmt_val(r['por_mes'])} {unidade}s por mês (intervalo de 95%: {fmt_ic(r['ic95'], fmt_dec)}); "
                f"{fmt_val(r['variacao_no_periodo'])} de {_mes_label(r['de']).lower()} a {_mes_label(r['ate']).lower()}, "
                f"em {contagem(r['meses'], 'mês', 'meses')}."
            )
        leituras.append(LeituraView(titulo, r["leitura"].capitalize(), texto, _classe_t(r["leitura"])))
    return leituras


def _desvio(linhas: list[dict[str, Any]], inicio: str, fim: str) -> tuple[list[LeituraView], tuple, tuple, bool]:
    recente, base = agregacao.janelas_padrao(inicio, fim)
    d = agregacao.desvio(linhas, recente, base)
    janela = f"{_data_label(recente[0])} a {_data_label(recente[1])}"
    base_txt = f"{_data_label(base[0])} a {_data_label(base[1])}" if base[0] <= base[1] else "sem base"
    leituras = []
    tem_dados = d["T"]["leitura"] != "dados insuficientes"
    for chave, titulo, fmt_val in (("T", "Janela recente × histórico: termômetro", fmt_t), ("Q", "Janela recente × histórico: qualidade", fmt_q)):
        r = d[chave]
        if r["leitura"] == "dados insuficientes":
            texto = (
                f"{r['n_recente']} análise(s) de {janela} e {r['n_base']} de {base_txt}; "
                f"a comparação pede {P['min_n']} em cada janela."
            )
        else:
            texto = (
                f"{janela}: {fmt_val(r['recente'])}; {base_txt}: {fmt_val(r['base'])}. Diferença de "
                f"{fmt_dec(r['diferenca'])} (intervalo de 95%: {fmt_ic(r['ic95'], fmt_dec)})."
            )
        leituras.append(LeituraView(titulo, r["leitura"].capitalize(), texto, _classe_t(r["leitura"])))
    return leituras, recente, base, tem_dados


def _puxadores(linhas: list[dict[str, Any]], recente: tuple, base: tuple) -> list[tuple[str, list[PuxadorView]]]:
    saida = []
    for por, titulo in (("editoria", "Por editoria"), ("veiculo", "Por veículo")):
        px = agregacao.puxadores(linhas, por, recente, base)
        itens = [
            PuxadorView(
                nome=filters.plain(str(g["nome"])),
                contribuicao=fmt_dec(g["contribuicao_T"]),
                inclinacao=fmt_dec(g["efeito_inclinacao"]),
                pauta=fmt_dec(g["efeito_pauta"]),
                leitura=g["T"]["leitura"],
            )
            for g in px["grupos"][:PUXADORES_MAX]
        ]
        if itens:
            saida.append((titulo, itens))
    return saida


def _serie_t(linhas: list[dict[str, Any]], inicio: str, fim: str) -> SerieView | None:
    semanas = agregacao.serie(linhas, inicio, fim, "semana")
    if not semanas:
        return None
    dominio = _dominio([v for s in semanas for v in (s["ic95_T"] or [s["media_T"]])])
    total = len(semanas)
    pontos, sup, inf = [], [], []
    for i, s in enumerate(semanas):
        left = round(i / (total - 1) * 100, 2) if total > 1 else 50.0
        top = round(100 - _pos(s["media_T"], dominio), 2)
        ic = s["ic95_T"] or [s["media_T"], s["media_T"]]
        sup.append((left, round(100 - _pos(ic[1], dominio), 2)))
        inf.append((left, round(100 - _pos(ic[0], dominio), 2)))
        pontos.append(
            PontoView(
                left,
                top,
                f"Semana de {_data_label(s['periodo'])}: T {fmt_t(s['media_T'])} ({s['rotulo']}), "
                f"{contagem(s['n'], 'notícia', 'notícias')}",
            )
        )
    linha = " ".join(f"{'M' if i == 0 else 'L'}{p.left * 10:.1f},{p.top * 10:.1f}" for i, p in enumerate(pontos))
    faixa = (
        " ".join(f"{'M' if i == 0 else 'L'}{x * 10:.1f},{y * 10:.1f}" for i, (x, y) in enumerate(sup))
        + " "
        + " ".join(f"L{x * 10:.1f},{y * 10:.1f}" for x, y in reversed(inf))
        + " Z"
    )
    grade = [(round(100 - p, 2), r) for p, r in _escala(dominio)]
    rotulos_x = [0, total // 2, total - 1] if total > 2 else list(range(total))
    eixo_x = [(pontos[i].left, _data_label(semanas[i]["periodo"])) for i in dict.fromkeys(rotulos_x)]
    ultimo = semanas[-1]
    aria = (
        f"Média semanal de T das notícias, {total} semana(s); última: {fmt_t(ultimo['media_T'])} "
        f"({ultimo['rotulo']}). Escala de {fmt_t(-dominio)} a {fmt_t(dominio)}."
    )
    return SerieView(
        "Termômetro por semana (notícias)", aria, pontos, linha, faixa, grade, eixo_x, zero=round(100 - _pos(0, dominio), 2)
    )


def _serie_q(linhas: list[dict[str, Any]], inicio: str, fim: str) -> SerieView | None:
    semanas = [s for s in agregacao.serie(linhas, inicio, fim, "semana") if s["media_Q"] is not None]
    if not semanas:
        return None
    total = len(semanas)
    pontos, sup, inf = [], [], []
    for i, s in enumerate(semanas):
        left = round(i / (total - 1) * 100, 2) if total > 1 else 50.0
        ic = s["ic95_Q"] or [s["media_Q"], s["media_Q"]]
        sup.append((left, round(100 - max(0, min(100, ic[1])), 2)))
        inf.append((left, round(100 - max(0, min(100, ic[0])), 2)))
        pontos.append(
            PontoView(
                left,
                round(100 - max(0, min(100, s["media_Q"])), 2),
                f"Semana de {_data_label(s['periodo'])}: qualidade {fmt_q(s['media_Q'])} ({s['rotulo_Q']})",
            )
        )
    linha = " ".join(f"{'M' if i == 0 else 'L'}{p.left * 10:.1f},{p.top * 10:.1f}" for i, p in enumerate(pontos))
    faixa = (
        " ".join(f"{'M' if i == 0 else 'L'}{x * 10:.1f},{y * 10:.1f}" for i, (x, y) in enumerate(sup))
        + " "
        + " ".join(f"L{x * 10:.1f},{y * 10:.1f}" for x, y in reversed(inf))
        + " Z"
    )
    fraca, boa = P["qualidade_faixas"]
    grade = [(0.0, "100"), (round(100 - boa, 2), str(boa)), (round(100 - fraca, 2), str(fraca)), (100.0, "0")]
    rotulos_x = [0, total // 2, total - 1] if total > 2 else list(range(total))
    eixo_x = [(pontos[i].left, _data_label(semanas[i]["periodo"])) for i in dict.fromkeys(rotulos_x)]
    aria = f"Qualidade média da apuração por semana, {total} semana(s); última: {fmt_q(semanas[-1]['media_Q'])} de 100."
    return SerieView("Qualidade da apuração por semana (0 a 100)", aria, pontos, linha, faixa, grade, eixo_x)


def _ranking(linhas: list[dict[str, Any]], inicio: str, fim: str, por: str, titulo: str) -> tuple[RankingView, dict]:
    r = agregacao.ranking(linhas, inicio, fim, por=por)
    dominio = _dominio(
        [v for i in r["ranking"] for v in ([i["T_ajustado"]] + list(i["ic95_T"] or []))]
    )
    itens = []
    for i in r["ranking"]:
        ic = i["ic95_T"]
        left = _pos(ic[0], dominio) if ic else None
        nome = filters.plain(str(i["nome"]))
        itens.append(
            RankItemView(
                nome=nome,
                valor=fmt_t(i["T_ajustado"]),
                pct=_pos(i["T_ajustado"], dominio),
                ic_left=left,
                ic_width=round(max(0.6, _pos(ic[1], dominio) - left), 2) if ic else None,
                rotulo=i["rotulo"],
                classe=CLASSE.get(i["rotulo"], "eq"),
                detalhe=f"{contagem(i['n'], 'notícia', 'notícias')} · média bruta {fmt_t(i['media_T'])}",
                title=(
                    f"{nome}: T ajustado {fmt_t(i['T_ajustado'])} ({i['rotulo']}); média {fmt_t(i['media_T'])}, "
                    f"intervalo de 95% {fmt_ic(ic)}; {i['n']} notícias"
                ),
            )
        )
    fora = ""
    if r["fora_do_ranking"]:
        nomes = ", ".join(f"{filters.plain(str(f['nome']))} ({f['n']})" for f in r["fora_do_ranking"])
        fora = f"Fora do ranking (menos de {P['min_n']} notícias com T): {nomes}."
    return RankingView(titulo, itens, _escala(dominio), _pos(0, dominio), fora), r


def _qualidade(r: Mapping[str, Any]) -> list[QualidadeItemView]:
    return [
        QualidadeItemView(
            nome=filters.plain(str(i["nome"])),
            valor=fmt_q(i["Q_ajustado"]),
            pct=round(max(0.0, min(100.0, i["Q_ajustado"])), 2),
            rotulo=i["rotulo_Q"],
            detalhe=f"média bruta {fmt_q(i['media_Q'])} em {contagem(i['n_Q'], 'texto', 'textos')}",
        )
        for i in r["ranking_qualidade"]
    ]


def _linha_tabela(nome: str, s: Mapping[str, Any], href: str | None = None) -> LinhaTabelaView:
    return LinhaTabelaView(
        nome=nome,
        href=href,
        textos=s["n_total"],
        validas=s["n"],
        valor=fmt_t(s["media_T"]),
        rotulo=s["rotulo"] or "—",
        classe=CLASSE.get(s["rotulo"] or "", "na"),
        q=fmt_q(s["media_Q"]),
    )


# ── view completa ────────────────────────────────────────────────────────────


def analitico_view(
    registros: Iterable[Mapping[str, Any]], *, edicoes: Iterable[str] = (), edition_date: str | None = None
) -> AnaliticoView:
    """Todo o histórico agregado (``registros``: um por dia, de :mod:`qijournal.termometro.historico`).
    ``edicoes``: datas com edição arquivada (link do dia na tabela)."""
    registros = list(registros)
    analises = [a for a in todas_validas(registros) if a["versao_calculo"] == P["versao"]]
    metodo = [
        f"Cada texto compilado nas edições passa pelo Termômetro de Viés Editorial (versão {P['versao']} do cálculo): "
        "a IA aponta sinais com a citação literal e o cálculo confere cada citação, aplica os pesos dos 11 critérios "
        "e dá T de −100 (esquerda) a +100 (direita) e a qualidade da apuração Q de 0 a 100.",
        "Médias, séries e rankings são das notícias; opinião (colunas, editoriais, análises) fica à parte. Um texto "
        "sem dimensão política ou inconclusivo conta nos totais, mas não na média de T.",
        f"Ranking: grupos com {P['min_n']} ou mais notícias com T; o T ajustado puxa a média do grupo para a média "
        f"geral com peso {P['peso_media_geral']}, o que segura grupos pequenos perto do centro. Intervalos de 95%.",
        f"Para onde estamos indo: tendência mensal (regressão de todas as análises sobre o mês; meses com "
        f"{P['min_n_mes']}+ análises, mínimo de {P['min_meses_tendencia']} meses); janela recente dos últimos "
        f"{P['janela_recente_dias']} dias (no máximo metade do histórico) comparada ao resto; os puxadores dividem a "
        "mudança entre inclinação (o grupo mudou) e pauta (o grupo ganhou ou perdeu espaço).",
        "Mede o tratamento dado pelo texto (verbos, fontes, rótulos, enquadramento…), não a veracidade dos fatos "
        "nem a intenção do veículo.",
    ]
    if not analises:
        return AnaliticoView(
            vazio=True, periodo="", intro="Nenhuma análise no histórico ainda.", gauge=None, resumo="",
            distribuicao=None, tiles=[], leituras=[], puxadores=[], series=[], series_msg="", rankings=[],
            qualidade=[], qualidade_fora="", eixos=[], dias=[], meses=[], metodo=metodo,
            atualizado=_data_label(edition_date) if edition_date else "",
        )

    linhas = agregacao.acumular(analises)
    datas = sorted(a["data_publicacao"][:10] for a in analises)
    inicio, fim = datas[0], max(datas[-1], (edition_date or datas[-1])[:10])
    geral = agregacao.resumo(linhas)
    tudo = agregacao.resumo(linhas, incluir_opiniao=True)
    opiniao = agregacao.resumo([l for l in linhas if l["tipo"] == "opiniao"], incluir_opiniao=True)
    veiculos = len({a["veiculo"] for a in analises})
    editorias = len({a["editoria"] for a in analises})
    dias_com = len({a["data_publicacao"][:10] for a in analises})

    medidor = gauge(geral["media_T"], geral["ic95_T"], geral["rotulo"], descricao="Média de todo o histórico (notícias)")
    resumo = (
        f"{medidor.rotulo} · T {medidor.valor} na média de {contagem(geral['n'], 'notícia', 'notícias')} com dimensão política"
        if medidor
        else "Ainda não há notícia com T (só textos sem dimensão política ou inconclusivos)."
    )
    tiles = [
        TileView("Textos analisados", text.format_number_pt(tudo["n_total"], 0), f"{veiculos} veículos · {editorias} editorias · {dias_com} dias"),
        TileView(
            "Notícias com T",
            text.format_number_pt(geral["n"], 0),
            f"{geral['n_nao_se_aplica']} sem dimensão política · {geral['n_inconclusivo']} inconclusivas",
        ),
        TileView(
            "Qualidade da apuração",
            fmt_q(geral["media_Q"]),
            f"de 100 · {geral['rotulo_Q']}" if geral["rotulo_Q"] else "sem avaliação",
        ),
        TileView(
            "Opinião (à parte)",
            text.format_number_pt(opiniao["n_total"], 0),
            f"T médio {fmt_t(opiniao['media_T'])}" if opiniao["media_T"] is not None else "sem T",
        ),
    ]

    leituras = _tendencias(linhas, inicio, fim)
    desvios, recente, base, tem_desvio = _desvio(linhas, inicio, fim)
    leituras += desvios
    puxadores = _puxadores(linhas, recente, base) if tem_desvio else []

    series = [s for s in (_serie_t(linhas, inicio, fim), _serie_q(linhas, inicio, fim)) if s]
    series_msg = (
        ""
        if series
        else f"As séries semanais começam quando uma semana reúne {P['min_n_semana']} ou mais notícias com T."
    )
    rank_v, r_v = _ranking(linhas, inicio, fim, "veiculo", "Veículos")
    rank_e, _r_e = _ranking(linhas, inicio, fim, "editoria", "Editorias")
    qualidade_fora = (
        "" if r_v["ranking_qualidade"] else f"O ranking de qualidade começa com {P['min_n']} notícias com T por veículo."
    )

    eixos = []
    for chave, nome in EIXOS.items():
        s = agregacao.resumo(linhas, eixo=chave)
        if s["n_total"]:
            eixos.append(_linha_tabela(nome, s))

    disponiveis = set(edicoes)
    # Os textos publicados num dia aparecem no Termômetro do dia da edição em que
    # foram analisados (em geral a do dia seguinte): o link vai para essa edição.
    edicao_do_dia: dict[str, Counter[str]] = {}
    for a in analises:
        if a.get("analisado_em") in disponiveis:
            edicao_do_dia.setdefault(a["data_publicacao"][:10], Counter())[a["analisado_em"]] += 1
    dias = []
    for dia in sorted({l["dia"] for l in linhas}, reverse=True):
        s = agregacao.resumo(linhas, dia, dia)
        s_tudo = agregacao.resumo(linhas, dia, dia, incluir_opiniao=True)
        edicao = edicao_do_dia[dia].most_common(1)[0][0] if dia in edicao_do_dia else None
        linha = _linha_tabela(_data_label(dia), s, f"{edicao}.html#termometro" if edicao else None)
        linha.textos = s_tudo["n_total"]
        dias.append(linha)

    por_mes: dict[str, list[dict[str, Any]]] = {}
    for a in sorted(analises, key=lambda a: (a["data_publicacao"], a.get("veiculo") or ""), reverse=True):
        por_mes.setdefault(a["data_publicacao"][:7], []).append(a)
    meses = [
        MesView(f"{_mes_label(m)} · {contagem(len(lista), 'texto', 'textos')}", [analise_view(a, evidencias=False) for a in lista], aberto=i == 0)
        for i, (m, lista) in enumerate(por_mes.items())
    ]

    return AnaliticoView(
        vazio=False,
        periodo=f"{_data_label(inicio)} a {_data_label(fim)}",
        intro=(
            f"Todo o histórico do termômetro: {contagem(tudo['n_total'], 'texto analisado', 'textos analisados')} "
            f"de {contagem(veiculos, 'veículo', 'veículos')}, publicados de {_data_label(inicio)} a {_data_label(fim)}."
        ),
        gauge=medidor,
        resumo=resumo,
        distribuicao=distribuicao(geral["rotulos"]),
        tiles=tiles,
        leituras=leituras,
        puxadores=puxadores,
        series=series,
        series_msg=series_msg,
        rankings=[rank_v, rank_e],
        qualidade=_qualidade(r_v),
        qualidade_fora=qualidade_fora,
        eixos=eixos,
        dias=dias,
        meses=meses,
        metodo=metodo,
        atualizado=_data_label(edition_date) if edition_date else "",
    )


def render_analitico_page(
    registros: Iterable[Mapping[str, Any]],
    config: Config,
    *,
    home_href: str,
    archive_href: str,
    edition_date: str | None = None,
    edicoes: Iterable[str] = (),
) -> str:
    """Página ``edicoes/analitico.html`` (autocontida, como as demais)."""
    view = analitico_view(registros, edicoes=edicoes, edition_date=edition_date)
    brand = brand_view(config)
    template = filters.environment().get_template("analitico.html.j2")
    html = template.render(
        a=view,
        brand=brand,
        home_href=filters.safe_href(home_href) or "../",
        archive_href=filters.safe_href(archive_href) or "./",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
    )
    log.info(
        "Analítico Político renderizado: %d texto(s) no histórico, %d KB",
        sum(len(m.analises) for m in view.meses),
        len(html.encode("utf-8")) // 1024,
    )
    return html
