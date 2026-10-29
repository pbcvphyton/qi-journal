"""Manifesto (§4.7): o "Comece aqui 2.0", que sai uma vez no D0 e fica fixado em 1º.

1. Capa laranja chapada: "MESMO FATO, VÁRIAS VERSÕES."
2. O funil da edição do D0, com a data: notícias → assuntos → coberturas comparadas → matérias.
3. Pauta solitária: "x em cada 10 assuntos saem em uma fonte só", com a matriz de pontos do dia.
4. A divisão: "Em x de cada 10 assuntos comparados, os veículos contaram sem divisão", com waffle.
5. Como fazemos e o que não fazemos.
6. CTA: os 3 posts do dia.

As contas dos slides 3 e 4 vêm do histórico com IA, de 14/10 (quando o agrupamento passou a
cruzar idiomas, §9) até a data da edição, com período e n no rodapé. "x em cada 10" é sempre
arredondado para baixo: o post fica fixado, e um número com data nunca fica falso.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from qijournal.models import Edition

from .. import estatistica as E
from .. import graficos as G
from ..conteudo import Peca, Slide, alt, edition_day, finish_caption, short_date
from .stories import DESCRICAO_AGENDA

SLOT = "lancamento"
TITULO = "Manifesto"
SELO = "Comece aqui"
SERIE_IA_DESDE = date(2026, 10, 14)  # 1ª edição com IA; antes disso o agrupamento não cruza idiomas
GANCHO_CAPA = "Mesmo fato, várias versões."
SUB_CAPA = "O que aconteceu, quanto o mercado andou e como cada veículo contou."
LINHA1 = "Mesmo fato, várias versões: como a Triangula lê a imprensa"
REGRAS = ("Sem adjetivo, sempre com fonte.", "Sem foto de jornal.", "Sem ranking de veículos.")
ENQUADRAMENTO = "Enquadramento é escolha do que destacar, não erro."
METODO_IA = "Classificação de ênfase feita por IA sobre os textos publicados."
AGRUPAMENTO = "O agrupamento de assuntos é automático e tende a superestimar exclusivos."
CTA = "Siga."
CTA_LEGENDA = "Siga: todo dia, 3 posts."
HASHTAGS = "#triangula #jornalismo #imprensa #dados"

# Os 3 posts do dia (§3.1), com o que muda no fim de semana e quando não há racha.
AGENDA = tuple(
    (hora, nome, DESCRICAO_AGENDA[nome], obs)
    for hora, nome, obs in (
        ("07h", "Em 1 minuto", "sáb. e dom., 08h30"),
        ("12h", "Número do dia", ""),
        ("18h30", "Raio-X", "dia sem assunto dividido: Placar do dia"),
    )
)


# ── Contas ────────────────────────────────────────────────────────────────


@dataclass
class Historico:
    """As contas do histórico com IA (de :data:`SERIE_IA_DESDE` até a edição)."""

    inicio: date
    fim: date
    edicoes: int
    assuntos: int
    uma_fonte: int
    coberturas: int
    divididas: int  # pelo menos 1 veículo de cada lado (o "dividida" do selo de cobertura)

    @property
    def sem_divisao(self) -> int:
        return self.coberturas - self.divididas

    @property
    def periodo(self) -> str:
        """``"de 14/10 a 26/10"``."""
        return f"de {short_date(self.inicio)} a {short_date(self.fim)}"

    @property
    def periodo_frase(self) -> str:
        """O período no começo de frase: ``"De 14/10 a 26/10"``."""
        return "D" + self.periodo[1:]


def historico(edition: Edition, history: Iterable[Edition]) -> Historico:
    """Soma as edições com IA do histórico (e a da edição), uma por data, até a data da edição."""
    fim = edition_day(edition)
    por_dia = {date.fromisoformat(e.date): e for e in history}
    por_dia[fim] = edition
    dias = sorted(d for d, e in por_dia.items() if e.mode == "ai" and SERIE_IA_DESDE <= d <= fim)
    h = Historico(dias[0] if dias else fim, fim, len(dias), 0, 0, 0, 0)
    for d in dias:
        e = por_dia[d]
        c = E.concentracao(e, election=False)
        if c is not None:
            h.assuntos += c.assuntos
            h.uma_fonte += c.uma_fonte
        for cov, _ in E.coverages(e):
            h.coberturas += 1
            h.divididas += cov.count("a") >= 1 and cov.count("b") >= 1
    return h


def em_cada_10(x: int, y: int) -> str:
    """Quantos em cada 10, sempre arredondado para baixo: ``88,7%`` → ``"mais de 8"``;
    ``90%`` → ``"9"``; abaixo de 10%, ``"menos de 1"``."""
    if y <= 0:
        return "0"
    dez = 10 * x / y
    k = math.floor(dez + 1e-9)
    if k == 0:
        return "menos de 1"
    return str(k) if math.isclose(dez, k, abs_tol=1e-9) else f"mais de {k}"


def proporcao(x: int, y: int, cada: str = "em") -> str:
    """``"Mais de 8 em cada 10"`` (``cada="de"``: ``"mais de 8 de cada 10"``); com base abaixo de
    :data:`.estatistica.PCT_MIN`, a contagem crua (``"5 de 12"``): proporção só com base ≥ 20."""
    if y < E.PCT_MIN:
        return f"{E.num(x)} de {E.num(y)}"
    return f"{em_cada_10(x, y)} {cada} cada 10"


def _pct(x: int, y: int) -> str:
    """``" (88,8%)"``, só com base ≥ :data:`.estatistica.PCT_MIN`."""
    return f" ({E.num(100 * x / y, 1)}%)" if y >= E.PCT_MIN else ""


# ── Slides ────────────────────────────────────────────────────────────────


def _capa() -> Slide:
    ctx = {
        "selo": SELO,
        "titulo": GANCHO_CAPA,
        "tam": G.tamanho_gancho(GANCHO_CAPA, max_linhas=4, tam_max=236, tam_min=180),
        "sub": SUB_CAPA,
        "chamada": "como a Triangula lê a imprensa · arraste →",
        "nota": "",
        "section": None,
    }
    return Slide("manifesto_capa", ctx, alt=alt(f"Manifesto da Triangula. {GANCHO_CAPA} {SUB_CAPA}"))


def _funil(edition: Edition) -> Slide:
    day = short_date(edition_day(edition))
    fontes = edition.rationale.outlets if edition.rationale else 0
    etapas = [
        {"n": E.num(edition.stats.articles_collected), "rotulo": "notícias lidas",
         "desc": f"de {fontes} fontes" if fontes else "nas fontes que lemos"},
        {"n": E.num(len(edition.index)), "rotulo": "assuntos", "desc": "as notícias do mesmo fato, agrupadas"},
        {"n": E.num(len(E.coverages(edition))), "rotulo": "coberturas comparadas",
         "desc": "a IA compara o que cada veículo destacou"},
        {"n": E.num(len(edition.stories)), "rotulo": "matérias", "desc": "a edição do dia, com as fontes"},
    ]
    linha = " → ".join(f"{x['n']} {x['rotulo'].removesuffix(' lidas')}" for x in etapas)
    titulo = f"De {etapas[0]['n']} notícias a {etapas[3]['n']} matérias"
    ctx = {
        "selo": SELO,
        "kicker": f"O funil de {day}",
        "titulo": titulo,
        "destaque": f"{etapas[3]['n']} matérias",
        "tam": G.tamanho_gancho(titulo, max_linhas=2, tam_max=120, tam_min=84),
        "etapas": etapas,
        # coluna do número na largura do maior (Archivo 62%/900 a 120 px), para os rótulos alinharem
        "col_num": int(max(G.largura_texto(x["n"], "hook", 120) for x in etapas)) + 8,
        "pe": f"Edição de {day}. Agrupamento automático das notícias por assunto; comparação e matérias por IA.",
        "nota": "",
        "section": None,
    }
    texto = f"O funil de {day}. {titulo}. Em {day}: {linha}. " + " ".join(f"{x['n']} {x['rotulo']}: {x['desc']}." for x in etapas) + f" {ctx['pe']}"
    return Slide("manifesto_funil", ctx, alt=alt(texto))


def _pauta_solitaria(edition: Edition, h: Historico) -> Slide:
    x = proporcao(h.uma_fonte, h.assuntos)
    titulo = f"{x[:1].upper()}{x[1:]} assuntos saem em uma fonte só"
    contagens = [len(E.topic_sources(t)) for t in edition.index]
    svg, descricao = G.matriz_pontos(contagens, altura_max=560)
    dia = short_date(edition_day(edition))
    texto = f"Cada ponto é um assunto de {dia}. Os mais claros saíram em uma fonte só: a pauta solitária."
    pe = (
        f"{h.periodo_frase}: {E.num(h.uma_fonte)} de {E.num(h.assuntos)} assuntos{_pct(h.uma_fonte, h.assuntos)} "
        f"em uma fonte só, em {h.edicoes} edições com IA, nas fontes que lemos. {AGRUPAMENTO}"
    )
    ctx = {
        "selo": SELO,
        "kicker": "Pauta solitária",
        "titulo": titulo,
        "destaque": x.removeprefix("mais de ").removeprefix("menos de "),
        "tam": G.tamanho_gancho(titulo, max_linhas=3, tam_max=120, tam_min=88),
        "grafico": svg,
        "texto": texto,
        "pe": pe,
        "nota": "",
        "section": None,
    }
    return Slide("manifesto_dados", ctx, alt=alt(f"Pauta solitária. {titulo}. {descricao} {texto} {pe}"))


def _divisao(h: Historico) -> Slide:
    x = proporcao(h.sem_divisao, h.coberturas, "de")
    titulo = f"Em {x} assuntos comparados, os veículos contaram sem divisão."
    n = E.num(h.coberturas)
    if h.coberturas > 100:  # um quadrado por cobertura não cabe: cada um vale 1% delas
        total, marcados = 100, round(100 * h.divididas / h.coberturas)
        unidade, plural = f"1% das {n} coberturas comparadas", f"quadrados; cada um, 1% das {n} coberturas"
    else:
        total, marcados = h.coberturas, h.divididas
        unidade, plural = "cobertura comparada", "coberturas comparadas"
    svg, descricao = G.waffle(
        total,
        marcados,
        rotulos=("divididas", "sem divisão"),
        unidade=unidade,
        unidade_plural=plural,
        por_linha=10,
        lado=44,
        gap=6,
    )
    texto = "Nos outros, a gente mostra a diferença."
    pe = (
        f"{h.periodo_frase}: {E.num(h.sem_divisao)} de {E.num(h.coberturas)} coberturas comparadas"
        f"{_pct(h.sem_divisao, h.coberturas)} sem divisão, em {h.edicoes} edições. Dividida = pelo menos 1 veículo "
        f"de cada lado. {METODO_IA}"
    )
    ctx = {
        "selo": SELO,
        "kicker": "A divisão",
        "titulo": titulo,
        "destaque": "",
        "tam": G.tamanho_gancho(titulo, max_linhas=3, tam_max=104, tam_min=80),
        "grafico": svg,
        "texto": texto,
        "texto_destaque": "diferença",
        "pe": pe,
        "nota": "",
        "section": None,
    }
    return Slide("manifesto_dados", ctx, alt=alt(f"A divisão. {titulo} {descricao} {texto} {pe}"))


def _como_fazemos() -> Slide:
    ctx = {
        "selo": SELO,
        "kicker": "Como fazemos e o que não fazemos",
        "regras": list(REGRAS),
        "frase": ENQUADRAMENTO,
        "pe": METODO_IA,
        "nota": "",
        "section": None,
    }
    texto = "Como fazemos e o que não fazemos. " + " ".join(REGRAS) + f" {ENQUADRAMENTO} {METODO_IA}"
    return Slide("manifesto_texto", ctx, alt=alt(texto))


def _cta() -> Slide:
    itens = [{"hora": h, "nome": n, "desc": d, "obs": o} for h, n, d, o in AGENDA]
    ctx = {
        "selo": SELO,
        "kicker": "Todo dia, 3 posts",
        "agenda": itens,
        "cta": CTA,
        "nota": "",
        "section": None,
    }
    linhas = " · ".join(f"{i['hora']} {i['nome']}" + (f" ({i['obs']})" if i["obs"] else "") for i in itens)
    return Slide("manifesto_cta", ctx, alt=alt(f"Todo dia, 3 posts: {linhas}. {CTA}"))


# ── A peça ────────────────────────────────────────────────────────────────


def _legenda(edition: Edition, h: Historico) -> str:
    day = short_date(edition_day(edition))
    fontes = edition.rationale.outlets if edition.rationale else 0
    agenda = " · ".join(f"{hh} {n}" + (f" ({o})" if o else "") for hh, n, _, o in AGENDA)
    corpo = [
        f"▸ Em {day}: {E.num(edition.stats.articles_collected)} notícias" + (f" de {fontes} fontes" if fontes else "")
        + f" → {E.num(len(edition.index))} assuntos → {E.num(len(E.coverages(edition)))} coberturas comparadas"
        f" → {E.num(len(edition.stories))} matérias.",
        f"▸ Pauta solitária: {h.periodo}, {E.num(h.uma_fonte)} de {E.num(h.assuntos)} assuntos"
        f"{_pct(h.uma_fonte, h.assuntos)} saíram em uma fonte só, nas fontes que lemos.",
        f"▸ A divisão: no mesmo período, {E.num(h.sem_divisao)} de {E.num(h.coberturas)} coberturas comparadas"
        f"{_pct(h.sem_divisao, h.coberturas)} saíram sem divisão. Nas outras, o Raio-X mostra o que cada lado destacou.",
        "▸ " + " ".join(REGRAS),
        f"▸ Todo dia, 3 posts: {agenda}.",
        f"Como medimos: agrupamento automático das notícias por assunto; {METODO_IA[0].lower()}{METODO_IA[1:]} "
        f"{ENQUADRAMENTO}",
        CTA_LEGENDA,
        HASHTAGS,
    ]
    return finish_caption([LINHA1, "\n".join(corpo)])


def manifesto(edition: Edition, brand: dict[str, Any], history: Sequence[Edition]) -> Peca:
    """O carrossel de 6 slides do manifesto, com os números da edição (o D0) e do histórico com IA.

    ``history``: :func:`.estatistica.load_history` até o dia da edição (a edição entra de todo jeito).
    """
    h = historico(edition, history)
    slides = [_capa(), _funil(edition), _pauta_solitaria(edition, h), _divisao(h), _como_fazemos(), _cta()]
    return Peca(slot=SLOT, kind="carousel", title=TITULO, slides=slides, caption=_legenda(edition, h))
