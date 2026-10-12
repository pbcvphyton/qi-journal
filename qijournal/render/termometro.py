"""Views do Termômetro de Viés Editorial: o medidor do dia (fim da edição) e as
peças reaproveitadas pela aba "Analítico Político" (:mod:`qijournal.render.analitico`).

Tudo aqui sai pronto para o template: textos limpos, números já formatados em
pt-BR (sinal de menos tipográfico) e posições em % na escala de −100 (esquerda)
a +100 (direita). Os números vêm só do cálculo de referência
(:mod:`qijournal.termometro.referencia`) e da agregação
(:mod:`qijournal.termometro.agregacao`); nada é recalculado aqui.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import text
from ..termometro import agregacao, historico
from ..termometro.referencia import NOMES, PARAMETROS, arredondar
from . import filters

ANALITICO_PAGINA = "analitico.html"

# Faixas do termômetro (PARAMETROS["faixas"] = 10, 25, 50), da esquerda para a direita.
_F1, _F2, _F3 = PARAMETROS["faixas"]
FAIXAS = (
    (-100, -_F3, "ef", "Esquerda forte"),
    (-_F3, -_F2, "em", "Esquerda moderada"),
    (-_F2, -_F1, "el", "Leve à esquerda"),
    (-_F1, _F1, "eq", "Equilibrado"),
    (_F1, _F2, "dl", "Leve à direita"),
    (_F2, _F3, "dm", "Direita moderada"),
    (_F3, 100, "df", "Direita forte"),
)
CLASSE = {rotulo: classe for _, _, classe, rotulo in FAIXAS} | {"Misto": "mx"}
ORDEM_ROTULOS = [rotulo for *_, rotulo in FAIXAS] + ["Misto"]
CONFIANCA = {"alta": "alta", "media": "média", "baixa": "baixa"}
DIRECAO = {"esquerda": "esquerda", "direita": "direita", "equilibrio": "equilíbrio"}
EIXOS = {
    "economico": "Econômico",
    "costumes": "Costumes",
    "seguranca": "Segurança",
    "instituicoes": "Instituições",
    "geral": "Geral",
}
MENOS = "−"


# ── números ──────────────────────────────────────────────────────────────────


def fmt_t(valor: float | None) -> str:
    """T arredondado como no cálculo, com sinal: "−14", "+8", "0"."""
    if valor is None:
        return "—"
    t = arredondar(valor)
    return f"{MENOS}{abs(t)}" if t < 0 else f"+{t}" if t > 0 else "0"


def fmt_dec(valor: float | None, casas: int = 1, sinal: bool = True) -> str:
    """Decimal em pt-BR com sinal tipográfico: "−1,2", "+0,4"."""
    if valor is None:
        return "—"
    numero = text.format_number_pt(abs(valor), casas)
    if round(valor, casas) == 0:
        return numero
    if valor < 0:
        return f"{MENOS}{numero}"
    return f"+{numero}" if sinal else numero


def fmt_q(valor: float | None) -> str:
    return "—" if valor is None else str(arredondar(valor))


def fmt_ic(ic: list[float] | None, fmt=fmt_t) -> str:
    return f"{fmt(ic[0])} a {fmt(ic[1])}" if ic else ""


def pct(valor: float, dominio: float = 100.0) -> float:
    """Posição (0 a 100%) de um valor numa escala simétrica de −dominio a +dominio."""
    valor = max(-dominio, min(dominio, valor))
    return round((valor + dominio) / (2 * dominio) * 100, 2)


def contagem(n: int, singular: str, plural: str) -> str:
    return f"{text.format_number_pt(n, 0)} {singular if n == 1 else plural}"


# ── medidor e distribuição ───────────────────────────────────────────────────


@dataclass
class FaixaView:
    classe: str
    rotulo: str
    left: float
    width: float


@dataclass
class GaugeView:
    """Escala de −100 a +100 com as faixas, o ponto da média e o intervalo de 95%."""

    pct: float
    valor: str  # "−14"
    rotulo: str
    classe: str
    ic: str  # "−20 a −8" (vazio sem intervalo)
    ic_left: float | None
    ic_width: float | None
    aria: str
    faixas: list[FaixaView]


def faixas_view() -> list[FaixaView]:
    return [FaixaView(c, r, pct(a), round(pct(b) - pct(a), 2)) for a, b, c, r in FAIXAS]


def gauge(media: float | None, ic: list[float] | None, rotulo: str | None, *, descricao: str) -> GaugeView | None:
    if media is None:
        return None
    rotulo = rotulo or agregacao.rotulo_T(media)
    ic_left = ic_width = None
    if ic:
        ic_left = pct(ic[0])
        ic_width = round(max(0.4, pct(ic[1]) - ic_left), 2)
    texto_ic = fmt_ic(ic)
    aria = f"{descricao}: T {fmt_t(media)} ({rotulo})" + (f", intervalo de 95% de {texto_ic}" if texto_ic else "")
    return GaugeView(
        pct=pct(media),
        valor=fmt_t(media),
        rotulo=rotulo,
        classe=CLASSE.get(rotulo, "eq"),
        ic=texto_ic,
        ic_left=ic_left,
        ic_width=ic_width,
        aria=aria + "; escala de −100 (esquerda) a +100 (direita)",
        faixas=faixas_view(),
    )


@dataclass
class SegmentoView:
    rotulo: str
    classe: str
    n: int
    pct: float


@dataclass
class DistribuicaoView:
    segmentos: list[SegmentoView]
    total: int
    aria: str


def distribuicao(rotulos: Mapping[str, int]) -> DistribuicaoView | None:
    """Barra com a contagem de cada rótulo, da esquerda forte à direita forte (Misto à parte, no fim)."""
    total = sum(int(rotulos.get(r, 0) or 0) for r in ORDEM_ROTULOS)
    if not total:
        return None
    segmentos = [
        SegmentoView(r, CLASSE[r], int(rotulos[r]), round(int(rotulos[r]) * 100 / total, 2))
        for r in ORDEM_ROTULOS
        if int(rotulos.get(r, 0) or 0) > 0
    ]
    aria = "Rótulos dos textos: " + "; ".join(f"{s.rotulo}: {s.n}" for s in segmentos)
    return DistribuicaoView(segmentos=segmentos, total=total, aria=aria)


# ── análises ─────────────────────────────────────────────────────────────────


@dataclass
class EvidenciaView:
    id: str
    criterio: str
    citacao: str
    direcao: str
    contribuicao: str
    explicacao: str


@dataclass
class AnaliseView:
    id: str
    veiculo: str
    editoria: str
    titulo: str
    url: str | None
    data: str  # "29/09"
    estado: str
    valor: str  # "−14" ou "—"
    pct: float | None
    rotulo: str
    classe: str
    confianca: str
    q: str
    q_rotulo: str
    selo: str
    resumo: str
    eixos: str
    evidencias: list[EvidenciaView] = field(default_factory=list)


def _data_curta(iso: str | None) -> str:
    iso = str(iso or "")
    return f"{iso[8:10]}/{iso[5:7]}" if len(iso) >= 10 else iso


def analise_view(a: Mapping[str, Any], *, evidencias: bool = True) -> AnaliseView:
    estado = str(a.get("estado") or "")
    valor = a.get("T") if estado == "ok" else None
    rotulo = filters.plain(str(a.get("rotulo") or ""))
    qualidade = a.get("qualidade_apuracao") or {}
    lista = []
    for e in (a.get("evidencias") or []) if evidencias else []:
        if not isinstance(e, dict):
            continue
        lista.append(
            EvidenciaView(
                id=filters.plain(str(e.get("id") or "")),
                criterio=NOMES.get(str(e.get("criterio")), filters.plain(str(e.get("criterio") or ""))),
                citacao=filters.plain(str(e.get("citacao") or "")),
                direcao=DIRECAO.get(str(e.get("direcao")), ""),
                contribuicao=fmt_dec(e.get("contribuicao") if isinstance(e.get("contribuicao"), (int, float)) else None),
                explicacao=filters.plain(str(e.get("explicacao") or "")),
            )
        )
    eixos = [EIXOS.get(str(x), str(x)) for x in (a.get("triagem") or {}).get("eixos") or []]
    return AnaliseView(
        id=str(a.get("id") or ""),
        veiculo=filters.plain(str(a.get("veiculo") or "")),
        editoria=filters.plain(str(a.get("editoria") or "")),
        titulo=filters.plain(str(a.get("titulo") or "")),
        url=filters.safe_url(a.get("url")),
        data=_data_curta(a.get("data_publicacao")),
        estado=estado,
        valor=fmt_t(valor),
        pct=pct(valor) if isinstance(valor, (int, float)) else None,
        rotulo=rotulo,
        classe=CLASSE.get(rotulo, "na"),
        confianca=CONFIANCA.get(str(a.get("confianca")), ""),
        q=fmt_q(qualidade.get("Q")),
        q_rotulo=filters.plain(str(qualidade.get("rotulo") or "")),
        selo=filters.plain(str(a.get("selo") or "")),
        resumo=filters.plain(str(a.get("resumo") or "")),
        eixos=", ".join(eixos),
        evidencias=lista,
    )


# ── o dia ────────────────────────────────────────────────────────────────────


@dataclass
class TermometroDiaView:
    """Bloco "Termômetro do dia", no fim da edição."""

    vazio: bool
    motivo: str
    gauge: GaugeView | None
    distribuicao: DistribuicaoView | None
    resumo: str  # "Leve à esquerda · T −14 nas 11 notícias com dimensão política"
    meta: list[str]
    analises: list[AnaliseView]
    analitico_href: str = ""


def valida(a: Any) -> bool:
    """Análise no formato da especificação, com o que a agregação lê."""
    return isinstance(a, dict) and all(
        isinstance(a.get(k), str) and a.get(k) for k in ("id", "veiculo", "editoria", "data_publicacao", "versao_calculo")
    )


def analises_validas(registro: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Análises do registro no formato da especificação (as malformadas ficam de fora)."""
    return [a for a in (registro or {}).get("analises") or [] if valida(a)]


