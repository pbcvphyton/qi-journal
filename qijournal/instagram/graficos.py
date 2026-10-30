"""Gráficos dos posts do Instagram: SVG inline que faz o papel da foto (§6.4).

Cada função é pura: recebe dados já calculados (números, listas, dicts, datas) e
devolve ``(svg, descricao)``.

- ``svg``: ``Markup`` com ``viewBox`` e tamanho natural em px. As cores vêm só dos
  tokens do ``_base.html.j2`` (``var(--g-a)``, ``var(--ink)``…) e as fontes são as da
  marca. Tem rótulo direto, a base escrita e nenhuma legenda separada.
- ``descricao``: a frase "Gráfico: …" do texto alternativo, tirada dos mesmos dados.

Os atributos vão entre aspas simples e o texto sai escapado: o SVG atravessa
``conteudo.polish`` (aspas tipográficas, espaço inseparável) sem quebrar. No template,
``{{ s.grafico|safe }}``.

Horários são convertidos para Brasília; datas ingênuas são tratadas como de Brasília.
"""

from __future__ import annotations

import math
import unicodedata
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from markupsafe import Markup

from qijournal.text import format_number_pt

TZ = ZoneInfo("America/Sao_Paulo")
LARGURA = 936  # 1080 − 2 × 72 de área segura
MENOS = "−"

# ── Fontes e medida de texto ──────────────────────────────────────────────

_FONTES = {
    "hook": ("Archivo, sans-serif", 900, "62%"),  # ExtraCondensed Black: números grandes
    "label": ("Archivo, sans-serif", 600, "100%"),  # nomes de veículo e rótulos
    "mono": ("IBM Plex Mono, monospace", 500, None),  # horas, datas, base
    "mono6": ("IBM Plex Mono, monospace", 600, None),
}

# Largura de cada caractere ASCII (32–126) em centésimos de em, medida no Chromium.
_ASCII = "".join(chr(c) for c in range(32, 127))
_TABELAS = {
    "label": "20 29 44 58 54 97 73 25 36 36 41 64 30 33 30 30 58 58 58 58 58 58 58 58 58 58 34 34 64 64 64 61 "
    "100 71 71 72 73 67 61 79 73 28 58 70 57 84 73 78 67 78 72 67 62 72 67 95 69 68 63 34 30 34 64 51 21 56 "
    "59 55 59 56 31 59 58 25 25 54 25 86 58 60 59 59 36 54 31 58 53 76 55 53 51 39 24 39 64",
    "hook": "11 27 40 50 40 74 57 21 39 39 37 53 22 24 22 28 44 37 43 44 44 44 44 40 43 44 22 22 53 53 53 42 69 "
    "53 50 51 51 46 40 54 51 26 45 51 43 70 52 55 48 55 51 47 48 50 49 73 50 48 48 39 28 39 53 38 20 44 44 42 "
    "44 43 29 42 44 23 23 46 23 65 44 43 44 44 30 40 29 44 41 64 45 41 38 39 20 39 53",
}
_ESPECIAIS = {
    "label": {"·": 33, "–": 50, "—": 100, "…": 97, "‘": 28, "’": 28, "“": 48, "”": 48, "º": 39, "ª": 40,
              "°": 40, "×": 64, MENOS: 64, "→": 100, " ": 20},
    "hook": {"·": 21, "–": 31, "—": 62, "…": 67, "‘": 20, "’": 20, "“": 37, "”": 37, "º": 30, "ª": 31,
             "°": 40, "×": 53, MENOS: 53, "→": 100, " ": 11},
}
_LARGURAS = {
    nome: dict(zip(_ASCII, map(int, tabela.split()))) | _ESPECIAIS[nome] for nome, tabela in _TABELAS.items()
}


def largura_texto(texto: str, fonte: str, tam: float) -> float:
    """Largura estimada, em px, de ``texto`` numa fonte de :data:`_FONTES` no tamanho ``tam``."""
    if fonte.startswith("mono"):
        return 0.6 * tam * len(texto)
    tabela = _LARGURAS["hook" if fonte == "hook" else "label"]
    total = 0
    for ch in texto:
        w = tabela.get(ch)
        if w is None:  # acentuadas medem o mesmo que a letra-base
            w = tabela.get(unicodedata.normalize("NFD", ch)[:1], 60)
        total += w
    return total / 100 * tam


def _cabe(texto: str, fonte: str, tam: float, max_w: float) -> str:
    """``texto`` inteiro se couber em ``max_w``; senão, cortado com reticências."""
    if largura_texto(texto, fonte, tam) <= max_w:
        return texto
    while texto and largura_texto(texto + "…", fonte, tam) > max_w:
        texto = texto[:-1]
    return texto.rstrip(" ,·") + "…"


def _quebra(texto: str, fonte: str, tam: float, max_w: float, max_linhas: int = 2) -> list[str]:
    """``texto`` em até ``max_linhas`` linhas de ``max_w``; a última leva reticências se sobrar."""
    palavras = texto.split()
    linhas: list[str] = []
    atual = ""
    for i, palavra in enumerate(palavras):
        tentativa = f"{atual} {palavra}".strip()
        if not atual or largura_texto(tentativa, fonte, tam) <= max_w:
            atual = tentativa
            continue
        linhas.append(atual)
        if len(linhas) == max_linhas - 1:
            atual = " ".join(palavras[i:])
            break
        atual = palavra
    linhas.append(atual)
    return [_cabe(linha, fonte, tam, max_w) for linha in linhas if linha]


def linhas_texto(texto: str, fonte: str, tam: float, max_w: float, tracking: float = 0.0) -> list[str]:
    """Quebra gulosa de ``texto`` em linhas de até ``max_w`` px (``tracking`` em em, como o letter-spacing)."""
    linhas: list[str] = []
    atual = ""
    for palavra in texto.split():
        tentativa = f"{atual} {palavra}".strip()
        w = largura_texto(tentativa, fonte, tam) + tracking * tam * len(tentativa)
        if atual and w > max_w:
            linhas.append(atual)
            atual = palavra
        else:
            atual = tentativa
    return linhas + ([atual] if atual else [])


def tamanho_gancho(texto: str, *, largura: float = LARGURA, max_linhas: int = 4, tam_max: int = 150,
                   tam_min: int = 112) -> int:
    """Maior tamanho (px, par) em que o gancho cabe em ``max_linhas`` na ExtraCondensed Black em caixa alta.

    Para o template passar ao ``hook`` (o ``data-fit`` ainda encolhe até o mínimo se a estimativa errar).
    Se nem ``tam_min`` couber, devolve ``tam_min``.
    """
    caixa = texto.upper()
    for tam in range(tam_max - tam_max % 2, tam_min - 1, -2):
        if len(linhas_texto(caixa, "hook", tam, largura * 0.985, tracking=-0.01)) <= max_linhas:
            return tam
    return tam_min


# ── Peças de SVG ──────────────────────────────────────────────────────────

# Contorno na cor do fundo atrás do texto: linhas e pontos que passam por baixo não riscam as letras.
HALO = "paint-order:stroke;stroke:var(--bg);stroke-width:8px;stroke-linejoin:round"


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _esc(texto: Any) -> str:
    return escape(str(texto), quote=True)


def _estilo(fonte: str, tam: float, extra: str = "") -> str:
    familia, peso, largura = _FONTES[fonte]
    s = f"font-family:{familia};font-weight:{peso};font-size:{_f(tam)}px"
    if largura:
        s += f";font-stretch:{largura}"
    if fonte == "hook":
        s += ";font-variant-numeric:lining-nums"
    return s + (f";{extra}" if extra else "")


def _texto(x: float, y: float, conteudo: str, fonte: str, tam: float, cor: str = "var(--ink)",
           ancora: str = "start", extra: str = "") -> str:
    return (
        f"<text x='{_f(x)}' y='{_f(y)}' text-anchor='{ancora}' fill='{cor}' "
        f"style='{_estilo(fonte, tam, extra)}'>{_esc(conteudo)}</text>"
    )


