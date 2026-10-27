"""Regras de texto dos posts e o lint que confere cada peça antes de renderizar.

Na Onda 1 não há IA nova: o lint confere o texto montado por moldes a partir do
JSON da edição (§4.1 e itens 2, 3 e 11 do §5.4). Se uma peça reprova, ela cai
para o substituto e o motivo vai para o log e para o e-mail.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import estatistica as E

LINHA1_MAX = 110  # 1ª linha da legenda (a que aparece no feed)
LEGENDA_MAX = 1300
ALT_MAX = 1000

# §5.4, item 3: sensacionalismo e vícios de texto de IA.
SENSACIONALISMO = (
    "urgente", "bomba", "chocante", "inacreditável",
    "despenca", "despencam", "despencou", "despencaram",
    "dispara", "disparam", "disparou", "dispararam",
    "derrete", "derretem", "derreteu", "derreteram",
    "desaba", "desabam", "desabou", "desabaram",
    "explode", "explodem", "explodiu", "explodiram",
    "polêmica", "escândalo", "você não vai acreditar", "tudo o que você precisa saber", "a verdade sobre", "segredo",
)
VICIOS_IA = (
    "cenário", "em meio a", "desdobramentos", "vale destacar", "vale lembrar", "nesse sentido",
    "ponto de inflexão", "divisor de águas", "sem precedentes", "acende alerta",
)
PROIBIDAS = SENSACIONALISMO + VICIOS_IA

# §4.1: o que um veículo fez. Horário nunca vira "primeiro a noticiar" nem "furo".
VERBOS_VEICULO = ("destacou", "enfatizou", "priorizou", "focou", "registrou", "citou", "publicou às")
VERBOS_PROIBIDOS = (
    "omitiu", "omitiram", "ignorou", "ignoraram", "escondeu", "esconderam", "distorceu", "distorceram",
    "errou", "erraram", "enviesou", "enviesaram", "furou", "furaram", "atrasou", "atrasaram", "admitiu", "admitiram",
    "primeiro a noticiar", "primeira a noticiar", "furo",
)


def _lista_re(termos: Iterable[str]) -> re.Pattern:
    alts = sorted((re.escape(t).replace(r"\ ", r"\s+") for t in termos), key=len, reverse=True)
    return re.compile(r"(?<![\wÀ-ú])(?:" + "|".join(alts) + r")(?![\wÀ-ú])", re.I)


_PROIBIDAS_RE = _lista_re(PROIBIDAS)
_VERBOS_PROIBIDOS_RE = _lista_re(VERBOS_PROIBIDOS)
_CITADOS = {"framing", "framings"}  # texto do veículo, usado como está no JSON: o lint não julga
_SEM_TEXTO = {"color", "id", "trend", "stance", "svg"}


def palavras_proibidas(texto: str) -> list[str]:
    """Termos de sensacionalismo e vícios de IA no texto (em minúsculas, na ordem)."""
    return [re.sub(r"\s+", " ", m.group(0).lower()) for m in _PROIBIDAS_RE.finditer(texto or "")]


def verbos_proibidos(texto: str) -> list[str]:
    """Verbos que julgam o veículo ("omitiu", "errou"...) e "furo"/"primeiro a noticiar"."""
    return [re.sub(r"\s+", " ", m.group(0).lower()) for m in _VERBOS_PROIBIDOS_RE.finditer(texto or "")]


def assinatura(handle: str | None) -> str:
    """``"▲ @triangula"`` com o @ configurado; sem ele, ``"▲ triangula"`` (nunca falha)."""
    handle = re.sub(r"[^A-Za-z0-9._]", "", (handle or "").strip().lstrip("@"))
    return f"▲ @{handle}" if handle else "▲ triangula"


# ── Lint ──────────────────────────────────────────────────────────────────


def _textos(value: Any, citados: list[str], key: str = "") -> list[str]:
    """Os textos de um contexto de slide (recursivo); os citados de veículo vão para ``citados``."""
    if isinstance(value, str):
        if key in _CITADOS:
            citados.append(value)
            return []
        if key in _SEM_TEXTO or value.lstrip().startswith("<"):
            return []
        return [value]
    if isinstance(value, dict):
        return [t for k, v in value.items() for t in _textos(v, citados, k if k in _CITADOS | _SEM_TEXTO else "")]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in _textos(v, citados, key)]
    return []


def _sem_citados(texto: str, citados: list[str]) -> str:
    for c in sorted(citados, key=len, reverse=True):
        if c:
            texto = texto.replace(E._plain(c), " ").replace(c, " ")
    return texto


def _secao(ctx: dict[str, Any]) -> str:
    section = ctx.get("section")
    if isinstance(section, dict):
        return str(section.get("id") or "")
    return section if isinstance(section, str) else ""


def _lista(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


def lint(
    peca: Any,
    *,
    election: bool,
    overflow: Iterable[int | str] = (),
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    veiculos: Iterable[str] = (),
) -> list[str]:
    """Problemas de texto de uma :class:`~.conteudo.Peca` (lista vazia: aprovada).

    Confere, em todo slide, texto alternativo e legenda:

    - palavras proibidas (sensacionalismo e vícios de IA, §5.4) e verbos que julgam veículo;
    - no modo eleitoral, o léxico eleitoral de :func:`.estatistica.is_electoral` (a seção do
      slide vem de ``ctx["section"]``; na legenda, nomes e partidos);
    - sempre: ``ctx["destaque"]`` (o laranja) e ``ctx["gancho"]`` sem nome de político nem
      partido; o destaque também sem adjetivo da casa nem nome de veículo (``veiculos``);
    - limites: 1ª linha da legenda até 110 caracteres, legenda até 1.300, alt até 1.000;
    - ``overflow``: slides (número a partir de 1, ou descrição) em que o texto transbordou
      mesmo com o ``data-fit`` no mínimo.

    O enquadramento de cada veículo (chaves ``framing``/``framings``) é citado como está no
    JSON e não é julgado.
    """
    nomes = tuple(nomes)
    veiculos = [v for v in veiculos if v]
    problemas: list[str] = []
    citados: list[str] = []
    slides = list(getattr(peca, "slides", []) or [])
    por_slide = [_textos(s.ctx, citados) for s in slides]

    def palavras(onde: str, texto: str) -> None:
        for w in palavras_proibidas(texto):
            problemas.append(f"{onde}: palavra proibida “{w}”")
        for w in verbos_proibidos(texto):
            problemas.append(f"{onde}: verbo proibido “{w}”")

    for i, (slide, textos) in enumerate(zip(slides, por_slide), 1):
        onde = f"slide {i}"
        texto = " · ".join(textos)
        alt = _sem_citados(slide.alt or "", citados)
        palavras(onde, texto)
        palavras(f"{onde} (alt)", alt)
        secao = _secao(slide.ctx)
        if election:
            for nome, t in ((onde, texto), (f"{onde} (alt)", alt)):
                if E.is_electoral(t, secao, nomes):
                    problemas.append(f"{nome}: termo eleitoral no período eleitoral")
        for chave in ("destaque", "gancho"):
            for valor in _lista(slide.ctx.get(chave)):
                if E.cita_nome(valor, nomes) or E.cita_partido(valor):
                    problemas.append(f"{onde}: {chave} com nome de político ou partido (“{valor}”)")
                if chave == "destaque" and E.ADJ_CASA.search(valor):
                    problemas.append(f"{onde}: destaque com adjetivo da casa (“{valor}”)")
                if chave == "destaque" and any(v.lower() in valor.lower() for v in veiculos):
                    problemas.append(f"{onde}: destaque com nome de veículo (“{valor}”)")
        alt_len = len(re.sub(r"\s+", " ", E._plain(slide.alt or "")).strip())
        if alt_len > ALT_MAX:
            problemas.append(f"{onde}: texto alternativo com {alt_len} caracteres (máx. {ALT_MAX})")

    caption = getattr(peca, "caption", "") or ""
    if caption:
        legenda = _sem_citados(caption, citados)
        palavras("legenda", legenda)
        if election and (E.cita_nome(legenda, nomes) or E._PARTIDO.search(legenda)):
            problemas.append("legenda: nome de político ou partido no período eleitoral")
        primeira = caption.strip().split("\n", 1)[0].strip()
        if len(primeira) > LINHA1_MAX:
            problemas.append(f"legenda: 1ª linha com {len(primeira)} caracteres (máx. {LINHA1_MAX})")
        if len(caption) > LEGENDA_MAX:
            problemas.append(f"legenda: {len(caption)} caracteres (máx. {LEGENDA_MAX})")

    for item in overflow:
        problemas.append(f"slide {item}: texto transbordou" if isinstance(item, int) else f"transbordou: {item}")
    return problemas