def termometro_dia_view(registro: Mapping[str, Any] | None) -> TermometroDiaView | None:
    """O resumo do termômetro das notícias compiladas na edição (notícias; opinião à parte)."""
    if registro is None:
        return None
    analises = [a for a in analises_validas(registro) if a["versao_calculo"] == PARAMETROS["versao"]]
    if not analises:
        motivo = filters.plain(str(registro.get("motivo") or "nenhum texto analisado"))
        return TermometroDiaView(True, motivo, None, None, "", [], [])
    linhas = agregacao.acumular(analises)
    noticias = agregacao.resumo(linhas)
    opiniao = agregacao.resumo([l for l in linhas if l["tipo"] == "opiniao"], incluir_opiniao=True)
    veiculos = len({a["veiculo"] for a in analises})
    meta = [f"{contagem(len(analises), 'texto analisado', 'textos analisados')} de {contagem(veiculos, 'veículo', 'veículos')}"]
    if noticias["n_nao_se_aplica"]:
        meta.append(f"{noticias['n_nao_se_aplica']} sem dimensão política")
    if noticias["n_inconclusivo"]:
        meta.append(f"{contagem(noticias['n_inconclusivo'], 'inconclusivo', 'inconclusivos')}")
    if noticias["media_Q"] is not None:
        meta.append(f"qualidade da apuração {fmt_q(noticias['media_Q'])} de 100 ({noticias['rotulo_Q']})")
    if opiniao["n_total"]:
        texto = contagem(opiniao["n_total"], "texto de opinião", "textos de opinião")
        if opiniao["media_T"] is not None:
            texto += f" (T {fmt_t(opiniao['media_T'])}, à parte)"
        meta.append(texto)
    medidor = gauge(noticias["media_T"], noticias["ic95_T"], noticias["rotulo"], descricao="Média das notícias do dia")
    if medidor is not None:
        resumo = (
            f"{medidor.rotulo} · T {medidor.valor} na média de "
            f"{contagem(noticias['n'], 'notícia com dimensão política', 'notícias com dimensão política')}"
        )
    else:
        resumo = "Nenhuma notícia com dimensão política e texto suficiente para o termômetro"
    ordem = {"ok": 0, "inconclusivo": 1, "nao_se_aplica": 2}
    views = sorted(
        (analise_view(a) for a in analises),
        key=lambda v: (ordem.get(v.estado, 3), v.pct if v.pct is not None else 50.0, v.veiculo),
    )
    return TermometroDiaView(
        vazio=False,
        motivo="",
        gauge=medidor,
        distribuicao=distribuicao(noticias["rotulos"]),
        resumo=resumo,
        meta=meta,
        analises=views,
    )


def todas_validas(registros: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Todas as análises do histórico (uma por texto), só as bem formadas."""
    return [a for a in historico.todas_as_analises(registros) if valida(a)]