def _tspan(conteudo: str, fonte: str, tam: float, cor: str, dx: float = 0) -> str:
    desloc = f" dx='{_f(dx)}'" if dx else ""
    return f"<tspan{desloc} fill='{cor}' style='{_estilo(fonte, tam)}'>{_esc(conteudo)}</tspan>"


def _rect(x: float, y: float, w: float, h: float, cor: str, *, rx: float = 0, extra: str = "") -> str:
    canto = f" rx='{_f(rx)}'" if rx else ""
    return f"<rect x='{_f(x)}' y='{_f(y)}' width='{_f(w)}' height='{_f(h)}'{canto} fill='{cor}'{extra}/>"


def _circulo(cx: float, cy: float, r: float, cor: str, extra: str = "") -> str:
    return f"<circle cx='{_f(cx)}' cy='{_f(cy)}' r='{_f(r)}' fill='{cor}'{extra}/>"


def _linha(x1: float, y1: float, x2: float, y2: float, cor: str, w: float = 2, extra: str = "") -> str:
    return (
        f"<line x1='{_f(x1)}' y1='{_f(y1)}' x2='{_f(x2)}' y2='{_f(y2)}' stroke='{cor}' "
        f"stroke-width='{_f(w)}'{extra}/>"
    )


def _svg(tipo: str, w: float, h: float, corpo: list[str], descricao: str) -> Markup:
    return Markup(
        f"<svg class='graf graf-{tipo}' xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {_f(w)} {_f(h)}' "
        f"width='{_f(w)}' height='{_f(h)}' role='img' aria-label='{_esc(descricao)}'>" + "".join(corpo) + "</svg>"
    )


# ── Números, datas e frases ───────────────────────────────────────────────


def _num(v: float, casas: int = 0) -> str:
    return format_number_pt(v, casas).replace("-", MENOS)


def _pct(v: float, casas: int = 2) -> str:
    sinal = "+" if v > 0 else (MENOS if v < 0 else "")
    return f"{sinal}{format_number_pt(abs(v), casas)}%"


def _local(dt: datetime | date) -> datetime:
    if isinstance(dt, datetime):
        return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)
    return datetime(dt.year, dt.month, dt.day, tzinfo=TZ)


def _dia(d: datetime | date) -> date:
    return _local(d).date() if isinstance(d, datetime) else d


def _ddmm(d: datetime | date) -> str:
    return f"{_dia(d):%d/%m}"


def _hora(dt: datetime, ref: date | None = None) -> str:
    """``"13h17"``; com a data na frente quando não é o dia ``ref``: ``"20/10 13h17"``."""
    local = _local(dt)
    h = local.strftime("%Hh%M")
    return h if ref is not None and local.date() == ref else f"{local:%d/%m} {h}"


