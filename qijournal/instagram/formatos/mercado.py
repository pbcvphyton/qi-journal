"""Mercado em linha (§4.8): o slide 7 do Em 1 minuto e o substituto da noite.

Quatro gráficos pequenos (Ibovespa, Dólar, Brent, Bitcoin) com a série de até 60
dias, a faixa Selic · IPCA 12m · juro real e o título-achado com o maior acumulado.
Tudo sai de ``edition.quotes`` e do histórico; nada de IA.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from qijournal.models import Edition, Quote

from .. import estatistica as E
from .. import graficos as G
from ..redacao import LINHA1_MAX
from ..conteudo import ALT_LIMIT, MESES_CURTOS, Peca, Slide, edition_day, finish_caption, hashtags, kicker_date, short_date

ATIVOS = ["ibov", "usd", "brent", "btc"]
JANELA_DIAS = 60  # a linha mostra no máximo 60 dias de série
PAINEL_LARGURA, PAINEL_ALTURA = 440, 250  # o gráfico de cada painel (o painel inteiro tem ~440 × 380)
CTA = "Salve para comparar com a próxima leitura."
RODAPE = "Yahoo Finance e BCB · uma leitura por dia · fech. = fechamento do pregão"

# Unidade de cada ativo no texto ("R$ 5,01", "208.296 pontos") e o nome no meio da frase.
PREFIXO = {"usd": "R$ ", "eur": "R$ ", "cny": "R$ ", "brent": "US$ ", "btc": "US$ ", "gold": "US$ "}
SUFIXO = {"ibov": " pontos", "ifix": " pontos", "spx": " pontos"}
NOME_CORRIDO = {"usd": "dólar", "eur": "euro", "cny": "yuan", "btc": "bitcoin", "gold": "ouro"}


def valor_fmt(qid: str, value: float, *, unidade: bool = True) -> str:
    """``"R$ 5,01"``, ``"US$ 110,14"``; com ``unidade``, ``"208.296 pontos"``."""
    texto = PREFIXO.get(qid, "") + E.valor_cotacao(qid, value)
    return texto + (SUFIXO.get(qid, "") if unidade else "")


def nome_corrido(qid: str, label: str) -> str:
    """O nome do ativo no meio da frase: "dólar", "bitcoin"; "Ibovespa" e "Brent" ficam."""
    return NOME_CORRIDO.get(qid, label)


def _mes_curto(q: Quote | None) -> str:
    """``"set"`` a partir do ``as_of`` "2026-09" do IPCA."""
    _, _, mes = (q.as_of or "").partition("-") if q else ("", "", "")
    return MESES_CURTOS[int(mes) - 1].lower() if mes.isdigit() and 1 <= int(mes) <= 12 else ""


def faixa_juros(edition: Edition) -> str:
    """``"Selic 13,75% · IPCA 12m 4,58% (set) · juro real ex-post 8,77%"`` (o que houver)."""
    by_id = {q.id: q for q in edition.quotes}
    selic, ipca = by_id.get("selic"), by_id.get("ipca12m")
    partes = []
    if selic is not None:
        partes.append(f"Selic {E.num(selic.value, 2)}%")
    if ipca is not None:
        mes = _mes_curto(ipca)
        partes.append(f"IPCA 12m {E.num(ipca.value, 2)}%" + (f" ({mes})" if mes else ""))
    real = E.juro_real(edition)
    if real is not None:
        partes.append(f"juro real ex-post {E.num(real, 2)}%")
    return " · ".join(partes)


def _painel(edition: Edition, history: list[Edition], qid: str, min_pontos: int) -> tuple[dict[str, Any], str] | None:
    """O painel de um ativo e a frase do gráfico para o texto alternativo ("" sem gráfico)."""
    q = next((x for x in edition.quotes if x.id == qid), None)
    if q is None:
        return None
    day = edition_day(edition)
    hoje = E.ponto_da_cotacao(q)
    pontos = [p for p in E.serie(history, qid) if p.day >= day - timedelta(days=JANELA_DIAS)]
    selo = E.selo_leitura(qid, hoje, day) if hoje is not None else ""
    # Leitura sem variação (o dólar às 09h00 com 0,00%) não mostra variação nem seta.
    mostra_var = bool(selo) and q.change_pct is not None
    painel: dict[str, Any] = {
        "id": qid,
        "nome": q.label,
        "valor": valor_fmt(qid, q.value, unidade=False),
        "variacao": q.change_pct if mostra_var else None,
        "change": E.sinal(q.change_pct, 2) if mostra_var else "",
        "trend": ("up" if q.change_pct > 0 else "down" if q.change_pct < 0 else "flat") if mostra_var else "",
        "selo": selo,
        "acumulado": "",
        "pct": None,
        "desde": "",
        "grafico": "",
        "leituras": len(pontos),
        "ate": "",  # "leitura das 06h00" quando o acumulado não termina num fechamento
    }
    descricao = ""
    acc = E.acumulado(pontos)
    if acc is not None:  # ponta a ponta com as duas datas: vale mesmo com série curta
        desde = f"{acc.first.day:%d/%m}"
        painel.update(pct=acc.pct, desde=desde, acumulado=f"{E.sinal(acc.pct)} desde {desde}")
        if not E.is_close(qid, acc.last.as_of):  # termina numa leitura do meio do pregão: o título diz a hora
            painel["ate"] = E.selo_leitura(qid, acc.last, day) or f"leitura de {acc.last.day:%d/%m}"
    if acc is not None and len(pontos) >= min_pontos:
        svg, descricao = G.linha(
            [(p.day, p.value) for p in pontos],
            E.pregoes_faltantes(pontos, qid),
            largura=PAINEL_LARGURA,
            altura=PAINEL_ALTURA,
            casas=E.CASAS.get(qid, 2),
            prefixo=PREFIXO.get(qid, ""),
            compacto=True,
        )
        painel["grafico"] = svg
        descricao = f"{q.label}: " + descricao.removeprefix("Gráfico: ")
    return painel, descricao


def _achado(paineis: list[dict[str, Any]], day: str, evitar: Iterable[str] = ()) -> tuple[str, str, str]:
    """O título-achado, o trecho em laranja e o selo do título: o maior |acumulado| ("Ibovespa +13,8%
    desde 28/09"), fora dos ativos em ``evitar`` se houver outro, com "até a leitura das 06h00" quando
    o acumulado termina no meio do pregão; sem série (uma leitura só), a maior variação da leitura
    ("Dólar −4,42% na leitura das 14h06", que já diz a hora)."""
    evitar = set(evitar)
    com_serie = [p for p in paineis if p["pct"] is not None]
    com_serie = [p for p in com_serie if p["id"] not in evitar] or com_serie
    if com_serie:
        p = max(com_serie, key=lambda p: abs(p["pct"]))
        return f"{p['nome']} {p['acumulado']}", E.sinal(p["pct"]), f"até a {p['ate']}" if p["ate"] else ""
    com_var = [p for p in paineis if p["variacao"] is not None]
    if com_var:
        p = max(com_var, key=lambda p: abs(p["variacao"]))
        return f"{p['nome']} {p['change']} na {p['selo']}", p["change"], ""
    return f"Mercado em {day}", "", ""


def _alt(titulo: str, day: str, paineis: list[dict[str, Any]], faixa: str, graficos: list[str], selo_titulo: str = "") -> str:
    """Texto alternativo com os valores, a faixa e a frase de cada gráfico; se passar de 1.000
    caracteres, cada gráfico fica só com a primeira frase ("linha com 14 leituras, de … a …")."""
    frases = []
    for p in paineis:
        partes = [x for x in (p["selo"], f"{p['change']} no dia" if p["change"] else "", p["acumulado"]) if x]
        frases.append(f"{p['nome']} {p['valor']}" + (f" ({', '.join(partes)})" if partes else ""))
    base = f"Mercado em linha, {day}: {titulo}" + (f", {selo_titulo}" if selo_titulo else "") + ". " + "; ".join(frases) + "."
    if faixa:
        base += f" {faixa[0].upper()}{faixa[1:]}."
    for desc in (graficos, [g.split(";")[0].rstrip(".") + "." for g in graficos], []):
        alt = base + (" Gráficos: " + " ".join(desc) if desc else "")
        if len(alt) <= ALT_LIMIT:
            return alt
    return base


def mercado_slide(
    edition: Edition,
    history: list[Edition],
    *,
    tema: str = "dark",
    min_pontos: int = E.ACUMULADO_MIN_PONTOS,
    evitar: Iterable[str] = (),
) -> Slide | None:
    """O slide do Mercado em linha: título-achado, 4 gráficos pequenos em 2×2, faixa de juros e rodapé.

    ``tema``: ``"dark"`` (slide 7 do Em 1 minuto) ou ``"paper"`` (substituto da noite).
    ``history`` precisa incluir a edição do dia. Com menos de ``min_pontos`` leituras
    (``limiares.grafico_min_pontos``), o painel mostra só o valor. None sem nenhuma cotação dos 4 ativos.
    ``evitar``: ativos que não viram título (ex.: ``{"ibov"}`` quando o Número do dia já é o acumulado dele).

    O ``ctx`` traz ``selo`` ("MERCADO EM LINHA · QUA 21 OUT"), ``contador`` (False) e ``cor``
    ("em1min" ou "mercado"): o Em 1 minuto troca o selo pelo da série e liga o contador.
    """
    day = edition_day(edition)
    feitos = [x for x in (_painel(edition, history, qid, min_pontos) for qid in ATIVOS) if x]
    if not feitos:
        return None
    paineis = [p for p, _ in feitos]
    titulo, destaque, selo_titulo = _achado(paineis, short_date(day), evitar)
    faixa = faixa_juros(edition)
    ctx = {
        "tema": "t-paper" if tema == "paper" else "t-dark",
        "selo": f"MERCADO EM LINHA · {kicker_date(day)}",
        "contador": False,
        "cor": "mercado" if tema == "paper" else "em1min",
        "titulo": titulo,
        "destaque": destaque,
        "selo_titulo": selo_titulo,
        "tam": G.tamanho_gancho(titulo, max_linhas=1, tam_max=104, tam_min=64),
        "paineis": paineis,
        "graficos": any(p["grafico"] for p in paineis),
        "faixa": faixa,
        "pe": RODAPE,
        "nota": "fonte: Yahoo Finance e BCB",
        "section": "mercados",
    }
    alt = _alt(titulo, short_date(day), paineis, faixa, [d for _, d in feitos if d], selo_titulo)
    return Slide("mercado_linha", ctx, alt=alt)


def mercado_em_linha(
    edition: Edition,
    brand: dict[str, Any],
    history: list[Edition],
    *,
    min_pontos: int = E.ACUMULADO_MIN_PONTOS,
    evitar: Iterable[str] = (),
) -> Peca | None:
    """Substituto da noite (18:30): imagem única em papel, sem número de série.

    ``evitar``: ativos que não viram título (o do Número do dia, para os posts do dia não repetirem o assunto).
    """
    slide = mercado_slide(edition, history, tema="paper", min_pontos=min_pontos, evitar=evitar)
    if slide is None:
        return None
    day = edition_day(edition)
    ctx = slide.ctx
    linhas = []
    for p in ctx["paineis"]:
        q = next(x for x in edition.quotes if x.id == p["id"])
        partes = [x for x in (p["selo"], f"{p['change']} no dia" if p["change"] else "", p["acumulado"]) if x]
        linhas.append(f"▸ {p['nome']}: {valor_fmt(p['id'], q.value)}" + (f" ({', '.join(partes)})" if partes else "") + ".")
    if ctx["faixa"]:
        linhas.append(f"▸ {ctx['faixa']}.")
    corpo = linhas + [
        "Como medimos: uma leitura por dia (Yahoo Finance e Banco Central), variação ponta a ponta; "
        "não é soma de variações diárias.",
        CTA,
        hashtags("#dados", ["mercados", "brasil"], election=False),
    ]
    linha1 = f"Mercado em linha · {short_date(day)}: {ctx['titulo']}"
    if ctx["selo_titulo"] and len(linha1) + len(ctx["selo_titulo"]) + 3 <= LINHA1_MAX:
        linha1 += f" ({ctx['selo_titulo']})"
    caption = finish_caption([linha1, "\n".join(corpo)])
    return Peca(
        slot="noite",
        kind="image",
        title="Mercado em linha",
        slides=[slide],
        caption=caption,
        serie="mercado",
        numero_serie=0,
    )

