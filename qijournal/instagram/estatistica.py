"""Contas dos posts sobre a edição do dia e o histórico em ``data/*.json``.

Tudo determinístico e verificável, sem IA: cada número sai com a base ("5 de
36"), porcentagem só com base de pelo menos :data:`PCT_MIN` casos, e "fontes"
quando a contagem inclui órgãos públicos ("veículos" só sem eles).
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from qijournal import text as qtext
from qijournal.models import Coverage, Edition, IndexTopic, Story

TZ = ZoneInfo("America/Sao_Paulo")
PCT_MIN = 20  # abaixo disso, só "X de Y"
PLACAR_MIN_OUTLETS = 4  # o placar conta assuntos com 4+ veículos comparados
RACHA_MIN_SIDE = 2  # racha: pelo menos 2 veículos de cada lado
TRIANGULADO_MIN = 3  # o mesmo número no título de 3+ veículos
CONCENTRACAO_MIN = 12  # assunto com 12+ veículos (P75 dos máximos diários de 29/09 a 21/10)
Z_FORTE, Z_MEDIO = 3.0, 2.0  # movimento de mercado fora do comum (escala robusta)
SERIE_MIN_PONTOS = 15  # para estimar a escala robusta de um ativo
REPETICAO_DIAS = 7  # o mesmo número não volta antes disso
MERCADO_Z_IDS = ["usd", "ibov", "brent", "btc", "spx", "gold"]
FECHAMENTO_IDS = {"ibov", "ifix", "spx"}  # pregões com horário: leitura depois das 17h é fechamento
SECOES_ECONOMIA = {"brasil", "mercados"}

# Órgãos públicos e entidades entre as fontes: entram em "fontes", não em "veículos".
ORGAOS = {
    "Agência Câmara", "Agência Senado", "Agência FAPESP", "CNJ", "STF", "STJ", "TJDFT", "TJMG",
    "TRF6", "TSE", "TST", "Secovi-SP",
}

_NUMERO = re.compile(
    r"(?:(?:R\$|US\$|€)\s?\d[\d.,]*(?:\s?(?:bilh(?:ão|ões)|bi|milh(?:ão|ões)|mi|trilh(?:ão|ões)|tri|mil)\b)?"
    r"|\d[\d.,]*\s?%"
    r"|\d[\d.,]*\s(?:bilh(?:ão|ões)|milh(?:ão|ões)|trilh(?:ão|ões))\b)",
    re.I,
)
_PESQUISA = re.compile(
    r"\b(pesquisas?|datafolha|quaest|atlasintel|ipec|ipespe|poderdata|nexus|inten[cç][aã]o de voto|"
    r"empate t[eé]cnico|margem de erro|pontos percentuais)\b",
    re.I,
)


# ── Formatação ────────────────────────────────────────────────────────────


def num(value: float, decimals: int = 0) -> str:
    return qtext.format_number_pt(value, decimals)


def fracao(x: int, y: int) -> str:
    """``5, 36`` → ``"5 de 36"``; com base ≥ :data:`PCT_MIN`, ``"13 de 85 (15%)"``."""
    if y >= PCT_MIN:
        return f"{x} de {y} ({round(100 * x / y)}%)"
    return f"{x} de {y}"


def hora(dt: datetime, ref: date | None = None) -> str:
    """``"09h24"``; com a data na frente quando não é o dia ``ref``: ``"20/10 09h24"``."""
    local = dt.astimezone(TZ)
    label = local.strftime("%Hh%M")
    return label if ref is None or local.date() == ref else f"{local:%d/%m} {label}"


def duracao(delta: timedelta) -> str:
    """``timedelta(hours=16, minutes=44)`` → ``"16h44"``; menos de 1 h → ``"38 min"``."""
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}h{minutes % 60:02d}"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def is_poll(value: str) -> bool:
    return bool(_PESQUISA.search(value or ""))


# ── Histórico ─────────────────────────────────────────────────────────────


def load_history(data_dir: Path, until: date, days: int = 90) -> list[Edition]:
    """Edições de ``data_dir`` de ``until - days`` até ``until`` (inclusive), em ordem de data."""
    out: list[Edition] = []
    start = until - timedelta(days=days)
    for path in sorted(Path(data_dir).glob("????-??-??.json")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if not start <= day <= until:
            continue
        try:
            out.append(Edition.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


# ── Coberturas e placar ───────────────────────────────────────────────────


def coverages(edition: Edition) -> list[tuple[Coverage, Story | None]]:
    """Todas as coberturas comparadas do dia: as das matérias e as dos outros assuntos."""
    out: list[tuple[Coverage, Story | None]] = [(s.coverage, s) for s in edition.stories if s.coverage]
    out += [(c, None) for c in edition.compared]
    return out


def coverage_section(cov: Coverage, story: Story | None) -> str:
    return (story.section if story else cov.section) or ""


def is_political_coverage(cov: Coverage, story: Story | None) -> bool:
    text = " ".join([cov.topic, cov.side_a, cov.side_b, story.headline if story else "", story.dek if story else ""])
    return coverage_section(cov, story) == "politica" or is_poll(text)


def is_racha(cov: Coverage) -> bool:
    return cov.has_debate and cov.count("a") >= RACHA_MIN_SIDE and cov.count("b") >= RACHA_MIN_SIDE


@dataclass
class Placar:
    rachas: int  # assuntos com 4+ veículos e pelo menos 2 de cada lado
    base: int  # assuntos com 4+ veículos comparados
    por_secao: dict[str, tuple[int, int]] = field(default_factory=dict)  # seção → (rachas, base)

    @property
    def texto(self) -> str:
        return fracao(self.rachas, self.base)


def placar(edition: Edition, *, election: bool) -> Placar:
    """Quantos dos assuntos comparados com 4+ veículos dividiram a imprensa (política fora no período eleitoral)."""
    result = Placar(0, 0)
    for cov, story in coverages(edition):
        if len(cov.outlets) < PLACAR_MIN_OUTLETS:
            continue
        if election and is_political_coverage(cov, story):
            continue
        racha = is_racha(cov)
        result.base += 1
        result.rachas += racha
        sec = coverage_section(cov, story)
        r, b = result.por_secao.get(sec, (0, 0))
        result.por_secao[sec] = (r + racha, b + 1)
    return result


# ── Concentração (o index: todas as notícias do dia agrupadas por assunto) ─


def topic_sources(topic: IndexTopic, *, veiculos: bool = False) -> list[str]:
    seen: list[str] = []
    for item in topic.items:
        if item.source and item.source not in seen and not (veiculos and item.source in ORGAOS):
            seen.append(item.source)
    return seen


@dataclass
class Concentracao:
    assuntos: int
    uma_fonte: int  # assuntos que saíram em uma fonte só
    com3: int
    com5: int
    com10: int
    topo: IndexTopic | None = None  # o assunto com mais veículos (fora política no período eleitoral)
    topo_veiculos: int = 0

    @property
    def uma_fonte_pct(self) -> int:
        return round(100 * self.uma_fonte / self.assuntos) if self.assuntos else 0


def concentracao(edition: Edition, *, election: bool) -> Concentracao | None:
    if not edition.index:
        return None
    ks = [len(topic_sources(t)) for t in edition.index]
    result = Concentracao(
        assuntos=len(ks),
        uma_fonte=sum(1 for k in ks if k == 1),
        com3=sum(1 for k in ks if k >= 3),
        com5=sum(1 for k in ks if k >= 5),
        com10=sum(1 for k in ks if k >= 10),
    )
    for topic in edition.index:
        if election and (topic.section == "politica" or is_poll(topic.title)):
            continue
        k = len(topic_sources(topic, veiculos=True))
        if k > result.topo_veiculos:
            result.topo, result.topo_veiculos = topic, k
    return result


def topic_of(edition: Edition, story_id: str) -> IndexTopic | None:
    return next((t for t in edition.index if t.story_id == story_id), None)


# ── Relógio da notícia ────────────────────────────────────────────────────


@dataclass
class Batida:
    source: str
    at: datetime
    stance: str | None = None  # "a" | "b" | "neutro" quando o veículo está na cobertura comparada


@dataclass
class Relogio:
    batidas: list[Batida]  # primeiro horário de cada veículo, em ordem

    @property
    def span(self) -> timedelta:
        return self.batidas[-1].at - self.batidas[0].at if self.batidas else timedelta(0)


def relogio(edition: Edition, story: Story, *, veiculos: bool = True) -> Relogio | None:
    """Quando cada veículo publicou o assunto (horário informado pelo próprio veículo)."""
    topic = topic_of(edition, story.id)
    if topic is None:
        return None
    stance = {o.name: o.stance for o in story.coverage.outlets} if story.coverage else {}
    first: dict[str, datetime] = {}
    for item in topic.items:
        at = _parse_dt(item.published)
        if at is None or not item.source or (veiculos and item.source in ORGAOS):
            continue
        if item.source not in first or at < first[item.source]:
            first[item.source] = at
    if len(first) < 2:
        return None
    batidas = sorted((Batida(s, at, stance.get(s)) for s, at in first.items()), key=lambda b: b.at)
    return Relogio(batidas)


# ── Mercado ───────────────────────────────────────────────────────────────


@dataclass
class Ponto:
    as_of: datetime
    value: float
    change_pct: float | None

    @property
    def day(self) -> date:
        return self.as_of.astimezone(TZ).date()


def serie(history: Iterable[Edition], qid: str) -> list[Ponto]:
    """Uma leitura por dia (a última de cada dia), sem repetir a mesma leitura de edições seguidas."""
    by_day: dict[date, Ponto] = {}
    for edition in history:
        for q in edition.quotes:
            if q.id != qid:
                continue
            at = _parse_dt(q.as_of)
            if at is None:
                continue
            p = Ponto(at, q.value, q.change_pct)
            if p.day not in by_day or at > by_day[p.day].as_of:
                by_day[p.day] = p
    return [by_day[d] for d in sorted(by_day)]


def is_close(qid: str, at: datetime) -> bool:
    """A leitura é de fechamento? Só faz sentido para pregões com horário (Ibovespa, S&P 500)."""
    return qid in FECHAMENTO_IDS and at.astimezone(TZ).hour >= 17


def stale_reading(p: Ponto) -> bool:
    """Leitura do começo do dia sem variação (o dólar às 09h00 com 0,00%): não serve de dado."""
    local = p.as_of.astimezone(TZ)
    return local.hour < 7 and not p.change_pct


def escala_robusta(pontos: list[Ponto]) -> float | None:
    """MAD × 1,4826 das variações diárias (robusta a dias extremos); None com menos de 15 pontos."""
    changes = [p.change_pct for p in pontos if p.change_pct is not None and not stale_reading(p)]
    if len(changes) < SERIE_MIN_PONTOS:
        return None
    med = statistics.median(changes)
    mad = statistics.median(abs(c - med) for c in changes)
    return mad * 1.4826 or None


@dataclass
class Acumulado:
    first: Ponto
    last: Ponto
    pct: float  # variação ponta a ponta, em %
    high: Ponto
    low: Ponto


def acumulado(pontos: list[Ponto]) -> Acumulado | None:
    if len(pontos) < 2:
        return None
    first, last = pontos[0], pontos[-1]
    return Acumulado(
        first=first,
        last=last,
        pct=(last.value / first.value - 1) * 100,
        high=max(pontos, key=lambda p: p.value),
        low=min(pontos, key=lambda p: p.value),
    )


# ── O Número do dia ───────────────────────────────────────────────────────


@dataclass
class Numero:
    valor: str  # o número em destaque: "12 veículos", "0,3%", "−4,42%"
    tese: str  # a frase que diz de que é o número
    selo: str  # a prova: "no título de 5 de 5 veículos", "fech. 20/10"...
    tipo: str  # "triangulado" | "concentracao" | "mercado"
    metodo: str  # como medimos, em uma linha
    section: str | None = None
    story_id: str | None = None
    chave: str = ""  # forma normalizada do número (para não repetir na semana)


def _core(value: str) -> str:
    s = value.lower().replace(" ", "").replace(" ", "")
    s = re.sub(r"^(r\$|us\$|€)", "", s)
    s = re.sub(r"bilh(ão|ões)$", "bi", s)
    s = re.sub(r"milh(ão|ões)$", "mi", s)
    s = re.sub(r"trilh(ão|ões)$", "tri", s)
    return s


def _title_has(core: str, title: str) -> bool:
    t = title.lower().replace(" ", "").replace(" ", "")
    t = re.sub(r"bilh(ão|ões)", "bi", t)
    t = re.sub(r"milh(ão|ões)", "mi", t)
    t = re.sub(r"trilh(ão|ões)", "tri", t)
    return re.search(r"(?<![\d,.])" + re.escape(core) + r"(?![\d])", t) is not None


def _display_number(raw: str) -> str:
    """``"R$ 1 bilhão"`` → ``"R$ 1 bi"`` (cabe na capa); o resto como veio."""
    s = re.sub(r"\s+", " ", raw.strip())
    s = re.sub(r"\s?bilh(ão|ões)$", " bi", s, flags=re.I)
    s = re.sub(r"\s?milh(ão|ões)$", " mi", s, flags=re.I)
    s = re.sub(r"\s?trilh(ão|ões)$", " tri", s, flags=re.I)
    return s


def triangulados(edition: Edition, *, election: bool) -> list[Numero]:
    """Números da manchete e da linha fina que aparecem no título de 3+ veículos do assunto."""
    out: list[tuple[tuple, Numero]] = []
    for story in edition.stories:
        text = f"{qtext.strip_markdown(story.headline)} {qtext.strip_markdown(story.dek)}"
        if story.section == "esporte" or (election and (story.section == "politica" or is_poll(text))):
            continue
        topic = topic_of(edition, story.id)
        if topic is None:
            continue
        sources = topic_sources(topic, veiculos=True)
        for raw in dict.fromkeys(m.strip() for m in _NUMERO.findall(text)):
            core = _core(raw)
            confirm = {i.source for i in topic.items if i.source in sources and _title_has(core, i.title)}
            if len(confirm) < TRIANGULADO_MIN:
                continue
            numero = Numero(
                valor=_display_number(raw),
                tese=qtext.strip_markdown(story.headline),
                selo=f"no título de {len(confirm)} de {len(sources)} veículos",
                tipo="triangulado",
                metodo=f"O mesmo número no título de {len(confirm)} veículos diferentes que publicaram o assunto.",
                section=story.section,
                story_id=story.id,
                chave=core,
            )
            # Primeiro o número que a maioria dos veículos do assunto pôs no título.
            key = (2 * len(confirm) >= len(sources), story.section in SECOES_ECONOMIA, len(confirm), story.importance)
            out.append((key, numero))
    out.sort(key=lambda kv: kv[0], reverse=True)
    return [n for _, n in out]


def _mercado(edition: Edition, history: list[Edition], z_min: float) -> Numero | None:
    best: tuple[float, Numero] | None = None
    labels = {q.id: q.label for q in edition.quotes}
    for qid in MERCADO_Z_IDS:
        q = next((q for q in edition.quotes if q.id == qid), None)
        at = _parse_dt(q.as_of) if q else None
        if q is None or at is None or q.change_pct is None:
            continue
        p = Ponto(at, q.value, q.change_pct)
        if stale_reading(p):
            continue
        series = [x for x in serie(history, qid) if x.as_of < at]
        scale = escala_robusta(series)
        if not scale:
            continue
        z = abs(q.change_pct) / scale
        if z < z_min:
            continue
        verb = "sobe" if q.change_pct > 0 else "cai"
        when = f"fech. {at.astimezone(TZ):%d/%m}" if is_close(qid, at) else f"leitura das {hora(at)}"
        sign = "+" if q.change_pct > 0 else "−"
        valor = f"{sign}{abs(q.change_pct):.2f}%".replace(".", ",")
        numero = Numero(
            valor=valor,
            tese=f"{labels.get(qid, qid)} {verb} {abs(q.change_pct):.2f}%".replace(".", ","),
            selo=f"{q.source or 'Yahoo Finance'} · {when}",
            tipo="mercado",
            metodo=f"Variação do dia {num(z, 1)} vezes a típica do ativo "
            f"(desvio robusto de {len(series)} leituras desde {series[0].as_of.astimezone(TZ):%d/%m}).",
            section="mercados",
            chave=f"{qid}:{valor}",
        )
        if best is None or z > best[0]:
            best = (z, numero)
    return best[1] if best else None


def _concentracao_numero(edition: Edition, *, election: bool) -> Numero | None:
    conc = concentracao(edition, election=election)
    if conc is None or conc.topo is None or conc.topo_veiculos < CONCENTRACAO_MIN:
        return None
    topic = conc.topo
    return Numero(
        valor=f"{conc.topo_veiculos} veículos",
        tese=qtext.strip_markdown(topic.title),
        selo=f"o assunto mais coberto entre {num(conc.assuntos)} do dia",
        tipo="concentracao",
        metodo=f"Veículos diferentes que publicaram o assunto; {fracao(conc.uma_fonte, conc.assuntos)} "
        "dos assuntos do dia saíram em uma fonte só.",
        section=topic.section,
        story_id=topic.story_id,
        chave=f"conc:{topic.title[:40]}",
    )


def numero_do_dia(edition: Edition, history: list[Edition], *, election: bool) -> Numero | None:
    """O Número do dia, pela primeira regra que tiver candidato:

    1. mercado fora do comum (variação ≥ 3 desvios robustos do próprio ativo);
    2. número triangulado (no título de 3+ veículos), sem repetir os dos últimos 7 dias;
    3. o assunto mais coberto, se tiver 12+ veículos;
    4. mercado com variação ≥ 2 desvios robustos;
    5. nenhum: o post usa a manchete.
    """
    if edition.mode != "ai":
        return None
    day = date.fromisoformat(edition.date)
    past = [e for e in history if date.fromisoformat(e.date) < day]
    forte = _mercado(edition, past, Z_FORTE)
    if forte:
        return forte
    recent = {
        n.chave
        for e in past
        if (day - date.fromisoformat(e.date)).days <= REPETICAO_DIAS and e.mode == "ai"
        for n in triangulados(e, election=election)[:1]
    }
    for n in triangulados(edition, election=election):
        if n.chave not in recent:
            return n
    return _concentracao_numero(edition, election=election) or _mercado(edition, past, Z_MEDIO)