def _duracao(delta: timedelta) -> str:
    minutos = int(delta.total_seconds() // 60)
    return f"{minutos} min" if minutos < 60 else f"{minutos // 60}h{minutos % 60:02d}"


def _lista(partes: Sequence[str]) -> str:
    """``["a", "b", "c"]`` → ``"a, b e c"``."""
    partes = [p for p in partes if p]
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + " e " + partes[-1]


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


def _grupo(rotulo: str) -> str:
    """Como o grupo entra na frase: "na maioria", "sem lado", "do lado sinergia em IA"."""
    r = rotulo.strip()
    primeira = r.split(" ", 1)[0].lower()
    if primeira in ("maioria", "minoria"):
        return "na " + r
    if primeira in ("sem", "fora", "de", "do", "da", "no", "na", "em", "lado"):
        return ("no " + r) if primeira == "lado" else r
    return "do lado " + r


def _vazio(tipo: str, texto: str, descricao: str, largura: float, altura: float = 64) -> tuple[Markup, str]:
    corpo = [_texto(0, altura / 2 + 8, texto, "mono", 24, "var(--ink3)")]
    return _svg(tipo, largura, altura, corpo, descricao), descricao


# ── barra_anb ─────────────────────────────────────────────────────────────

_COR_LADO = {"a": "var(--g-a)", "n": "var(--g-n)", "b": "var(--g-b)"}
_TINTA_LADO = {"a": "var(--g-a-ink)", "n": "var(--g-n-ink)", "b": "var(--g-b-ink)"}


def _segmentos(contagens: Sequence[tuple[str, int]], largura: float, gap: float, minimo: float) -> list[tuple]:
    """``[(chave, contagem, x, w)]`` proporcionais, com largura mínima para quem tem contagem."""
    ativos = [(k, c) for k, c in contagens if c > 0]
    if not ativos:
        return []
    livre = largura - gap * (len(ativos) - 1)
    minimo = min(minimo, livre / len(ativos))
    total = sum(c for _, c in ativos)
    w = {k: livre * c / total for k, c in ativos}
    falta = sum(minimo - v for v in w.values() if v < minimo)
    if falta:
        sobra = sum(v - minimo for v in w.values() if v > minimo)
        w = {k: minimo if v <= minimo else v - falta * (v - minimo) / sobra for k, v in w.items()}
    out, x = [], 0.0
    for k, c in ativos:
        out.append((k, c, x, w[k]))
        x += w[k] + gap
    return out


def _descricao_anb(a: int, n: int, b: int, rotulos: Mapping[str, str] | None) -> str:
    total = a + n + b
    if total == 0:
        return "Gráfico: barra vazia, sem veículos classificados."
    if rotulos:
        rot = {"a": "lado A", "n": "sem lado", "b": "lado B"} | dict(rotulos)
        partes = [f"{c} {_grupo(rot[k])}" for k, c in (("a", a), ("n", n), ("b", b)) if c]
    else:
        lados = iter(("de um lado", "do outro"))
        partes = [f"{c} {'sem lado' if k == 'n' else next(lados)}" for k, c in (("a", a), ("n", n), ("b", b)) if c]
    primeiro, _, resto = partes[0].partition(" ")
    partes[0] = f"{primeiro} {_plural(int(primeiro), 'veículo')} {resto}"
    return f"Gráfico: barra com {_lista(partes)}."


def barra_anb(
    a: int,
    n: int,
    b: int,
    rotulos: Mapping[str, str] | None = None,
    *,
    largura: float = LARGURA,
    altura: float = 120,
    mini: bool = False,
    base: str | None = None,
) -> tuple[Markup, str]:
    """Barra A | sem lado | B, proporcional à contagem de veículos.

    - ``a``, ``n``, ``b``: veículos do lado A, sem lado e do lado B (A à esquerda).
    - ``rotulos``: ``{"a": "maioria", "n": "sem lado", "b": "minoria"}``; o rótulo vai sob o
      segmento e a contagem dentro (Archivo 900). Sem rótulos, só as contagens.
    - ``base``: linha acima da barra; padrão ``"11 veículos analisados"`` ("" tira).
    - ``mini``: selo de 240 × 16 px, sem texto (o selo de cobertura dos fatos).
    """
    total = a + n + b
    descricao = _descricao_anb(a, n, b, rotulos)
    contagens = (("a", a), ("n", n), ("b", b))
    if mini:
        w, h = 240, 16
        segs = _segmentos(contagens, w, 2, 8)
        corpo = [_rect(x, 0, sw, h, _COR_LADO[k], rx=2) for k, _, x, sw in segs] or [_rect(0, 0, w, h, "var(--rule)", rx=2)]
        return _svg("anb-mini", w, h, corpo, descricao), descricao

    corpo: list[str] = []
    topo = 0.0
    base = f"{total} {_plural(total, 'veículo')} {_plural(total, 'analisado')}" if base is None else base
    if base:
        corpo.append(_texto(0, 24, base, "mono", 24, "var(--ink2)"))
        topo = 44.0
    tam = round(56 * altura / 120)
    segs = _segmentos(contagens, largura, 6, max(64, tam * 1.2))
    if not segs:
        corpo.append(_rect(0, topo, largura, altura, "var(--rule)", rx=4))
        corpo.append(_texto(largura / 2, topo + altura / 2 + 9, "nenhum veículo classificado", "mono", 24,
                            "var(--ink2)", "middle"))
        return _svg("anb", largura, topo + altura, corpo, descricao), descricao

    rotulos_txt: list[tuple[str, float, float, str]] = []  # (texto, x, largura, âncora)
    for i, (k, c, x, w) in enumerate(segs):
        corpo.append(_rect(x, topo, w, altura, _COR_LADO[k], rx=4))
        num = str(c)
        dentro = largura_texto(num, "hook", tam) + 24 <= w
        if dentro:
            corpo.append(_texto(x + w / 2, topo + altura / 2 + tam * 0.36, num, "hook", tam, _TINTA_LADO[k], "middle"))
        if rotulos and rotulos.get(k):
            txt = rotulos[k] if dentro else f"{rotulos[k]} · {c}"
            ancora = "start" if i == 0 else ("end" if i == len(segs) - 1 else "middle")
            ax = x if ancora == "start" else (x + w if ancora == "end" else x + w / 2)
            rotulos_txt.append((txt, ax, largura_texto(txt, "label", 30), ancora))
        elif not dentro:
            rotulos_txt.append((num, x + w / 2, largura_texto(num, "label", 30), "middle"))

    # Rótulos sob a barra; o do meio desce uma linha se encostar nos vizinhos.
    def extensao(r: tuple) -> tuple[float, float]:
        _, ax, w, anc = r
        return (ax, ax + w) if anc == "start" else ((ax - w, ax) if anc == "end" else (ax - w / 2, ax + w / 2))

    y = topo + altura + 42
    linhas = [[]]
    for r in rotulos_txt:
        e0, e1 = extensao(r)
        if any(not (e1 + 16 <= o0 or e0 >= o1 + 16) for o0, o1 in map(extensao, linhas[0])):
            linhas.append([r])
        else:
            linhas[0].append(r)
    for j, linha in enumerate(linhas):
        for txt, ax, _, anc in linha:
            ax = min(max(ax, 0), largura)
            corpo.append(_texto(ax, y + 40 * j, txt, "label", 30, "var(--ink)", anc))
    altura_total = (y + 40 * (len(linhas) - 1) + 10) if rotulos_txt else topo + altura
    return _svg("anb", largura, altura_total, corpo, descricao), descricao


# ── relogio ───────────────────────────────────────────────────────────────

_COR_STANCE = {"a": "var(--g-a)", "b": "var(--g-b)", "neutro": "var(--g-n)"}
_PASSOS_H = (1, 2, 3, 6, 12, 24)


def _batida(item: Any) -> tuple[str, datetime]:
    if hasattr(item, "source") and hasattr(item, "at"):
        return str(item.source), _local(item.at)
    nome, at = item[0], item[1]
    return str(nome), _local(at)


def _piso(dt: datetime, passo: int) -> datetime:
    return dt.replace(hour=dt.hour - dt.hour % passo, minute=0, second=0, microsecond=0)


def relogio(
    batidas: Iterable[Any],
    stance: Mapping[str, str | None] | None = None,
    ref_date: date | None = None,
    *,
    rotulos: Mapping[str, str] | None = None,
    largura: float = LARGURA,
    linha: float = 60,
    max_linhas: int = 12,
    base: str | None = None,
) -> tuple[Markup, str]:
    """Relógio da notícia: quando cada veículo publicou o assunto (horário de Brasília).

    - ``batidas``: ``[(nome, datetime), …]`` (ou objetos com ``.source`` e ``.at``), em qualquer ordem.
    - ``stance``: ``{nome: "a" | "b" | "neutro" | None}``. Quem não está no dict (ou tem None) está
      fora da análise e vira ponto vazado. Sem ``stance`` (assunto sem cobertura comparada), todos
      os pontos são cheios e não há rótulo de lado.
    - ``ref_date``: o dia da edição; horários de outro dia levam a data ("20/10 13h17").
    - ``rotulos``: ``{"a": "sinergia em IA", "b": "complexidade de governança"}`` (opcionais
      ``"neutro"`` e ``"fora"``); vão depois do nome, em cada linha.

    Eixo de tempo no topo, com marcas a cada 3 h (ou o passo que couber) e "00h 21/10" na virada
    do dia; o span (do primeiro ao último) fica marcado no eixo. Uma linha de ``linha`` px por
    veículo, até ``max_linhas`` (as do meio viram uma linha "+k veículos"). No pé, a base
    (``base``; padrão "12 veículos · horário informado por cada veículo, em Brasília"; "" tira).
    """
    pts = sorted((_batida(b) for b in batidas), key=lambda p: p[1])
    if not pts:
        return _vazio("relogio", "nenhum horário informado", "Gráfico: linha do tempo sem horários informados.", largura)
    ref = ref_date or pts[-1][1].date()
    if base is None:
        base = f"{len(pts)} {_plural(len(pts), 'veículo')} · horário informado por cada veículo, em Brasília"
    rot = {"a": "lado A", "b": "lado B", "neutro": "sem lado", "fora": "fora da análise"} | dict(rotulos or {})

    def lado(nome: str) -> str | None:
        if stance is None:
            return "sem"
        s = stance.get(nome)
        return s if s in _COR_STANCE else None

    # Descrição
    t0, t1 = pts[0][1], pts[-1][1]
    n = len(pts)
    if n == 1:
        descricao = f"Gráfico: linha do tempo com 1 ponto, em {_hora(t0)}"
    else:
        descricao = f"Gráfico: linha do tempo com {n} pontos, de {_hora(t0)} a {_hora(t1)}"
    if stance is not None:
        conta = {k: sum(1 for nome, _ in pts if lado(nome) == k) for k in ("a", "b", "neutro", None)}
        partes = []
        if conta["a"]:
            partes.append(f"{conta['a']} do lado {rot['a'].removeprefix('lado ')}")
        if conta["b"]:
            partes.append(f"{conta['b']} do lado {rot['b'].removeprefix('lado ')}")
        if conta["neutro"]:
            partes.append(f"{conta['neutro']} {_plural(conta['neutro'], 'neutro')}")
        if conta[None]:
            partes.append(f"{conta[None]} {rot['fora']}")
        descricao += "; " + ", ".join(partes)
    descricao += "."

    # Eixo: domínio arredondado ao passo, para o span aparecer marcado dentro dele.
    span = t1 - t0
    passo = next((p for p in _PASSOS_H if (span + timedelta(hours=2 * p)) / timedelta(hours=p) <= 9), 24)
    ini = _piso(t0, passo)
    fim = _piso(t1, passo) + timedelta(hours=passo)
    dominio = (fim - ini).total_seconds()
    pad = 16

    def X(t: datetime) -> float:
        return pad + (t - ini).total_seconds() / dominio * (largura - 2 * pad)

    corpo: list[str] = []
    y_eixo = 44.0
    # duração do span, sobre o eixo
    if n > 1:
        meio = (X(t0) + X(t1)) / 2
        txt_d, txt_r = _duracao(span), " do primeiro ao último horário"
        w = largura_texto(txt_d + txt_r, "mono", 24)
        cx = min(max(meio, w / 2), largura - w / 2)
        corpo.append(
            f"<text x='{_f(cx)}' y='22' text-anchor='middle'>"
            + _tspan(txt_d, "mono6", 24, "var(--ink)") + _tspan(txt_r, "mono", 24, "var(--ink2)") + "</text>"
        )
    corpo.append(_linha(pad, y_eixo, largura - pad, y_eixo, "var(--rule2)", 2))
    if n > 1:
        corpo.append(_linha(X(t0), y_eixo, X(t1), y_eixo, "var(--ink)", 6, " stroke-linecap='butt'"))
        for t in (t0, t1):
            corpo.append(_linha(X(t), y_eixo - 10, X(t), y_eixo + 10, "var(--ink)", 3))

    # marcas: primeiro as viradas de dia (sempre rotuladas), depois as demais se couberem
    marcas = []
    t = ini
    while t <= fim:
        marcas.append(t)
        t += timedelta(hours=passo)
    nlinhas = min(n, max_linhas)
    y_linhas = y_eixo + 52
    altura = y_linhas + nlinhas * linha + 6
    ocupado: list[tuple[float, float]] = []
    viradas: list[float] = []  # x de cada meia-noite: fio tracejado que desce pelas linhas
    rotulos_x: list[tuple[float, float, float]] = []  # (cy, x0, x1) de cada rótulo de linha
    for virada in (True, False):
        for t in marcas:
            if (t.hour == 0) != virada:
                continue
            txt = f"00h {t:%d/%m}" if virada else f"{t:%H}h"
            w = largura_texto(txt, "mono", 22)
            x0 = min(max(X(t) - w / 2, 0), largura - w)
            corpo.append(_linha(X(t), y_eixo, X(t), y_eixo + 8, "var(--rule3)", 2))
            if virada:
                viradas.append(X(t))
            if any(not (x0 + w + 14 <= a or x0 >= b + 14) for a, b in ocupado):
                continue
            ocupado.append((x0, x0 + w))
            corpo.append(_texto(x0, y_eixo + 32, txt, "mono6" if virada else "mono", 22,
                                "var(--ink)" if virada else "var(--ink2)"))

    # linhas por veículo; o excesso do meio vira "+k veículos"
    if n > max_linhas:
        cab = max_linhas - 2
        mostradas = pts[:cab] + [None] + pts[-1:]
        ocultas = pts[cab:-1]
    else:
        mostradas, ocultas = list(pts), []
    for i, p in enumerate(mostradas):
        cy = y_linhas + i * linha + linha / 2
        if p is None:
            for nome, t in ocultas:
                lo = lado(nome)
                cor = "var(--ink2)" if lo == "sem" else _COR_STANCE.get(lo or "", "var(--ink3)")
                corpo.append(_circulo(X(t), cy, 6, cor))
            txt = f"+{len(ocultas)} {_plural(len(ocultas), 'veículo')} entre {_hora(ocultas[0][1], ref)} e {_hora(ocultas[-1][1], ref)}"
            w = largura_texto(txt, "mono", 22)
            xd = X(ocultas[-1][1]) + 18
            x_txt, anc = (xd, "start") if xd + w <= largura else (X(ocultas[0][1]) - 18, "end")
            rotulos_x.append((cy, x_txt if anc == "start" else x_txt - w, x_txt + w if anc == "start" else x_txt))
            corpo.append(_texto(x_txt, cy + 8, txt, "mono", 22, "var(--ink2)", anc, HALO))
            continue
        nome, t = p
        lo = lado(nome)
        x = X(t)
        hora = _hora(t, ref)
        tag = "" if lo == "sem" else rot["fora" if lo is None else lo]
        w_h = largura_texto(hora, "mono", 24)
        w_t = largura_texto(tag, "mono", 21) + 16 if tag else 0
        livre_d = largura - (x + 24)
        livre_e = x - 24
        lugar = max(livre_d, livre_e)
        nome_txt = nome
        w_n = largura_texto(nome, "label", 28)
        if w_h + 14 + w_n + w_t > lugar:
            if w_t and w_h + 14 + w_n > lugar - w_t:
                tag, w_t = "", 0
            nome_txt = _cabe(nome, "label", 28, lugar - w_h - 14 - w_t)
        w_tot = w_h + 14 + largura_texto(nome_txt, "label", 28) + w_t
        ax, anc = (x + 24, "start") if w_tot <= livre_d else (x - 24, "end")
        rotulos_x.append((cy, ax if anc == "start" else ax - w_tot, ax + w_tot if anc == "start" else ax))
        # haste desde o primeiro horário: para antes do rótulo quando ele fica à esquerda do ponto
        fim_haste = x if anc == "start" else ax - w_tot - 14
        if n > 1 and fim_haste > X(t0) + 30:
            corpo.append(_linha(X(t0), cy, fim_haste, cy, "var(--rule2)", 2))
        if lo is None:
            corpo.append(_circulo(x, cy, 10, "var(--bg)", " stroke='var(--ink2)' stroke-width='3'"))
        else:
            cor = "var(--ink)" if lo == "sem" else _COR_STANCE[lo]
            corpo.append(_circulo(x, cy, 12, cor, " stroke='var(--bg)' stroke-width='3'"))
        spans = _tspan(hora, "mono", 24, "var(--ink2)") + _tspan(nome_txt, "label", 28, "var(--ink)", 14)
        if tag:
            spans += _tspan(tag, "mono", 21, "var(--ink2)", 16)
        corpo.append(f"<text x='{_f(ax)}' y='{_f(cy + 9)}' text-anchor='{anc}' style='{HALO}'>{spans}</text>")
    # a meia-noite desce pelas linhas, interrompida onde passaria por um rótulo
    for xv in viradas:
        trechos = [(y_eixo + 8, y_linhas)]
        for i in range(len(mostradas)):
            cy = y_linhas + i * linha + linha / 2
            coberto = any(abs(c - cy) < 1 and x0 - 10 <= xv <= x1 + 10 for c, x0, x1 in rotulos_x)
            if not coberto:
                trechos.append((cy - linha / 2, cy + linha / 2))
        for y0, y1 in trechos:
            corpo.insert(0, _linha(xv, y0, xv, y1, "var(--rule2)", 2, " stroke-dasharray='4 8'"))
    if base:
        corpo.append(_texto(0, altura + 30, _cabe(base, "mono", 22, largura), "mono", 22, "var(--ink2)"))
        altura += 42
    return _svg("relogio", largura, altura, corpo, descricao), descricao


# ── espectro ──────────────────────────────────────────────────────────────


def espectro(
    maioria: Sequence[str],
    neutros: Sequence[str],
    minoria: Sequence[str],
    *,
    rotulos: tuple[str, str] = ("maioria", "minoria"),
    lado_maioria: str = "a",
    fora: Sequence[str] = (),
    largura: float = LARGURA,
    max_nomes: int = 7,
) -> tuple[Markup, str]:
    """Espectro em pontos: maioria à esquerda, sem lado no meio (cinza), minoria à direita.

    - ``maioria``, ``neutros``, ``minoria``: nomes dos veículos de cada grupo.
    - ``rotulos``: rótulo da maioria e da minoria (``("sinergia em IA", "complexidade de governança")``).
    - ``lado_maioria``: ``"a"`` ou ``"b"`` (a cor dos pontos segue o lado; a minoria fica com o outro).
    - ``fora``: veículos que publicaram mas ficaram fora da análise (linha no pé, ponto vazado).

    Cada grupo é uma coluna: a fileira de pontos, a contagem grande, o rótulo e os nomes
    (até ``max_nomes``; o resto vira "+k"). Sem percentual.
    """
    lado_minoria = "b" if lado_maioria == "a" else "a"
    grupos = [("maj", list(maioria), _COR_LADO[lado_maioria], rotulos[0])]
    if neutros:
        grupos.append(("neu", list(neutros), _COR_LADO["n"], "sem lado"))
    grupos.append(("min", list(minoria), _COR_LADO[lado_minoria], rotulos[1]))
    total = len(maioria) + len(neutros) + len(minoria)

    def parte(nomes: list[str], rotulo: str) -> str:
        return f"{len(nomes)} {_grupo(rotulo)}" + (f" ({', '.join(nomes)})" if nomes else "")

    lugares = {"maj": "à esquerda", "neu": "no meio", "min": "à direita"}
    descricao = f"Gráfico: espectro com {total} {_plural(total, 'veículo')} {_plural(total, 'analisado')}; " + "; ".join(
        f"{lugares[k]}, {parte(nomes, rot)}" for k, nomes, _, rot in grupos
    )
    if fora:
        descricao += f"; fora da análise: {', '.join(fora)}"
    descricao += "."

    gap = 36
    minimo = 236
    livre = largura - gap * (len(grupos) - 1)
    pesos = [max(len(nomes), 1) for _, nomes, _, _ in grupos]
    larguras = [livre * p / sum(pesos) for p in pesos]
    falta = sum(minimo - w for w in larguras if w < minimo)
    if falta:
        sobra = sum(w - minimo for w in larguras if w > minimo)
        larguras = [minimo if w <= minimo else w - falta * (w - minimo) / sobra for w in larguras]

    corpo: list[str] = []
    x = 0.0
    rot_linhas = [_quebra(rot, "label", 30, w) for (_, _, _, rot), w in zip(grupos, larguras)]
    y_nomes = 206 + 36 * (max(len(r) for r in rot_linhas) - 1)
    passo_nome = 42
    max_linhas = 0
    for (chave, nomes, cor, rot), w, linhas_rot in zip(grupos, larguras, rot_linhas):
        k = len(nomes)
        if k:
            esp = min(36.0, (w - 28) / max(k - 1, 1))
            for i in range(k):
                corpo.append(_circulo(x + 14 + i * esp, 16, 13, cor, " stroke='var(--bg)' stroke-width='3'"))
        else:
            corpo.append(_circulo(x + 14, 16, 12, "none", " stroke='var(--rule3)' stroke-width='2' stroke-dasharray='4 4'"))
        corpo.append(_texto(x, 128, str(k), "hook", 104, "var(--ink)"))
        for j, txt in enumerate(linhas_rot):
            corpo.append(_texto(x, 168 + 36 * j, txt, "label", 30, "var(--ink)"))
        visiveis = nomes if k <= max_nomes else nomes[: max_nomes - 1]
        for j, nome in enumerate(visiveis):
            corpo.append(_texto(x, y_nomes + 14 + j * passo_nome, _cabe(nome, "label", 28, w), "label", 28, "var(--ink2)"))
        linhas = len(visiveis)
        if k > len(visiveis):
            corpo.append(_texto(x, y_nomes + 14 + linhas * passo_nome, f"+{k - len(visiveis)}", "mono6", 24, "var(--ink2)"))
            linhas += 1
        max_linhas = max(max_linhas, linhas)
        x += w + gap
    y = y_nomes + max(max_linhas, 1) * passo_nome + 30
    corpo.append(_linha(0, y - 22, largura, y - 22, "var(--rule)", 2))
    base = f"{total} {_plural(total, 'veículo')} {_plural(total, 'analisado')}"
    corpo.append(_texto(0, y + 12, base, "mono", 22, "var(--ink2)"))
    if fora:
        y += 44
        corpo.append(_circulo(10, y + 4, 9, "var(--bg)", " stroke='var(--ink2)' stroke-width='3'"))
        corpo.append(_texto(30, y + 12, _cabe(f"fora da análise: {', '.join(fora)}", "mono", 22, largura - 30),
                            "mono", 22, "var(--ink2)"))
    return _svg("espectro", largura, y + 24, corpo, descricao), descricao


# ── waffle ────────────────────────────────────────────────────────────────


def waffle(
    total: int,
    destacados: int | Iterable[int],
    contorno_idx: int | None = None,
    *,
    rotulos: tuple[str, str] = ("dividiram", "sem divisão"),
    unidade: str = "assunto com 4 ou mais veículos comparados",
    unidade_plural: str | None = None,
    contorno_rotulo: str = "este Raio-X",
    por_linha: int = 7,
    lado: float = 64,
    gap: float = 8,
    largura: float = LARGURA,
) -> tuple[Markup, str]:
    """Um quadrado por item: os destacados em laranja, o resto em cinza (``--g-n`` a 40%).

    - ``total``: quantos quadrados (a base).
    - ``destacados``: quantos (os primeiros) ou quais índices ficam em laranja.
    - ``contorno_idx``: índice do quadrado que recebe contorno (o assunto deste Raio-X).
    - ``rotulos``: o que os destacados e o resto são ("dividiram", "sem divisão").
    - ``unidade``: o que cada quadrado é, no singular; ``unidade_plural`` se o plural não for
      só acrescentar "s" na primeira palavra.

    Quadrados de 64 px, 7 por linha (com mais de 70, a grade alarga até 10 linhas). À direita,
    as duas contagens em número grande; embaixo, a base.
    """
    primeira, _, resto = unidade.partition(" ")
    plural = unidade_plural or f"{primeira}s {resto}".strip()
    marcados = set(range(destacados)) if isinstance(destacados, int) else set(destacados)
    marcados = {i for i in marcados if 0 <= i < total}
    d = len(marcados)
    descricao = f"Gráfico: grade de {total} {_plural(total, 'quadrado')}, um por {unidade}; {d} {rotulos[0]} e {total - d} {rotulos[1]}"
    if contorno_idx is not None and 0 <= contorno_idx < total:
        descricao += f"; o contorno marca {contorno_rotulo}"
    descricao += "."
    if total <= 0:
        return _vazio("waffle", f"nenhum {unidade}", f"Gráfico: grade vazia; nenhum {unidade}.", largura)

    largura_grade = por_linha * lado + (por_linha - 1) * gap
    if total > por_linha * 10:
        por_linha = math.ceil(total / 10)
        lado = (largura_grade + gap) / por_linha * 0.88
        gap = (largura_grade - por_linha * lado) / max(por_linha - 1, 1)
    linhas = math.ceil(total / por_linha)
    gw = por_linha * lado + (por_linha - 1) * gap
    gh = linhas * lado + (linhas - 1) * gap
    corpo: list[str] = []
    for i in range(total):
        x = (i % por_linha) * (lado + gap)
        y = (i // por_linha) * (lado + gap)
        if i in marcados:
            corpo.append(_rect(x, y, lado, lado, "var(--g-hi)", rx=lado * 0.08))
        else:
            corpo.append(_rect(x, y, lado, lado, "var(--g-n)", rx=lado * 0.08, extra=" fill-opacity='0.4'"))
    if contorno_idx is not None and 0 <= contorno_idx < total:
        x = (contorno_idx % por_linha) * (lado + gap)
        y = (contorno_idx // por_linha) * (lado + gap)
        corpo.append(
            f"<rect x='{_f(x - 5)}' y='{_f(y - 5)}' width='{_f(lado + 10)}' height='{_f(lado + 10)}' "
            f"rx='{_f(lado * 0.12)}' fill='none' stroke='var(--ink)' stroke-width='4'/>"
        )

    # contagens à direita: a dos destacados no alto, a do resto alinhada ao pé da grade
    xl = gw + 56
    wl = largura - xl
    corpo.append(_texto(xl, 92, str(d), "hook", 128, "var(--accent)"))
    corpo.append(_texto(xl, 134, _cabe(rotulos[0], "label", 32, wl), "label", 32, "var(--ink)"))
    cursor = 134
    if contorno_idx is not None and 0 <= contorno_idx < total:
        corpo.append(_rect(xl + 2, cursor + 26, 26, 26, "none", rx=3, extra=" stroke='var(--ink)' stroke-width='4'"))
        corpo.append(_texto(xl + 42, cursor + 47, _cabe(contorno_rotulo, "mono", 22, wl - 42), "mono", 22, "var(--ink2)"))
        cursor += 60
    y_rot2 = max(gh - 4, cursor + 44 + 134)
    corpo.append(_texto(xl, y_rot2 - 42, str(total - d), "hook", 128, "var(--ink)"))
    corpo.append(_texto(xl, y_rot2, _cabe(rotulos[1], "label", 32, wl), "label", 32, "var(--ink)"))
    y_base = max(gh, y_rot2) + 50
    corpo.append(_texto(0, y_base, _cabe(f"base: {total} {plural if total != 1 else unidade}", "mono", 24, largura),
                        "mono", 24, "var(--ink2)"))
    return _svg("waffle", largura, y_base + 12, corpo, descricao), descricao


# ── matriz_pontos ─────────────────────────────────────────────────────────

_FAIXAS = (  # (rótulo com {1}/{n} = unidade no singular/plural, condição, cor, opacidade)
    ("com 5 ou mais {n}", lambda k: k >= 5, "var(--ink)", 1.0),
    ("com 2 a 4 {n}", lambda k: 2 <= k <= 4, "var(--g-n)", 0.62),
    ("com 1 {1}", lambda k: k <= 1, "var(--g-n)", 0.26),
)


def matriz_pontos(
    contagens_por_assunto: Sequence[int],
    destaque: int | None = None,
    *,
    rotulo_destaque: str | None = None,
    colunas: int = 34,
    largura: float = LARGURA,
    altura_max: float = 760,
    rotulos: bool = True,
    unidade: tuple[str, str] = ("fonte", "fontes"),
    item: tuple[str, str] = ("assunto", "assuntos"),
) -> tuple[Markup, str]:
    """Um ponto por assunto do dia, em ``colunas`` colunas, ordenado do mais coberto ao menos.

    - ``contagens_por_assunto``: quantas fontes cada assunto teve (qualquer ordem).
    - ``destaque``: índice (na lista recebida) do assunto em laranja e ampliado.
    - ``rotulo_destaque``: texto sobre o ponto destacado ("SpaceX e Tesla: 12 veículos");
      padrão "12 fontes".
    - ``rotulos``: com True, a coluna da direita rotula as 3 faixas (1, 2 a 4, 5 ou mais);
      False para a versão pequena.

    Tons: cinza claro para 1 fonte, cinza médio para 2 a 4, tinta para 5 ou mais. Embaixo, a base:
    "1.159 assuntos · 60 com 3+ · 22 com 5+ · 1 com 10+".
    """
    ks = [max(int(k), 0) for k in contagens_por_assunto]
    N = len(ks)
    if N == 0:
        return _vazio("matriz", f"nenhum {item[0]}", f"Gráfico: matriz vazia; nenhum {item[0]}.", largura)
    ordem = sorted(range(N), key=lambda i: -ks[i])
    pos_destaque = ordem.index(destaque) if destaque is not None and 0 <= destaque < N else None
    faixas = [
        (rot.replace("{1}", unidade[0]).replace("{n}", unidade[1]), sum(1 for k in ks if cond(k)), cor, op)
        for rot, cond, cor, op in _FAIXAS
    ]
    k_d = ks[destaque] if pos_destaque is not None else 0
    txt_d = rotulo_destaque or f"{k_d} {_plural(k_d, *unidade)}"
    descricao = (
        f"Gráfico: matriz com {_num(N)} {_plural(N, 'ponto')}, um por {item[0]}; "
        + _lista([f"{_num(c)} {rot}" for rot, c, _, _ in reversed(faixas) if c])
    )
    if pos_destaque is not None:
        descricao += f"; em destaque, {txt_d}"
    descricao += "."

    lw = 230 if rotulos else 0
    mw = largura - lw - (36 if rotulos else 0)
    hh = 56 if pos_destaque is not None else 0
    linhas = math.ceil(N / colunas)
    passo = min(mw / colunas, (altura_max - hh - 56) / linhas)
    # folga para o ponto ampliado não sair do quadro na borda
    folga = passo * 0.55 + 3 if pos_destaque is not None else 0
    passo = min((mw - folga) / colunas, (altura_max - hh - 56 - folga) / linhas)
    r = passo * 0.36
    rd = min(passo * 1.05, r * 2.8)
    corpo: list[str] = []
    for p, i in enumerate(ordem):
        if p == pos_destaque:
            continue
        cx = folga + passo * (p % colunas + 0.5)
        cy = hh + folga + passo * (p // colunas + 0.5)
        _, _, cor, op = next(f for f in _FAIXAS if f[1](ks[i]))
        corpo.append(_circulo(cx, cy, r, cor, "" if op == 1 else f" fill-opacity='{op}'"))
    if pos_destaque is not None:
        cx = folga + passo * (pos_destaque % colunas + 0.5)
        cy = hh + folga + passo * (pos_destaque // colunas + 0.5)
        corpo.append(_circulo(cx, cy, rd, "var(--g-hi)", " stroke='var(--bg)' stroke-width='3'"))
        w = largura_texto(txt_d, "label", 28)
        xt = min(max(cx - rd, 0), largura - w)
        corpo.append(_texto(xt, 30, _cabe(txt_d, "label", 28, largura - xt), "label", 28, "var(--ink)"))
        if cy - rd > hh + 4:
            corpo.append(_linha(cx, 40, cx, cy - rd - 3, "var(--ink2)", 2))
    altura_m = hh + folga + linhas * passo
    if rotulos:
        xl = mw + 36
        prox = hh
        acumulado = 0
        for rot, c, cor, op in faixas:
            if not c:
                continue
            alvo = hh + folga + passo * (acumulado // colunas)
            y = max(alvo, prox)
            acumulado += c
            opac = "" if op == 1 else f" fill-opacity='{op}'"
            corpo.append(_circulo(xl + 11, y + 26, 11, cor, opac))
            corpo.append(_texto(xl + 32, y + 42, _num(c), "hook", 52, "var(--ink)"))
            linhas_rot = _quebra(rot, "label", 24, lw - 32)
            for j, txt in enumerate(linhas_rot):
                corpo.append(_texto(xl + 32, y + 76 + 30 * j, txt, "label", 24, "var(--ink2)"))
            prox = y + 104 + 30 * (len(linhas_rot) - 1)
        altura_m = max(altura_m, prox)
    com3, com5, com10 = (sum(1 for k in ks if k >= m) for m in (3, 5, 10))
    base = f"{_num(N)} {_plural(N, *item)} · {_num(com3)} com 3+ · {_num(com5)} com 5+ · {_num(com10)} com 10+"
    y_base = altura_m + 44
    corpo.append(_texto(0, y_base, _cabe(base, "mono", 24, largura), "mono", 24, "var(--ink2)"))
    return _svg("matriz", largura, y_base + 12, corpo, descricao), descricao


# ── linha ─────────────────────────────────────────────────────────────────


def linha(
    pontos: Sequence[tuple[date | datetime, float]],
    faltantes: Iterable[date] = (),
    anotacoes: Iterable[str] | None = None,
    *,
    largura: float = LARGURA,
    altura: float = 520,
    casas: int = 0,
    prefixo: str = "",
    sufixo: str = "",
    compacto: bool = False,
) -> tuple[Markup, str]:
    """Linha da série: um ponto por leitura; a última (hoje) em laranja.

    - ``pontos``: ``[(data ou datetime, valor), …]``, uma leitura por dia.
    - ``faltantes``: dias úteis sem leitura; o trecho que passa por eles sai tracejado.
    - ``anotacoes``: quais rótulos desenhar, entre ``"primeiro"``, ``"ultimo"``, ``"max"`` e
      ``"min"``. Padrão: os quatro (no ``compacto``, primeiro, último e um extremo).
    - ``casas``, ``prefixo``, ``sufixo``: formato dos valores ("US$ " + 83.366).
    - ``compacto``: versão de 440 × 330 do Mercado em linha (passe ``largura``/``altura``).

    Primeiro e último levam valor e data; máxima e mínima, "máx. 209.067 · 09/10" (quando
    coincidem com uma ponta, a ponta ganha "· máx."). A base vai no pé.
    """
    pts = sorted(((_dia(d), float(v)) for d, v in pontos), key=lambda p: p[0])
    n = len(pts)

    def fmt(v: float) -> str:
        return f"{prefixo}{_num(v, casas)}{sufixo}"

    if n == 0:
        return _vazio("linha", "sem leituras", "Gráfico: linha sem leituras.", largura)
    d0, v0 = pts[0]
    dn, vn = pts[-1]
    i_max = max(range(n), key=lambda i: pts[i][1])
    i_min = min(range(n), key=lambda i: pts[i][1])
    buracos = sorted({f for f in faltantes if d0 < f < dn})
    if n == 1:
        descricao = f"Gráfico: uma leitura, {fmt(v0)} em {_ddmm(d0)}."
    else:
        descricao = f"Gráfico: linha com {n} leituras, de {fmt(v0)} ({_ddmm(d0)}) a {fmt(vn)} ({_ddmm(dn)})"
        descricao += f"; máxima de {fmt(pts[i_max][1])} em {_ddmm(pts[i_max][0])}"
        descricao += f"; mínima de {fmt(pts[i_min][1])} em {_ddmm(pts[i_min][0])}"
        if buracos:
            descricao += f"; {len(buracos)} {_plural(len(buracos), 'pregão', 'pregões')} sem leitura, tracejado"
        descricao += "."

    if anotacoes is None:
        if compacto:
            extremo = "max" if i_max not in (0, n - 1) or i_min in (0, n - 1) else "min"
            anotacoes = {"primeiro", "ultimo", extremo}
        else:
            anotacoes = {"primeiro", "ultimo", "max", "min"}
    anotacoes = set(anotacoes)
    tv, td = (20, 17) if compacto else (30, 22)  # tamanho do valor e da data
    lo, hi = pts[i_min][1], pts[i_max][1]
    if hi == lo:
        lo, hi = lo - abs(lo) * 0.01 - 1, hi + abs(hi) * 0.01 + 1
    # margens: rótulo de duas linhas no alto; embaixo, também quando uma ponta está no terço de baixo
    rotulo_h = tv + td + 6 + 18
    topo = rotulo_h + 4
    ponta_baixa = min(v0, vn) <= lo + (hi - lo) * 0.3
    pe = (td + 22) + (rotulo_h + 4 if ponta_baixa else td + 26)
    pad = 14
    plot_h = altura - topo - pe
    dias = max((dn - d0).days, 1)

    def X(d: date) -> float:
        return largura / 2 if n == 1 else pad + (d - d0).days / dias * (largura - 2 * pad)

    def Y(v: float) -> float:
        return topo + (hi - v) / (hi - lo) * plot_h

    corpo: list[str] = []
    if n > 1:
        corpo.append(_linha(pad, Y(v0), largura - pad, Y(v0), "var(--rule2)", 2))
    cheio, tracejado = [], []
    for (da, va), (db, vb) in zip(pts, pts[1:]):
        seg = f"M{_f(X(da))} {_f(Y(va))}L{_f(X(db))} {_f(Y(vb))}"
        (tracejado if any(da < f < db for f in buracos) else cheio).append(seg)
    esp = 3 if compacto else 4
    if cheio:
        corpo.append(f"<path d='{''.join(cheio)}' fill='none' stroke='var(--ink)' stroke-width='{esp}' "
                     "stroke-linecap='round' stroke-linejoin='round'/>")
    if tracejado:
        corpo.append(f"<path d='{''.join(tracejado)}' fill='none' stroke='var(--ink2)' stroke-width='{esp}' "
                     "stroke-dasharray='3 9' stroke-linecap='round'/>")
    for d, v in pts[:-1]:
        corpo.append(_circulo(X(d), Y(v), 3.5 if compacto else 5, "var(--ink)"))
    corpo.append(_circulo(X(dn), Y(vn), 8 if compacto else 11, "var(--g-hi)", " stroke='var(--bg)' stroke-width='3'"))

    # Rótulos: cada um tenta suas posições em ordem e fica na que não cruza a linha nem outro rótulo.
    tracado = [(X(d), Y(v)) for d, v in pts]
    amostras = list(tracado)
    for (xa, ya), (xb, yb) in zip(tracado, tracado[1:]):
        k = max(int(math.hypot(xb - xa, yb - ya) // 4), 1)
        amostras += [(xa + (xb - xa) * j / k, ya + (yb - ya) * j / k) for j in range(1, k)]
    caixas: list[tuple[float, float, float, float]] = []
    piso = altura - (td + 22)  # acima da linha de base

    def custo(x0: float, y0: float, x1: float, y1: float) -> float:
        if x0 < -1 or x1 > largura + 1 or y0 < 0 or y1 > piso:
            return 1e9
        na_linha = sum(1 for px, py in amostras if x0 - 8 <= px <= x1 + 8 and y0 - 6 <= py <= y1 + 6)
        em_rotulo = sum(1 for a, b, c, e in caixas if not (x1 + 10 <= a or x0 >= c + 10 or y1 + 4 <= b or y0 >= e + 4))
        return na_linha + 1000 * em_rotulo

    def rotular(i: int, linhas: list[tuple[str, str, float, str]], opcoes: list[tuple[str, str]], opcional: bool) -> None:
        px, py = tracado[i]
        w = max(largura_texto(t, f, tam) for t, f, tam, _ in linhas)
        h = sum(tam for _, _, tam, _ in linhas) + 6 * (len(linhas) - 1)
        melhor = None
        # cada posição perto do ponto; depois, as mesmas afastadas (com fio até o ponto)
        tentativas = [(a, o, 0) for a, o in opcoes] + [(a, o, k * 26) for k in (1, 2, 3) for a, o in opcoes if o != "lado"]
        for ancora, onde, desvio in tentativas:
            x = px + (18 if ancora == "start" else -18) if onde == "lado" else px
            if ancora == "middle":
                x = min(max(x, w / 2), largura - w / 2)
            topo_txt = {"acima": py - 16 - h - desvio, "abaixo": py + 16 + desvio, "lado": py - h / 2}[onde]
            x0 = x if ancora == "start" else (x - w if ancora == "end" else x - w / 2)
            c = custo(x0, topo_txt, x0 + w, topo_txt + h)
            if melhor is None or c < melhor[0]:
                melhor = (c, x, topo_txt, ancora, (x0, topo_txt, x0 + w, topo_txt + h), onde, desvio)
            if c == 0:
                break
        if melhor is None or (opcional and melhor[0] > 0) or melhor[0] >= 1e9:
            return
        _, x, y, ancora, caixa, onde, desvio = melhor
        if desvio:
            ponta_fio = caixa[3] + 4 if onde == "acima" else caixa[1] - 4
            corpo.append(_linha(px, py + (-10 if onde == "acima" else 10), px, ponta_fio, "var(--ink3)", 2))
        caixas.append(caixa)
        for t, fonte, tam, cor in linhas:
            y += tam
            corpo.append(_texto(x, y - tam * 0.18, t, fonte, tam, cor, ancora, HALO))
            y += 6

    def ponta(i: int) -> list[tuple[str, str, float, str]]:
        d, v = pts[i]
        marca = " · máx." if i == i_max and n > 1 else (" · mín." if i == i_min and n > 1 else "")
        cor = "var(--accent)" if i == n - 1 else "var(--ink)"
        return [(fmt(v), "mono6", tv, cor), (_ddmm(d) + marca, "mono", td, "var(--ink2)")]

    if "ultimo" in anotacoes:
        lado = [("end", "acima"), ("end", "abaixo"), ("end", "lado")] if n > 1 else [("middle", "acima"), ("middle", "abaixo")]
        rotular(n - 1, ponta(n - 1), lado, False)
    if "primeiro" in anotacoes and n > 1:
        rotular(0, ponta(0), [("start", "acima"), ("start", "abaixo"), ("start", "lado")], False)
    for chave, i, prefixo_txt, ordem in (("max", i_max, "máx.", ("acima", "abaixo")), ("min", i_min, "mín.", ("abaixo", "acima"))):
        if chave not in anotacoes or i in (0, n - 1) or n < 3:
            continue
        d, v = pts[i]
        txt = f"{prefixo_txt} {fmt(v)} · {_ddmm(d)}"
        rotular(i, [(txt, "mono", td, "var(--ink2)")], [("middle", o) for o in ordem], True)

    if n > 1:
        base = f"{n} leituras, de {_ddmm(d0)} a {_ddmm(dn)}"
        if buracos:
            base += f" · {len(buracos)} {_plural(len(buracos), 'pregão', 'pregões')} sem leitura (tracejado)"
        corpo.append(_texto(0, altura - 8, _cabe(base, "mono", td, largura), "mono", td, "var(--ink2)"))
    return _svg("linha", largura, altura, corpo, descricao), descricao


# ── barras_variacao ───────────────────────────────────────────────────────


def barras_variacao(
    changes: Sequence[tuple[date | datetime, float]],
    escala: float | None = None,
    *,
    largura: float = LARGURA,
    altura: float = 520,
    casas: int = 2,
    max_barras: int = 30,
    rotulo_hoje: str = "hoje",
) -> tuple[Markup, str]:
    """Barras das variações diárias (%), a última (hoje) em laranja.

    - ``changes``: ``[(data ou datetime, variação em %), …]``; ficam as ``max_barras`` mais recentes.
    - ``escala``: a variação típica (escala robusta, em %); desenha tracejados em ±2 e ±3 vezes ela
      e a anotação "2,6 vezes a variação típica". Sem escala, só as barras.
    """
    pts = sorted(((_dia(d), float(c)) for d, c in changes), key=lambda p: p[0])[-max_barras:]
    n = len(pts)
    if n == 0:
        return _vazio("barras", "sem leituras", "Gráfico: barras sem leituras.", largura)
    d0, dn = pts[0][0], pts[-1][0]
    hoje = pts[-1][1]
    z = abs(hoje) / escala if escala else None
    vez = "vez" if z is not None and z < 2 else "vezes"  # "1,6 vez", "2,6 vezes"
    descricao = f"Gráfico: barras com {n} {_plural(n, 'variação diária', 'variações diárias')}"
    descricao += f", de {_ddmm(d0)} a {_ddmm(dn)}" if n > 1 else f", em {_ddmm(d0)}"
    descricao += f"; {rotulo_hoje}, {_pct(hoje, casas)}"
    if z is not None:
        descricao += (f", {_num(z, 1)} {vez} a variação típica de {_num(escala, 2)}%; "
                      "linhas tracejadas em 2 e 3 vezes essa variação")
    descricao += "."

    topo, pe, dir_ = 96, 86, (82 if escala else 0)
    plot_w = largura - dir_
    plot_h = altura - topo - pe
    m = max(max(abs(c) for _, c in pts), 3 * escala if escala else 0, 0.01) * 1.08
    y0 = topo + plot_h / 2

    def Y(v: float) -> float:
        return y0 - v / m * plot_h / 2

    corpo: list[str] = []
    if escala:
        for k in (2, 3):
            for s in (1, -1):
                y = Y(s * k * escala)
                corpo.append(_linha(0, y, plot_w, y, "var(--ink3)", 2, " stroke-dasharray='8 8'"))
                rot = f"{'+' if s > 0 else MENOS}{k}×"
                corpo.append(_texto(largura, y + 8, rot, "mono", 22, "var(--ink2)", "end"))
    passo = plot_w / n
    bw = min(24.0, passo * 0.62)
    for i, (d, c) in enumerate(pts):
        x = passo * i + (passo - bw) / 2
        y, h = min(Y(c), y0), max(abs(Y(c) - y0), 2)
        if i == n - 1:
            corpo.append(_rect(x, y, bw, h, "var(--g-hi)", rx=3))
        else:
            corpo.append(_rect(x, y, bw, h, "var(--g-n)", rx=3, extra=" fill-opacity='0.55'"))
    corpo.append(_linha(0, y0, plot_w, y0, "var(--rule3)", 2))
    # anotação de hoje, no alto, alinhada à barra
    xh = min(passo * (n - 1) + (passo + bw) / 2, plot_w)
    linha1 = f"{rotulo_hoje}: {_pct(hoje, casas)}"
    corpo.append(_texto(xh, 32, linha1, "mono6", 30, "var(--accent)", "end"))
    if z is not None:
        corpo.append(_texto(xh, 68, f"{_num(z, 1)} {vez} a variação típica", "mono", 24, "var(--ink2)", "end"))
    y_datas = topo + plot_h + 34
    corpo.append(_texto(passo * 0.5 - bw / 2, y_datas, _ddmm(d0), "mono", 22, "var(--ink2)"))
    if n > 1:
        corpo.append(_texto(xh, y_datas, _ddmm(dn), "mono", 22, "var(--ink2)", "end"))
    base = f"{n} {_plural(n, 'leitura diária', 'leituras diárias')}"
    if escala:
        base += f" · variação típica {_num(escala, 2)}% (escala robusta)"
    corpo.append(_texto(0, altura - 8, _cabe(base, "mono", 22, largura), "mono", 22, "var(--ink2)"))
    return _svg("barras", largura, altura, corpo, descricao), descricao


# ── checklist ─────────────────────────────────────────────────────────────


def checklist(
    veiculos: Sequence[str],
    confirmados: Iterable[str],
    horas: Mapping[str, datetime | None] | Sequence[datetime | None] | None = None,
    *,
    ref_date: date | None = None,
    largura: float = LARGURA,
    linha: float = 72,
    max_linhas: int = 12,
    base: str = "com o número no título",
) -> tuple[Markup, str]:
    """Lista de verificação do Número triangulado: ✓ laranja se o veículo pôs o número no título, "—" se não.

    - ``veiculos``: os veículos do assunto, na ordem em que aparecem.
    - ``confirmados``: os que puseram o número no título.
    - ``horas``: ``{nome: datetime}`` (ou lista na ordem de ``veiculos``) com o horário de cada um.
    - ``ref_date``: dia da edição (outro dia leva a data na frente da hora).

    Nenhum título de veículo aparece: só o nome, a marca e a hora.
    """
    nomes = list(dict.fromkeys(veiculos))
    ok = set(confirmados)
    if isinstance(horas, Mapping):
        hora_de = dict(horas)
    elif horas is not None:
        hora_de = dict(zip(nomes, horas))
    else:
        hora_de = {}
    n, k = len(nomes), sum(1 for nm in nomes if nm in ok)
    if n == 0:
        return _vazio("checklist", "nenhum veículo", "Gráfico: lista vazia; nenhum veículo.", largura)

    def com_hora(nm: str) -> str:
        h = hora_de.get(nm)
        return f"{nm}, {_hora(h)}" if h else nm

    sim = [com_hora(nm) for nm in nomes if nm in ok]
    nao = [nm for nm in nomes if nm not in ok]
    descricao = f"Gráfico: lista de {n} {_plural(n, 'veículo')}; {k} de {n} {base}"
    if sim:
        descricao += f" ({'; '.join(sim)})"
    if nao:
        descricao += f"; sem o número: {', '.join(nao)}"
    descricao += "; títulos não reproduzidos."

    mostrados = nomes if n <= max_linhas else nomes[: max_linhas - 1]
    corpo: list[str] = []
    for i, nm in enumerate(mostrados):
        y = i * linha
        cy = y + linha / 2
        if nm in ok:
            corpo.append(f"<path d='M{_f(4)} {_f(cy)}l12 13 24-26' fill='none' stroke='var(--g-hi)' "
                         "stroke-width='7' stroke-linecap='round' stroke-linejoin='round'/>")
        else:
            corpo.append(_linha(8, cy, 36, cy, "var(--ink3)", 4, " stroke-linecap='round'"))
        h = hora_de.get(nm)
        hora = _hora(h, ref_date) if h else ""
        w_h = largura_texto(hora, "mono", 26) if hora else 0
        corpo.append(_texto(64, cy + 12, _cabe(nm, "label", 34, largura - 64 - w_h - 24), "label", 34,
                            "var(--ink)" if nm in ok else "var(--ink2)"))
        if hora:
            corpo.append(_texto(largura, cy + 10, hora, "mono", 26, "var(--ink2)", "end"))
        corpo.append(_linha(0, y + linha, largura, y + linha, "var(--rule)", 2))
    y = len(mostrados) * linha
    if n > len(mostrados):
        resto = nomes[len(mostrados):]
        kr = sum(1 for nm in resto if nm in ok)
        corpo.append(_texto(64, y + linha / 2 + 10, f"+{len(resto)} {_plural(len(resto), 'veículo')} ({kr} com o número)",
                            "mono", 24, "var(--ink2)"))
        y += linha
    corpo.append(_texto(0, y + 52, f"{k} de {n} {_plural(n, 'veículo')} {base}", "mono6", 26, "var(--ink)"))
    return _svg("checklist", largura, y + 64, corpo, descricao), descricao
