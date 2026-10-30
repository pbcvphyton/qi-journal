"""Número do dia (12:00, §4.4): carrossel de 3 com a capa laranja chapada.

1. Capa: o qualificador ("ATÉ"), o número, a tese em uma frase e a prova em mono.
2. A prova: o gráfico do tipo (lista de verificação, matriz de pontos, barras de
   variação, linha da série ou barras de juros), 2 linhas de contexto e a base.
3. Como medimos: método, fonte, período, base, limite, a ordem de escolha e o CTA.

O número vem de :func:`.estatistica.numero_do_dia`; o texto, de moldes sobre os campos
dele. Nada de IA.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable, Iterable

from markupsafe import Markup

from qijournal.models import Edition, Story

from .. import estatistica as E
from .. import graficos as G
from ..conteudo import Peca, Slide, edition_day, finish_caption, hashtags, kicker_date, plain, short_date
from ..redacao import LINHA1_MAX, palavras_proibidas
from .mercado import PREFIXO, SUFIXO, nome_corrido, valor_fmt

SLOT = "numero"
SERIE = "numero"
CTA = "Mande para quem acha que leu tudo."
HASHTAG = "#dados"
NUM_MAX, NUM_MIN = 360, 200  # px do número da capa (data-fit encolhe até o mínimo)
LARGURA = G.LARGURA
QUADRO = 1122  # altura útil do slide (1350 − 116 da faixa − 112 da assinatura)
GRAFICO_MAX = 600  # linha e barras não passam disso, mesmo com espaço sobrando
GRAFICO_MIN = 300
CHECKLIST_LINHA_MIN = 46  # px por veículo na lista de verificação (nome em 34 px)

# A cadeia de escolha (§4.4), na voz do slide 3.
PASSOS = (
    "Mercado fora do comum: 3 vezes a variação típica",
    "O mesmo número no título de 3 ou mais veículos",
    "Assunto com 12 ou mais veículos",
    "Mercado: 2 vezes a variação típica",
    "Maior acumulado da série",
    "Juro real ex-post",
    "Maior acumulado, mesmo repetido",
)

NOTA = {
    "triangulado": "fonte: títulos dos veículos",
    "concentracao": "fonte: índice da edição",
    "mercado": "fonte: Yahoo Finance",
    "acumulado": "fonte: Yahoo Finance",
    "juro_real": "fonte: Banco Central",
}

LIMITE = {
    "triangulado": "Só conta o número escrito em algarismos e com a unidade (R$, US$, %, bi); "
    "o agrupamento dos títulos em assuntos é automático.",
    "concentracao": "O agrupamento de assuntos é automático e tende a superestimar exclusivos.",
    "mercado": "Uma leitura por dia, no horário da coleta: a variação de uma leitura da manhã pode mudar até o fechamento.",
    "acumulado": "Ponta a ponta entre duas leituras; dias sem leitura não entram na conta.",
    "juro_real": "Ex-post: desconta a inflação passada de 12 meses, não a esperada.",
}

METODO_LEGENDA = {
    "triangulado": "o mesmo número, com a unidade, no título de veículos diferentes que publicaram o assunto; "
    "nenhum título é reproduzido.",
    "concentracao": "veículos diferentes que publicaram o assunto, no agrupamento automático da edição; "
    "o agrupamento tende a superestimar exclusivos.",
    "mercado": "a variação da leitura dividida pela variação típica do ativo "
    "(escala robusta: desvio absoluto mediano × 1,4826).",
    "acumulado": "uma leitura por dia (Yahoo Finance), variação ponta a ponta; não é soma de variações diárias.",
    "juro_real": "(1 + Selic) ÷ (1 + IPCA de 12 meses) − 1, com as taxas do Banco Central.",
}


# ── Utilidades ────────────────────────────────────────────────────────────


def selo_serie(numero_serie: int, day: date | None = None) -> str:
    """``"NÚMERO DO DIA Nº 003 · QUA 21 OUT"``; sem número de série, sem o "Nº"."""
    base = f"NÚMERO DO DIA Nº {numero_serie:03d}" if numero_serie else "NÚMERO DO DIA"
    return f"{base} · {kicker_date(day)}" if day else base


def _nome_serie(numero_serie: int) -> str:
    return f"Número do dia nº {numero_serie}" if numero_serie else "Número do dia"


def _quando(qid: str, d: dict[str, Any], day: date) -> str:
    """De quando é a leitura, com a data sempre que não é do dia: ``"fech. 28/09"``,
    ``"leitura de 28/09, 23h34"``, ``"leitura das 04h30"``."""
    return E.selo_leitura(qid, E.Ponto(d["as_of"], d["valor"], d.get("pct")), day) or d["dia"]


def tamanho_numero(valor: str, *, largura: float = LARGURA) -> int:
    """O maior px (até 360) em que o número cabe numa linha da área útil; nunca abaixo de 200."""
    w = G.largura_texto(valor.upper(), "hook", 100) / 100
    return int(max(NUM_MIN, min(NUM_MAX, (largura - 8) / w if w else NUM_MAX)))


def _divide_unidade(n: E.Numero) -> tuple[str, str]:
    """``"12 veículos"`` → ``("12", "veículos")``: na capa, a palavra vai embaixo, menor."""
    m = re.fullmatch(r"(\d[\d.,]*)\s+([^\d\s].*)", n.valor)
    if n.tipo == "concentracao" and m:
        return m.group(1), m.group(2)
    return n.valor, ""


def _vezes(z: float) -> str:
    return "vez" if round(z, 1) < 2 else "vezes"


def _prova_capa(n: E.Numero, day: date) -> str:
    """A prova em mono da capa (o ``Numero.selo``, com as datas e a base quando faltam)."""
    p = n.prova
    if n.tipo == "acumulado":
        return f"{_quando(p['qid'], p['primeiro'], day)} → {_quando(p['qid'], p['ultimo'], day)} · {p['leituras']} leituras"
    if n.tipo == "mercado":
        return f"{p['leitura']} · {E.num(p['z'], 1)} {_vezes(p['z'])} a variação típica"
    return n.selo


def _cabe(*candidatas: str, limite: int = LINHA1_MAX) -> str:
    """A primeira candidata com até ``limite`` caracteres (a última, cortada, se nenhuma couber)."""
    for c in candidatas:
        if c and len(c) <= limite:
            return c
    ultima = candidatas[-1]
    return ultima[: limite - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _limpo(texto: str) -> bool:
    return bool(texto) and not palavras_proibidas(texto)


def _horas(veiculos: list[dict[str, Any]], day: date) -> tuple[str, str]:
    ats = [v["at"] for v in veiculos if v.get("at")]
    if not ats:
        return "", ""
    return E.hora(min(ats), day), E.hora(max(ats), day)


def _lista(nomes: list[str]) -> str:
    """``["a", "b", "c"]`` → ``"a, b e c"``."""
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _milhar(texto: str) -> str:
    """Contagens com ponto de milhar ("1039 de 1159" → "1.039 de 1.159"); só para textos sem ano."""
    return re.sub(r"(?<![\d.,])(\d{4,})(?![\d.,]\d)", lambda m: E.num(int(m.group(1))), texto)


def _materia(edition: Edition, n: E.Numero) -> Story | None:
    return next((s for s in edition.stories if s.id == n.story_id), None) if n.story_id else None


def _fato(n: E.Numero, edition: Edition, limite: int = 170) -> str:
    """De que é o número, para o slide 2 se sustentar sozinho: a linha fina da matéria (sem palavra
    proibida do lint e com até ``limite`` caracteres); senão, a tese. Se a tese da capa saiu da linha
    fina, só o resto dela ("Puxado por forte demanda…"), ou "": o slide 2 não repete a capa."""
    story = _materia(edition, n)
    dek = " ".join(plain(story.dek).split()) if story is not None else ""
    tese = " ".join(n.tese.rstrip(".").split())
    if dek and E._fold(tese) in E._fold(dek):
        resto = dek[len(tese):].lstrip(" ,;:—-") if dek.startswith(tese) else ""
        return resto[:1].upper() + resto[1:] if len(resto.split()) >= 4 and _limpo(resto) else ""
    return dek if _limpo(dek) and len(dek) <= limite else f"O número: {n.tese}."


def _data_iso(value: str) -> str:
    try:
        return short_date(date.fromisoformat(value))
    except ValueError:
        return ""


# ── Altura do gráfico do slide 2 ─────────────────────────────────────────


def _linhas(texto: str, fonte: str, tam: float) -> int:
    # Noto Serif é um pouco mais larga que a Archivo de rótulo: a conta erra para mais linhas.
    largura = LARGURA * (0.94 if fonte == "label" else 1)
    return max(1, len(G.linhas_texto(texto, fonte, tam, largura)))


def tamanho_titulo(titulo: str) -> int:
    """O px do título-achado do slide 2: em 2 linhas se couber a partir de 76 px; senão, em 3."""
    for linhas, minimo in ((2, 76), (3, 64)):
        tam = G.tamanho_gancho(titulo, max_linhas=linhas, tam_max=104, tam_min=minimo)
        if len(G.linhas_texto(titulo.upper(), "hook", tam, LARGURA * 0.985, tracking=-0.01)) <= linhas:
            return tam
    return 64


def altura_grafico(titulo: str, tam: int, contexto: list[str], pe: str, teto: int = GRAFICO_MAX) -> int:
    """Quanto sobra para o gráfico no slide 2 (até ``teto``), com as medidas do ``numero_prova.html.j2``."""
    linhas_titulo = len(G.linhas_texto(titulo.upper(), "hook", tam, LARGURA * 0.985, tracking=-0.01))
    usado = 60  # "A PROVA" e a margem
    usado += 14 + linhas_titulo * tam * 0.92 + 0.24 * tam  # título (data-fit põe folga em cima e embaixo)
    usado += 44  # margem do gráfico
    if contexto:
        usado += 36 + 28 + sum(_linhas(c, "label", 32) * 32 * 1.32 for c in contexto) + 12 * (len(contexto) - 1)
    usado += 20 + _linhas(pe, "mono", 22) * 22 * 1.4
    return int(max(GRAFICO_MIN, min(teto, QUADRO - usado - 24)))


def colunas_matriz(total: int, altura_max: float, *, largura: float = LARGURA - 266, minimo: int = 34) -> int:
    """Colunas da matriz de assuntos: 34, ou mais quando a altura limita (os pontos ficam do maior
    tamanho que cabe na área, em vez de uma coluna estreita ao lado de espaço vazio)."""
    util = max(altura_max - 56 - 56, 1.0)  # o rótulo do destaque em cima e a base embaixo
    passo = (largura * util / max(total, 1)) ** 0.5
    return max(minimo, int(-(-largura // passo)))


# ── Gráfico de juros (Selic, IPCA e juro real) ───────────────────────────


def barras_juros(selic: float, ipca: float, real: float, formula: str, *, largura: float = LARGURA) -> tuple[Markup, str]:
    """Três barras horizontais: Selic, IPCA de 12 meses e o juro real ex-post (em laranja).

    O juro real não é a diferença simples entre as barras (13,75 − 4,58 = 9,17), e sim a
    fórmula escrita na base; por isso ele tem a própria barra, e não um colchete entre as duas.
    """
    linhas = (
        ("Selic", selic, "var(--ink)", "var(--ink)"),
        ("IPCA de 12 meses", ipca, "var(--g-n)", "var(--ink)"),
        ("Juro real ex-post", real, "var(--g-hi)", "var(--accent)"),
    )
    topo = max(selic, ipca, real, 0.01)
    w_max = largura - (G.largura_texto("00,00%", "hook", 72) + 24)
    corpo: list[str] = []
    y = 0.0
    for rotulo, valor, cor, tinta in linhas:
        corpo.append(G._texto(0, y + 30, rotulo, "label", 30, "var(--ink)"))
        w = max(8.0, w_max * max(valor, 0) / topo)
        corpo.append(G._rect(0, y + 48, w, 64, cor, rx=4))
        corpo.append(G._texto(w + 20, y + 106, f"{E.num(valor, 2)}%", "hook", 72, tinta))
        y += 152
    corpo.append(G._linha(0, y + 4, largura, y + 4, "var(--rule2)", 2))
    corpo.append(G._texto(0, y + 46, G._cabe(formula, "mono", 26, largura), "mono", 26, "var(--ink2)"))
    descricao = (
        f"Gráfico: barras com a Selic de {E.num(selic, 2)}%, o IPCA de 12 meses de {E.num(ipca, 2)}% "
        f"e o juro real ex-post de {E.num(real, 2)}%, pela conta {formula}."
    )
    return G._svg("juros", largura, y + 60, corpo, descricao), descricao


# ── Slide 2: a prova, por tipo ───────────────────────────────────────────
# Cada função devolve os textos do slide, a ficha do slide 3 (fonte, período, base),
# ``desenha(altura) -> (svg, descricao)``, chamada depois que se sabe quanto espaço sobra, e
# ``minimo``/``teto``: a altura em que o gráfico ainda se lê e a que ele não precisa passar.
# O contexto vem do mais importante ao menos; o último sai primeiro se faltar espaço.

Desenho = Callable[[int], tuple[Markup, str]]


def _prova_triangulado(n: E.Numero, edition: Edition, day: date) -> dict[str, Any]:
    p = n.prova
    veiculos = p["veiculos"]
    nomes = [v["nome"] for v in veiculos]
    sim = [v["nome"] for v in veiculos if v["confirmado"]]
    horas = {v["nome"]: v["at"] for v in veiculos if v.get("at")}

    linhas_max = min(len(nomes), 12)

    def desenha(altura: int) -> tuple[Markup, str]:
        cabem = min(12, max(3, int((altura - 64) // CHECKLIST_LINHA_MIN)))
        linhas = len(nomes) if len(nomes) <= cabem else cabem  # acima disso, a última vira "+k veículos"
        linha = min(72.0, (altura - 64) / max(linhas, 1))
        return G.checklist(nomes, sim, horas, ref_date=day, linha=linha, max_linhas=cabem)

    ini, fim = _horas(veiculos, day)
    horario = (
        f"Títulos de {ini} a {fim}, no horário informado por cada veículo."
        if ini and ini != fim else "Horário informado por cada veículo, em Brasília."
    )
    return {
        "titulo": p["frase"],
        "destaque": f"{p['confirmados']} de {p['total']}",
        "desenha": desenha,
        "minimo": linhas_max * CHECKLIST_LINHA_MIN + 64,
        "teto": linhas_max * 72 + 64,
        "contexto": [c for c in (_fato(n, edition), horario) if c],
        "pe": f"Fonte: títulos do índice de {short_date(day)} · nenhum título reproduzido",
        "fonte": f"títulos do índice da edição de {short_date(day)}",
        "periodo": f"{ini} a {fim}" if ini else short_date(day),
        "base": f"{p['total']} veículos que publicaram o assunto",
    }


def _prova_concentracao(n: E.Numero, edition: Edition, day: date) -> dict[str, Any]:
    p = n.prova
    ordem = f"{p['rank']}º"
    artigos = edition.stats.articles_collected if edition.stats else 0

    total = len(p["contagens"])

    def desenha(altura: int) -> tuple[Markup, str]:
        rotulo = f"{p['veiculos']} veículos, {p['textos']} textos"
        return G.matriz_pontos(p["contagens"], p["destaque"], rotulo_destaque=rotulo, altura_max=altura - 56,
                               colunas=colunas_matriz(total, altura - 56))

    return {
        "titulo": f"{ordem} entre {E.num(p['assuntos'])} assuntos; {E.num(p['uma_fonte'])} saíram em uma fonte só",
        "destaque": ordem,
        "desenha": desenha,
        "minimo": 420,
        "teto": 760,
        "contexto": [f"Em laranja: {n.tese.rstrip('.')}."],
        "pe": f"Fonte: índice da edição de {short_date(day)} · agrupamento automático",
        "fonte": f"índice da edição de {short_date(day)}",
        "periodo": f"a coleta da edição de {short_date(day)}",
        "base": f"{E.num(p['assuntos'])} assuntos, {E.num(artigos)} notícias",
    }


def _prova_mercado(n: E.Numero, edition: Edition, day: date) -> dict[str, Any]:
    p = n.prova
    var = p["variacoes"]
    hoje = var[-1]
    rotulo = "hoje" if hoje["data"] == day else f"leitura de {hoje['dia']}"
    ativo = nome_corrido(p["qid"], p["ativo"])
    z = f"{E.num(p['z'], 1)} {_vezes(p['z'])}"
    anteriores = [v for v in var[:-1] if v["pct"] is not None]
    contexto = []
    if anteriores:
        maior = max(anteriores, key=lambda v: abs(v["pct"]))
        contexto = [
            f"Variação típica do {ativo}: {E.num(p['escala'], 2)}% por dia, nas {len(anteriores)} leituras "
            f"anteriores (desde {anteriores[0]['dia']}).",
            f"Maior variação anterior no período: {E.sinal(maior['pct'], 2)} em {maior['dia']}.",
        ]

    def desenha(altura: int) -> tuple[Markup, str]:
        return G.barras_variacao([(v["data"], v["pct"]) for v in var], p["escala"], altura=altura, rotulo_hoje=rotulo)

    return {
        "titulo": f"{z} a variação típica do {ativo}",
        "destaque": z,
        "desenha": desenha,
        "minimo": 400,
        "teto": GRAFICO_MAX,
        "contexto": contexto,
        "pe": f"Fonte: {p['fonte']} · uma leitura por dia · " + (f"hoje: {p['leitura']}" if hoje["data"] == day else p["leitura"]),
        "fonte": p["fonte"],
        "periodo": f"{var[0]['dia']} a {hoje['dia']}",
        "base": f"{len(var)} leituras diárias",
    }


def _extremo(p: dict[str, Any], day: date) -> str:
    """A máxima (ou a mínima) da série que não é uma das pontas: "Máxima da série: 209.067 pontos, fech. 09/10."."""
    qid = p["qid"]
    pontas = {p["primeiro"]["dia"], p["ultimo"]["dia"]}
    for chave, nome in (("max", "Máxima"), ("min", "Mínima")):
        x = p[chave]
        if x["dia"] not in pontas:
            return f"{nome} da série: {valor_fmt(qid, x['valor'])}, {_quando(qid, x, day)}."
    return ""


def _outros(p: dict[str, Any]) -> str:
    """Os 2 outros ativos do Mercado em linha com maior |acumulado|: "Brent +10,1%, dólar −4,1%"."""
    outros = [o for o in p.get("outros", []) if o["qid"] in ("ibov", "usd", "brent", "btc")]
    outros.sort(key=lambda o: -abs(o["pct"]))
    return ", ".join(f"{nome_corrido(o['qid'], o['ativo'])} {o['pct_fmt']}" for o in outros[:2])


def _prova_acumulado(n: E.Numero, edition: Edition, day: date) -> dict[str, Any]:
    p = n.prova
    qid = p["qid"]
    primeiro, ultimo = p["primeiro"], p["ultimo"]
    de, ate = valor_fmt(qid, primeiro["valor"], unidade=False), valor_fmt(qid, ultimo["valor"], unidade=False)
    contexto = [_extremo(p, day)]
    outros = _outros(p)
    if outros:
        contexto.append(f"No mesmo período: {outros}.")

    def desenha(altura: int) -> tuple[Markup, str]:
        return G.linha(
            [(x["data"], x["valor"]) for x in p["pontos"]],
            [f["data"] for f in p["faltantes"]],
            altura=altura,
            casas=E.CASAS.get(qid, 2),
            prefixo=PREFIXO.get(qid, ""),
        )

    return {
        "titulo": f"De {de} a {ate}{SUFIXO.get(qid, '')} em {p['leituras']} leituras",
        "destaque": ate,
        "desenha": desenha,
        "minimo": 400,
        "teto": GRAFICO_MAX,
        "contexto": [c for c in contexto if c],
        "pe": "Fonte: Yahoo Finance · uma leitura por dia · variação ponta a ponta",
        "fonte": "Yahoo Finance",
        "periodo": f"{_quando(qid, primeiro, day)} a {_quando(qid, ultimo, day)}",
        "base": f"{p['leituras']} leituras" + (f"; {len(p['faltantes'])} pregões sem leitura" if p["faltantes"] else ""),
    }


def _prova_juro(n: E.Numero, edition: Edition, day: date) -> dict[str, Any]:
    p = n.prova
    selic_dia = _data_iso(p.get("selic_ref", "")) or short_date(day)
    return {
        "titulo": f"Selic de {E.num(p['selic'], 2)}% contra inflação de {E.num(p['ipca'], 2)}% em 12 meses",
        "destaque": f"{E.num(p['selic'], 2)}%",
        "desenha": lambda altura: barras_juros(p["selic"], p["ipca"], p["real"], p["formula"]),
        "minimo": 516,
        "teto": 516,
        "contexto": [
            f"Selic de {selic_dia}; IPCA acumulado em 12 meses até {p['ipca_ref']}.",
            "O juro real não é a diferença simples entre as duas taxas: a conta divide uma pela outra.",
        ],
        "pe": f"Fonte: {p['fonte']} · Selic de {selic_dia} · IPCA 12m até {p['ipca_ref']}",
        "fonte": p["fonte"],
        "periodo": f"Selic de {selic_dia}; IPCA de 12 meses até {p['ipca_ref']}",
        "base": "as duas taxas oficiais do dia",
    }


PROVAS = {
    "triangulado": _prova_triangulado,
    "concentracao": _prova_concentracao,
    "mercado": _prova_mercado,
    "acumulado": _prova_acumulado,
    "juro_real": _prova_juro,
}


# ── Legenda ───────────────────────────────────────────────────────────────


def _frase_curta(n: E.Numero) -> str:
    """A tese sem repetir o número que já abre a frase (alt e legenda do acumulado)."""
    if n.tipo == "acumulado":
        p = n.prova
        return f"{p['ativo']} acumula {'alta' if p['pct'] > 0 else 'queda'} desde {p['primeiro']['dia']}"
    return n.tese


def _serie_comeca(history: list[Edition], n: E.Numero, janela: int = 90) -> bool:
    """O primeiro ponto do acumulado é o começo da série da Triangula, e não o corte da janela do histórico."""
    datas = sorted(date.fromisoformat(e.date) for e in history)
    if n.tipo != "acumulado" or not datas:
        return False
    return (datas[-1] - datas[0]).days < janela - 1 and abs((n.prova["primeiro"]["data"] - datas[0]).days) <= 1


def _linha1(n: E.Numero, history: list[Edition]) -> str:
    """1ª linha da legenda: o número e o contraste, em até 110 caracteres."""
    p = n.prova
    if n.tipo == "acumulado":
        fim = ", quando a série da Triangula começa." if _serie_comeca(history, n) else "."
        return _cabe(f"{n.valor}: {_frase_curta(n)}{fim}", f"{n.valor}: {_frase_curta(n)}.")
    if n.tipo == "mercado":
        ativo = nome_corrido(p["qid"], p["ativo"])
        z = f"{E.num(p['z'], 1)} {_vezes(p['z'])}"
        return _cabe(
            f"{n.valor}: a variação do {ativo} na {p['leitura']} é {z} a típica da série.",
            f"{n.valor}: {n.tese}, {z} a variação típica.",
        )
    if n.tipo == "triangulado":
        k, total = p["confirmados"], p["total"]
        return _cabe(
            f"{n.valor} no título de {k} de {total} veículos: {n.tese}",
            f"{n.tese}: no título de {k} de {total} veículos",
            f"{n.valor} no título de {k} de {total} veículos que publicaram o assunto.",
        )
    if n.tipo == "concentracao":
        onde = f"o mais coberto entre {E.num(p['assuntos'])} do dia" if p["rank"] == 1 else f"o {p['rank']}º entre {E.num(p['assuntos'])} do dia"
        return _cabe(f"{p['veiculos']} veículos no mesmo assunto: {n.tese}", f"{p['veiculos']} veículos no mesmo assunto, {onde}.")
    valor = f"{n.qualificador.capitalize()} {n.valor}" if n.qualificador else n.valor
    return _cabe(
        f"{valor}: o juro real ex-post com a Selic a {E.num(p['selic'], 2)}% e o IPCA de 12 meses a {E.num(p['ipca'], 2)}%.",
        f"{valor}: {n.tese}.",
    )


def _bullets(n: E.Numero, edition: Edition, day: date) -> list[str]:
    """As linhas "▸" da legenda: o que não coube na arte."""
    p = n.prova
    if n.tipo == "acumulado":
        qid = p["qid"]
        ini, fim = p["primeiro"], p["ultimo"]
        linhas = [
            f"De {valor_fmt(qid, ini['valor'])} ({_quando(qid, ini, day)}) a {valor_fmt(qid, fim['valor'], unidade=False)} "
            f"({_quando(qid, fim, day)}), em {p['leituras']} leituras.",
            _extremo(p, day),
        ]
        outros = _outros(p)
        if outros:
            linhas.append(f"No mesmo período: {outros}.")
        return [x for x in linhas if x]
    if n.tipo == "mercado":
        qid = p["qid"]
        var = p["variacoes"]
        anteriores = [v for v in var[:-1] if v["pct"] is not None]
        linhas = [f"Valor na leitura: {valor_fmt(qid, var[-1]['valor'])} ({p['fonte']}, {p['leitura']})."]
        if anteriores:
            maior = max(anteriores, key=lambda v: abs(v["pct"]))
            linhas += [
                f"Variação típica do {nome_corrido(qid, p['ativo'])}: {E.num(p['escala'], 2)}% por dia "
                f"(escala robusta de {len(anteriores)} leituras desde {anteriores[0]['dia']}).",
                f"Maior variação anterior no período: {E.sinal(maior['pct'], 2)} em {maior['dia']}.",
            ]
        return linhas
    if n.tipo == "triangulado":
        sim = [f"{v['nome']} ({v['hora']})" if v["hora"] else v["nome"] for v in p["veiculos"] if v["confirmado"]]
        nao = [v["nome"] for v in p["veiculos"] if not v["confirmado"]]
        linhas = [f"Com o número no título: {', '.join(sim)}."]
        if nao:
            linhas.append(f"Sem o número no título: {_lista(nao)}.")
        story = _materia(edition, n)
        dek = plain(story.dek) if story is not None else ""
        if _limpo(dek) and len(dek) <= 240:
            linhas.append(f"O fato: {dek}")
        ini, fim = _horas(p["veiculos"], day)
        linhas.append(
            f"Títulos de {ini} a {fim}, no horário informado por cada veículo, em Brasília."
            if ini and ini != fim else "Horário informado por cada veículo, em Brasília."
        )
        if len(linhas) < 3:
            linhas.append(f"Veículos que publicaram o assunto: {p['total']}; com o número no título: {p['confirmados']}.")
        return linhas
    if n.tipo == "concentracao":
        return [
            f"O assunto: {n.tese.rstrip('.')}.",
            f"{p['veiculos']} veículos e {p['textos']} textos sobre ele.",
            f"{E.fracao(p['uma_fonte'], p['assuntos'])} dos assuntos do dia saíram em uma fonte só.",
        ]
    selic_dia = _data_iso(p.get("selic_ref", "")) or short_date(day)
    return [
        f"Selic: {E.num(p['selic'], 2)}% ao ano ({p['fonte']}, {selic_dia}).",
        f"IPCA de 12 meses até {p['ipca_ref']}: {E.num(p['ipca'], 2)}%.",
        f"Conta: {p['formula']}.",
    ]


# ── A peça ────────────────────────────────────────────────────────────────


def numero_do_dia(
    edition: Edition,
    brand: dict[str, Any],
    history: list[Edition],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    numero_serie: int = 0,
    exclude: Iterable[str] = (),
    usados: Iterable[str] | None = None,
    min_pontos: int = E.ACUMULADO_MIN_PONTOS,
) -> Peca | None:
    """O carrossel do Número do dia, com o número de :func:`.estatistica.numero_do_dia`.

    ``exclude`` = {lead, story do Raio-X}; ``usados`` = ``fila.chaves_numero(dia)``; ``history``
    precisa incluir a edição do dia. None só se a edição não tiver cotações.
    """
    n = E.numero_do_dia(
        edition, history, election=election, nomes=nomes, exclude=exclude, usados=usados, min_pontos=min_pontos
    )
    if n is None:
        return None
    return peca_numero(edition, n, history, election=election, numero_serie=numero_serie)


def peca_numero(edition: Edition, n: E.Numero, history: list[Edition], *, election: bool, numero_serie: int = 0) -> Peca:
    """Os 3 slides, a legenda e o texto alternativo de um :class:`.estatistica.Numero` já escolhido."""
    day = edition_day(edition)
    section = n.section or "mercados"
    nota = NOTA.get(n.tipo, "")
    principal, unidade = _divide_unidade(n)
    prova_capa = _prova_capa(n, day)
    serie_nome = _nome_serie(numero_serie)
    valor_falado = f"{n.qualificador.lower()} {n.valor}" if n.qualificador else n.valor

    capa = Slide(
        "numero_capa",
        {
            "selo": selo_serie(numero_serie, day),
            "qual": n.qualificador,
            "valor": principal,
            "unidade": unidade,
            "tam": tamanho_numero(principal),
            "tese": n.tese,
            "prova": prova_capa,
            "nota": nota,
            "section": section,
        },
        alt=f"{serie_nome}: {valor_falado}. {n.tese}. {prova_capa}.",
    )

    prova = PROVAS[n.tipo](n, edition, day)
    desenha, minimo, teto = prova.pop("desenha"), prova.pop("minimo"), prova.pop("teto")
    ficha = {k: prova.pop(k) for k in ("fonte", "periodo", "base")}
    tam = tamanho_titulo(prova["titulo"])
    altura = altura_grafico(prova["titulo"], tam, prova["contexto"], prova["pe"], teto)
    while altura < minimo and prova["contexto"]:  # falta espaço: o gráfico fica, sai o contexto menos importante
        prova["contexto"] = prova["contexto"][:-1]
        altura = altura_grafico(prova["titulo"], tam, prova["contexto"], prova["pe"], teto)
    svg, descricao = desenha(altura)
    slide2 = Slide(
        "numero_prova",
        {"selo": selo_serie(numero_serie), "kicker": "A prova", "tam": tam, "grafico": svg, "nota": nota, "section": section, **prova},
        alt=f"{serie_nome}: {valor_falado}. {_frase_curta(n)}. {descricao} " + " ".join(prova["contexto"]) + f" {prova['pe']}.",
    )

    metodo = _milhar(n.metodo) if n.tipo == "concentracao" else n.metodo
    ficha_linhas = [
        {"k": "Fonte", "v": ficha["fonte"]},
        {"k": "Período", "v": ficha["periodo"]},
        {"k": "Base", "v": ficha["base"]},
        {"k": "Limite", "v": LIMITE[n.tipo]},
    ]
    slide3 = Slide(
        "numero_metodo",
        {
            "selo": selo_serie(numero_serie),
            "kicker": "Como medimos",
            "metodo": metodo,
            "ficha": ficha_linhas,
            "cadeia": [{"n": i, "texto": t, "atual": i == n.passo} for i, t in enumerate(PASSOS, 1)],
            "passo": n.passo,
            "cta": CTA,
            "nota": nota,
            "section": section,
        },
        alt=f"Como medimos: {metodo} "
        + " ".join(f"{f['k']}: {f['v'].rstrip('.')}." for f in ficha_linhas)
        + f" Por que este número: o passo {n.passo} de {len(PASSOS)} da ordem de escolha, {PASSOS[n.passo - 1].lower()}. "
        + CTA,
    )

    corpo = [f"▸ {b}" for b in _bullets(n, edition, day)[:5]]
    corpo += [f"Como medimos: {METODO_LEGENDA[n.tipo]}", CTA, hashtags(HASHTAG, [section], election=election)]
    return Peca(
        slot=SLOT,
        kind="carousel",
        title="Número do dia",
        slides=[capa, slide2, slide3],
        caption=finish_caption([_linha1(n, history), "\n".join(corpo)]),
        story_ids=[n.story_id] if n.story_id else [],
        serie=SERIE,
        numero_serie=numero_serie,
        chave=n.chave,
    )
