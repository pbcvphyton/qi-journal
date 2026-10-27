"""Contas dos posts sobre a edição do dia e o histórico em ``data/*.json``.

Tudo determinístico e verificável, sem IA: cada número sai com a base ("5 de
36"), porcentagem só com base de pelo menos :data:`PCT_MIN` casos, e "fontes"
quando a contagem inclui órgãos públicos ("veículos" só sem eles).
"""

from __future__ import annotations

import json
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from qijournal import text as qtext
from qijournal.models import Coverage, Edition, IndexTopic, Quote, Story

TZ = ZoneInfo("America/Sao_Paulo")
PCT_MIN = 20  # abaixo disso, só "X de Y"
PLACAR_MIN_OUTLETS = 4  # o placar conta assuntos com 4+ veículos comparados
RACHA_MIN_SIDE = 2  # racha: pelo menos 2 veículos de cada lado
TRIANGULADO_MIN = 3  # o mesmo número no título de 3+ veículos
CONCENTRACAO_MIN = 12  # assunto com 12+ veículos (P75 dos máximos diários de 29/09 a 21/10)
Z_FORTE, Z_MEDIO = 3.0, 2.0  # movimento de mercado fora do comum (escala robusta)
SERIE_MIN_PONTOS = 15  # para estimar a escala robusta de um ativo
ACUMULADO_MIN_PONTOS = 10  # leituras para o acumulado virar Número (= limiares.grafico_min_pontos)
REPETICAO_DIAS = 7  # o mesmo número não volta antes disso
RELOGIO_MIN_VEICULOS = 4  # relógio da notícia só com 4+ veículos com horário...
RELOGIO_MIN_SPAN = timedelta(minutes=30)  # ...e 30 min entre o primeiro e o último
TESE_MAX_PALAVRAS = 14
MERCADO_Z_IDS = ["usd", "ibov", "brent", "btc", "spx", "gold"]
ACUMULADO_IDS = ["ibov", "usd", "brent", "btc", "spx", "gold"]
FECHAMENTO_IDS = {"ibov", "ifix", "spx"}  # pregões com horário: leitura depois das 17h é fechamento
SECOES_ECONOMIA = {"brasil", "mercados"}
CASAS = {"ibov": 0, "ifix": 0, "spx": 0, "btc": 0, "gold": 0, "usd": 2, "eur": 2, "cny": 2, "brent": 2}

# Pregões sem negociação (dia útil sem leitura nesses dias não é buraco na série).
_FERIADOS_B3 = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-04-03", "2026-04-21", "2026-05-01", "2026-06-04",
    "2026-09-07", "2026-10-12", "2026-11-02", "2026-11-20", "2026-12-24", "2026-12-25", "2026-12-31",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-04-21", "2027-05-27",
}
_FERIADOS_NYSE = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
}
FERIADOS = {
    "ibov": {date.fromisoformat(d) for d in _FERIADOS_B3},
    "ifix": {date.fromisoformat(d) for d in _FERIADOS_B3},
    "spx": {date.fromisoformat(d) for d in _FERIADOS_NYSE},
}

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
_ANO = re.compile(r"(?:19|20)\d\d")
_QUALIFICADOR = re.compile(r"\b(cerca de|mais de|até|quase|aproximadamente|em torno de)\s*$", re.I)
_PESQUISA = re.compile(
    r"\b(pesquisas?|datafolha|quaest|atlasintel|ipec|ipespe|poderdata|nexus|inten[cç][aã]o de voto|"
    r"empate t[eé]cnico|margem de erro|pontos percentuais)\b",
    re.I,
)
# A parte do _PESQUISA que só existe em pesquisa eleitoral: "pesquisa" e "pontos percentuais"
# soltos, no texto inteiro da cobertura, pegam IBGE, pesquisa científica e a pesquisa
# Reuters do IBC-Br (16/10).
_PESQUISA_ELEITORAL = re.compile(
    r"\b(pesquisas? eleitora(?:l|is)|datafolha|quaest|atlasintel|ipec|ipespe|poderdata|nexus|"
    r"inten[cç](?:[aã]o|[oõ]es) de voto|empate t[eé]cnico|margem de erro)\b",
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


def sinal(value: float, decimals: int = 1) -> str:
    """``13.83`` → ``"+13,8%"``; ``-4.08`` → ``"−4,1%"`` (sinal de menos tipográfico)."""
    rounded = round(value, decimals)
    mark = "+" if rounded > 0 else "−" if rounded < 0 else ""
    return f"{mark}{num(abs(rounded), decimals)}%"


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


def _plain(value: str | None) -> str:
    return qtext.strip_markdown(value or "")


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def palavras(value: str) -> list[str]:
    return _plain(value).split()


def is_poll(value: str) -> bool:
    return bool(_PESQUISA.search(value or ""))


# ── Filtro eleitoral por conteúdo ─────────────────────────────────────────

NOMES_ELEICAO = ("Lula", "Flávio Bolsonaro", "Bolsonaro")  # padrão de instagram.eleicao.nomes
SECOES_PARTIDO = {"brasil", "politica", "juridico", "mercados"}
PARTIDOS = (
    "PL", "PT", "MDB", "PSDB", "PSOL", "PSD", "PP", "PDT", "PSB", "Novo", "Republicanos", "União Brasil", "Podemos",
)

_ELEITORAL_SIGLAS = re.compile(r"\b(?:TSE|TREs?)\b")
_ELEITORAL_FORTE = re.compile(
    r"\b(?:justiça eleitoral|(?:primeiro|segundo|1º|2º) turno|urnas?|propaganda eleitoral|horário eleitoral|"
    r"candidat[oa]s?|candidatura|campanha (?:eleitoral|de|do|da)|chapa|intenção de voto|cassação)\b",
    re.I,
)
# Só com artigo ou preposição antes: "PL" solto também é projeto de lei ("o PL 2338" não conta).
_PARTIDO = re.compile(
    r"\b(?i:do|da|pelo|pela|o|a)\s(?:PL(?!\s?\d)|PT|MDB|PSDB|PSOL|PSD|PP|PDT|PSB|Novo|Republicanos|União Brasil|Podemos)\b"
)
# Em destaque, gancho e rótulo a regra é permanente e mais dura: qualquer sigla conta.
_PARTIDO_SOLTO = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in PARTIDOS) + r")\b")


@lru_cache(maxsize=32)
def _nomes_re(nomes: tuple[str, ...]) -> re.Pattern | None:
    nomes = tuple(n.strip() for n in nomes if n and n.strip())
    if not nomes:
        return None
    alts = sorted((re.escape(n) for n in nomes), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b")


def cita_nome(text: str, nomes: Iterable[str] = NOMES_ELEICAO) -> bool:
    """O texto cita um dos políticos da lista (com borda de palavra: "Flávio Dino" não é "Flávio Bolsonaro")."""
    pattern = _nomes_re(tuple(nomes))
    return bool(pattern and pattern.search(text or ""))


def cita_partido(text: str) -> bool:
    """Sigla ou nome de partido em qualquer lugar do texto (regra do destaque, do gancho e do rótulo)."""
    return bool(_PARTIDO_SOLTO.search(text or ""))


def is_electoral(text: str, section: str | None, nomes: Iterable[str] = NOMES_ELEICAO) -> bool:
    """O texto é eleitoral? Três famílias de regra (§4.0.1):

    - forte, em toda seção menos ``mundo``: TSE, TRE, Justiça Eleitoral, turno, urna,
      propaganda e horário eleitoral, candidato, campanha de, chapa, intenção de voto,
      cassação e os termos de pesquisa eleitoral do ``_PESQUISA`` (institutos, empate
      técnico, margem de erro);
    - nomes de ``instagram.eleicao.nomes``, em toda seção;
    - partido com artigo ou preposição antes ("do PL"), só em brasil, política, jurídico e mercados.

    "voto" sozinho (voto de ministro do STF) e "eleição" em Mundo não contam.
    """
    text = text or ""
    if cita_nome(text, nomes):
        return True
    if section != "mundo" and (
        _ELEITORAL_SIGLAS.search(text) or _ELEITORAL_FORTE.search(text) or _PESQUISA_ELEITORAL.search(text)
    ):
        return True
    return section in SECOES_PARTIDO and bool(_PARTIDO.search(text))


def coverage_text(cov: Coverage | None, story: Story | None = None) -> str:
    """Texto analisado pelo filtro: manchete, linha fina, assunto, lados, conclusão e enquadramentos."""
    parts = [story.headline, story.dek] if story else []
    if cov is not None:
        parts += [cov.topic, cov.side_a, cov.side_b, cov.conclusion, *(o.framing for o in cov.outlets)]
    return " ".join(_plain(p) for p in parts if p)


def story_text(story: Story) -> str:
    return coverage_text(story.coverage, story)


def story_is_electoral(story: Story, nomes: Iterable[str] = NOMES_ELEICAO) -> bool:
    return is_electoral(story_text(story), story.section, nomes)


def is_political_coverage(cov: Coverage, story: Story | None, nomes: Iterable[str] = NOMES_ELEICAO) -> bool:
    section = coverage_section(cov, story)
    head = " ".join([cov.topic, cov.side_a, cov.side_b, story.headline if story else "", story.dek if story else ""])
    return section == "politica" or is_poll(head) or is_electoral(coverage_text(cov, story), section, nomes)


# ── Adjetivo da casa e número de capa ─────────────────────────────────────

ADJ_CASA = re.compile(
    r"\b(?:gigantes?|históric[oa]s?|inédit[oa]s?|bilionári[oa]s?|polêmic[oa]s?|chocantes?|explosiv[oa]s?|"
    r"dramátic[oa]s?|colossa(?:l|is)|recordes?)\b",
    re.I,
)
_ADJ_RAIZES = ("gigante", "historic", "inedit", "bilionari", "polemic", "chocante", "explosiv", "dramatic", "colossa", "recorde")


def _raiz_adj(word: str) -> str:
    folded = _fold(word)
    return next((r for r in _ADJ_RAIZES if folded.startswith(r)), folded)


def _adj_nos_titulos(raiz: str, titulos: Sequence[str]) -> bool:
    """O adjetivo aparece no título de pelo menos metade dos veículos?"""
    if not titulos:
        return False
    com = sum(1 for t in titulos if any(_raiz_adj(m.group(0)) == raiz for m in ADJ_CASA.finditer(t or "")))
    return 2 * com >= len(titulos)


def sem_adj_casa(texto: str, titulos: Sequence[str] = (), dek: str = "") -> str:
    """Texto de capa sem adjetivo da casa (§4.1).

    O adjetivo fica se estiver no título de pelo menos metade dos veículos do assunto
    (``titulos``: um texto por veículo). Senão, corta-se no último " e " ou "," antes
    dele, se sobrarem 5 palavras ou mais; se não sobrarem, vale o ``dek`` (e, sem
    ``dek``, devolve "").
    """
    out = _plain(texto)
    while True:
        bad = next((m for m in ADJ_CASA.finditer(out) if not _adj_nos_titulos(_raiz_adj(m.group(0)), titulos)), None)
        if bad is None:
            return out
        cut = max(out.rfind(" e ", 0, bad.start()), out.rfind(", ", 0, bad.start()))
        head = out[:cut].strip() if cut > 0 else ""
        if len(head.split()) >= 5:
            out = head
            continue
        if not dek:
            return ""
        return sem_adj_casa(dek, titulos) or _plain(dek)


def titulos_por_veiculo(topic: IndexTopic | None) -> list[str]:
    """Os títulos de cada veículo do assunto (um texto por veículo, órgãos públicos fora)."""
    if topic is None:
        return []
    by_source: dict[str, list[str]] = {}
    for item in topic.items:
        if item.source and item.source not in ORGAOS:
            by_source.setdefault(item.source, []).append(item.title or "")
    return [" · ".join(ts) for ts in by_source.values()]


def capa_sem_adj(edition: Edition, story: Story, texto: str | None = None) -> str:
    """:func:`sem_adj_casa` com os títulos do assunto da matéria e o ``dek`` como reserva."""
    titulos = titulos_por_veiculo(topic_of(edition, story.id))
    return sem_adj_casa(story.headline if texto is None else texto, titulos, dek=story.dek)


_PENDURADAS = {"a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "em", "na", "no", "nas", "nos",
               "para", "por", "pela", "pelo", "com", "sobre", "ao", "à", "e", "ou", "que", "entre", "após", "sem"}


def _corta_palavras(texto: str, limite: int) -> str:
    """Até ``limite`` palavras: corta na última vírgula antes disso; sem vírgula, no último
    " e "; sem os dois, no limite, sem artigo ou preposição pendurados no fim."""
    words = _plain(texto).split()
    if len(words) <= limite:
        return " ".join(words)
    head = " ".join(words[:limite]).rstrip(",")
    for sep in (", ", " e "):
        cut = head.rfind(sep)
        if cut > 0 and len(head[:cut].split()) >= 5:
            return head[:cut].strip()
    words = head.rstrip(",;:—-").split()
    while len(words) > 1 and words[-1].lower() in _PENDURADAS:
        words.pop()
    return " ".join(words)


def _corta_na_juncao(texto: str, limite: int) -> str:
    """O texto até a última "," ou " e " antes da palavra ``limite + 1``, se sobrarem 5+ palavras."""
    words = texto.split()
    if len(words) <= limite:
        return texto
    head = " ".join(words[: limite + 1])
    cut = max(head.rfind(", "), head.rfind(" e "))
    return head[:cut].strip() if cut > 0 and len(head[:cut].split()) >= 5 else ""


def tese_da_materia(edition: Edition, story: Story, limite: int = TESE_MAX_PALAVRAS) -> str:
    """Tese de capa a partir da matéria (§4.4): a manchete sem adjetivo da casa; com mais de
    ``limite`` palavras, cortada no último " e " ou ","; senão o assunto da cobertura; se
    ainda passar, a linha fina cortada na última vírgula antes da palavra ``limite``."""
    titulos = titulos_por_veiculo(topic_of(edition, story.id))
    topic = story.coverage.topic if story.coverage and story.coverage.topic else ""
    for texto in (story.headline, topic):
        clean = sem_adj_casa(texto, titulos) if texto else ""
        cut = _corta_na_juncao(clean, limite) if clean else ""
        if cut:
            return cut
    dek = sem_adj_casa(story.dek, titulos) or _plain(story.dek)
    return _corta_palavras(dek or _plain(story.headline), limite)


def numero_destaque(texto: str) -> tuple[str, str] | None:
    """O número de capa do texto: ``(qualificador, número)``, ex.: ``("CERCA DE", "R$ 1 bi")``.

    É o primeiro número com unidade (R$, US$, %, bilhões...); ano solto não conta. O
    qualificador (cerca de, mais de, até, quase, aproximadamente, em torno de) vem em
    caixa alta, ou ``""``.
    """
    texto = _plain(texto)
    for m in _NUMERO.finditer(texto):
        raw = m.group(0).strip()
        if _ANO.fullmatch(raw):
            continue
        q = _QUALIFICADOR.search(texto[: m.start()])
        return (q.group(1).upper() if q else "", _display_number(raw))
    return None


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


def is_racha(cov: Coverage) -> bool:
    return cov.has_debate and cov.count("a") >= RACHA_MIN_SIDE and cov.count("b") >= RACHA_MIN_SIDE


@dataclass
class Placar:
    rachas: int  # assuntos com 4+ veículos e pelo menos 2 de cada lado
    base: int  # assuntos com 4+ veículos comparados
    por_secao: dict[str, tuple[int, int]] = field(default_factory=dict)  # seção → (rachas, base)
    itens: list[tuple[Coverage, Story | None]] = field(default_factory=list)  # a base, na ordem (para o waffle)

    @property
    def texto(self) -> str:
        return fracao(self.rachas, self.base)

    @property
    def texto_dia(self) -> str:
        """O placar diário vai sempre sem porcentagem: ``"4 de 26"``."""
        return f"{self.rachas} de {self.base}"


def placar(edition: Edition, *, election: bool, nomes: Iterable[str] = NOMES_ELEICAO) -> Placar:
    """Quantos dos assuntos comparados com 4+ veículos dividiram a imprensa (política fora no período eleitoral)."""
    nomes = tuple(nomes)
    result = Placar(0, 0)
    for cov, story in coverages(edition):
        if len(cov.outlets) < PLACAR_MIN_OUTLETS:
            continue
        if election and is_political_coverage(cov, story, nomes):
            continue
        racha = is_racha(cov)
        result.base += 1
        result.rachas += racha
        result.itens.append((cov, story))
        sec = coverage_section(cov, story)
        r, b = result.por_secao.get(sec, (0, 0))
        result.por_secao[sec] = (r + racha, b + 1)
    return result


def rachas_do_dia(edition: Edition, *, election: bool, nomes: Iterable[str] = NOMES_ELEICAO) -> list[tuple[Coverage, Story | None]]:
    """Os rachas da base do placar, do mais dividido (maior min(a, b), depois a + b) ao menos."""
    base = placar(edition, election=election, nomes=nomes).itens
    rachas = [(c, s) for c, s in base if is_racha(c)]
    return sorted(rachas, key=lambda cs: (-min(cs[0].count("a"), cs[0].count("b")), -(cs[0].count("a") + cs[0].count("b"))))


def maior_consenso(edition: Edition, *, election: bool, nomes: Iterable[str] = NOMES_ELEICAO) -> tuple[Coverage, Story | None] | None:
    """A cobertura da base do placar com mais veículos em que ninguém ficou nem no lado a nem no b."""
    base = placar(edition, election=election, nomes=nomes).itens
    sem_lado = [(c, s) for c, s in base if c.count("a") == 0 and c.count("b") == 0]
    return max(sem_lado, key=lambda cs: len(cs[0].outlets), default=None)


# ── Concentração (o index: todas as notícias do dia agrupadas por assunto) ─


def topic_sources(topic: IndexTopic, *, veiculos: bool = False) -> list[str]:
    seen: list[str] = []
    for item in topic.items:
        if item.source and item.source not in seen and not (veiculos and item.source in ORGAOS):
            seen.append(item.source)
    return seen


def topic_texts(topic: IndexTopic, *, veiculos: bool = True) -> int:
    """Quantos textos o assunto teve (dos veículos, sem órgãos públicos)."""
    return sum(1 for i in topic.items if i.source and not (veiculos and i.source in ORGAOS))


def topic_of(edition: Edition, story_id: str) -> IndexTopic | None:
    return next((t for t in edition.index if t.story_id == story_id), None)


def topic_text(edition: Edition, topic: IndexTopic) -> str:
    """Texto do assunto para o filtro eleitoral: o título e, se virou matéria, a matéria inteira."""
    story = next((s for s in edition.stories if s.id == topic.story_id), None) if topic.story_id else None
    return " ".join(p for p in (_plain(topic.title), story_text(story) if story else "") if p)


def topic_is_political(edition: Edition, topic: IndexTopic, nomes: Iterable[str] = NOMES_ELEICAO) -> bool:
    return topic.section == "politica" or is_poll(topic.title) or is_electoral(topic_text(edition, topic), topic.section, nomes)


def assuntos_mais_cobertos(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = NOMES_ELEICAO,
    exclude: Iterable[str] = (),
) -> list[tuple[IndexTopic, int]]:
    """Assuntos por número de veículos (órgãos fora), do maior ao menor; empate na ordem do index.

    No período eleitoral, política, pesquisa e assunto eleitoral ficam fora; ``exclude``
    tira assuntos que viraram as matérias indicadas (``story_id``).
    """
    nomes, excluded = tuple(nomes), set(exclude)
    ranked = []
    for topic in edition.index:
        if topic.story_id and topic.story_id in excluded:
            continue
        if election and topic_is_political(edition, topic, nomes):
            continue
        ranked.append((topic, len(topic_sources(topic, veiculos=True))))
    return sorted(ranked, key=lambda tk: -tk[1])


def rank_assunto(edition: Edition, topic: IndexTopic) -> int:
    """Posição do assunto entre todos os do dia pelo número de veículos (1 = o mais coberto)."""
    k = len(topic_sources(topic, veiculos=True))
    return 1 + sum(1 for t in edition.index if len(topic_sources(t, veiculos=True)) > k)


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

    @property
    def linha(self) -> str:
        """``"1.159 assuntos · 60 com 3+ · 22 com 5+ · 1 com 10+"``."""
        return f"{num(self.assuntos)} assuntos · {num(self.com3)} com 3+ · {num(self.com5)} com 5+ · {num(self.com10)} com 10+"


def concentracao(edition: Edition, *, election: bool, nomes: Iterable[str] = NOMES_ELEICAO) -> Concentracao | None:
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
    ranked = assuntos_mais_cobertos(edition, election=election, nomes=nomes)
    if ranked and ranked[0][1] > 0:
        result.topo, result.topo_veiculos = ranked[0]
    return result


# ── Relógio da notícia ────────────────────────────────────────────────────


@dataclass
class Batida:
    source: str
    at: datetime
    stance: str | None = None  # "a" | "b" | "neutro" quando o veículo está na cobertura comparada
    lang: str | None = None  # idioma do texto (index.items[].lang)


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
    first: dict[str, tuple[datetime, str]] = {}
    for item in topic.items:
        at = _parse_dt(item.published)
        if at is None or not item.source or (veiculos and item.source in ORGAOS):
            continue
        if item.source not in first or at < first[item.source][0]:
            first[item.source] = (at, item.lang)
    if len(first) < 2:
        return None
    batidas = sorted((Batida(s, at, stance.get(s), lang) for s, (at, lang) in first.items()), key=lambda b: b.at)
    return Relogio(batidas)


def relogio_valido(
    r: Relogio | None,
    *,
    min_veiculos: int = RELOGIO_MIN_VEICULOS,
    min_span: timedelta = RELOGIO_MIN_SPAN,
) -> bool:
    """O relógio vale como slide: 4+ veículos com horário e 30 min ou mais do primeiro ao último."""
    return r is not None and len(r.batidas) >= min_veiculos and r.span >= min_span


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


def selo_leitura(qid: str, ponto: Ponto, ref: date | None = None) -> str:
    """De quando é a cotação: ``"fech. 20/10"`` (só fechamento de pregão), senão
    ``"leitura das 06h55"`` (``"leitura de 23/10, 17h06"`` se não é do dia ``ref``);
    ``""`` quando a leitura não serve de dado (:func:`stale_reading`: sem variação)."""
    if stale_reading(ponto):
        return ""
    local = ponto.as_of.astimezone(TZ)
    if is_close(qid, ponto.as_of):
        return f"fech. {local:%d/%m}"
    if ref is not None and local.date() != ref:
        return f"leitura de {local:%d/%m}, {local:%Hh%M}"
    return f"leitura das {local:%Hh%M}"


def ponto_da_cotacao(q: Quote) -> Ponto | None:
    at = _parse_dt(q.as_of)
    return Ponto(at, q.value, q.change_pct) if at is not None else None


def pregoes_faltantes(pontos: list[Ponto], qid: str) -> list[date]:
    """Dias úteis de pregão sem leitura entre a primeira e a última (só ibov, ifix e spx;
    feriados da bolsa não contam). No gráfico, viram buraco tracejado."""
    if qid not in FECHAMENTO_IDS or len(pontos) < 2:
        return []
    have = {p.day for p in pontos}
    feriados = FERIADOS.get(qid, set())
    out = []
    day = pontos[0].day + timedelta(days=1)
    while day < pontos[-1].day:
        if day.weekday() < 5 and day not in have and day not in feriados:
            out.append(day)
        day += timedelta(days=1)
    return out


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


def juro_real(edition: Edition) -> float | None:
    """Juro real ex-post, em %: ``(1 + Selic) / (1 + IPCA 12m) − 1`` com as taxas da edição."""
    by_id = {q.id: q for q in edition.quotes}
    selic, ipca = by_id.get("selic"), by_id.get("ipca12m")
    if selic is None or ipca is None:
        return None
    return ((1 + selic.value / 100) / (1 + ipca.value / 100) - 1) * 100


def valor_cotacao(qid: str, value: float) -> str:
    """O valor sem unidade, nas casas do ativo: ``"208.296"``, ``"110,14"``."""
    return num(value, CASAS.get(qid, 2))


# ── O Número do dia ───────────────────────────────────────────────────────


@dataclass
class Numero:
    valor: str  # o número em destaque: "12 veículos", "0,3%", "−4,42%", "+13,8%"
    tese: str  # a frase que diz de que é o número
    selo: str  # a prova: "no título de 5 de 5 veículos", "fech. 28/09 → fech. 20/10"...
    tipo: str  # "mercado" | "triangulado" | "concentracao" | "acumulado" | "juro_real"
    metodo: str  # como medimos, em uma linha
    section: str | None = None
    story_id: str | None = None
    chave: str = ""  # forma normalizada do número (para não repetir na semana)
    qualificador: str = ""  # "CERCA DE", "ATÉ"... (vai acima do número, em caixa menor)
    passo: int = 0  # o passo da escolha (1 a 7) que achou o número
    prova: dict[str, Any] = field(default_factory=dict)  # dados do gráfico do slide 2 (formato por tipo)


def _core(value: str) -> str:
    s = value.lower().replace(" ", "").replace(" ", "")
    s = re.sub(r"^(r\$|us\$|€)", "", s)
    s = re.sub(r"bilh(ão|ões)$", "bi", s)
    s = re.sub(r"milh(ão|ões)$", "mi", s)
    s = re.sub(r"trilh(ão|ões)$", "tri", s)
    return s


def _title_has(core: str, title: str) -> bool:
    t = title.lower().replace(" ", "").replace(" ", "")
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


def _ponto_json(qid: str, p: Ponto, ref: date | None = None) -> dict[str, Any]:
    return {
        "dia": f"{p.day:%d/%m}",
        "data": p.day.isoformat(),
        "as_of": p.as_of.isoformat(),
        "valor": p.value,
        "valor_fmt": valor_cotacao(qid, p.value),
        "pct": p.change_pct,
        "selo": selo_leitura(qid, p, ref),
    }


def _prova_triangulado(edition: Edition, topic: IndexTopic, core: str, valor: str) -> dict[str, Any]:
    day = date.fromisoformat(edition.date)
    rows: dict[str, dict[str, Any]] = {}
    for item in topic.items:
        if not item.source or item.source in ORGAOS:
            continue
        row = rows.setdefault(item.source, {"nome": item.source, "confirmado": False, "at": None, "hora": ""})
        row["confirmado"] = row["confirmado"] or _title_has(core, item.title or "")
        at = _parse_dt(item.published)
        if at is not None and (row["at"] is None or at < row["at"]):
            row["at"], row["hora"] = at, hora(at, day)
    ordered = sorted(rows.values(), key=lambda r: (r["at"] is None, r["at"] or datetime.min.replace(tzinfo=TZ)))
    for r in ordered:
        r["at"] = r["at"].isoformat() if r["at"] else None
    confirmados = sum(1 for r in ordered if r["confirmado"])
    return {
        "veiculos": ordered,
        "confirmados": confirmados,
        "total": len(ordered),
        "frase": f"{confirmados} de {len(ordered)} veículos puseram {valor} no título",
    }


def triangulados(edition: Edition, *, election: bool, nomes: Iterable[str] = NOMES_ELEICAO) -> list[Numero]:
    """Números da manchete e da linha fina que aparecem no título de 3+ veículos do assunto.

    Esporte fica fora; no período eleitoral, também política, pesquisa e matéria eleitoral.
    """
    nomes = tuple(nomes)
    out: list[tuple[tuple, Numero]] = []
    for story in edition.stories:
        text = f"{_plain(story.headline)} {_plain(story.dek)}"
        if story.section == "esporte":
            continue
        if election and (story.section == "politica" or is_poll(text) or story_is_electoral(story, nomes)):
            continue
        topic = topic_of(edition, story.id)
        if topic is None:
            continue
        sources = topic_sources(topic, veiculos=True)
        for m in _NUMERO.finditer(text):
            raw = m.group(0).strip()
            core = _core(raw)
            if any(n.story_id == story.id and n.chave == core for _, n in out):
                continue
            confirm = {i.source for i in topic.items if i.source in sources and _title_has(core, i.title)}
            if len(confirm) < TRIANGULADO_MIN:
                continue
            q = _QUALIFICADOR.search(text[: m.start()])
            valor = _display_number(raw)
            numero = Numero(
                valor=valor,
                tese=tese_da_materia(edition, story),
                selo=f"no título de {len(confirm)} de {len(sources)} veículos",
                tipo="triangulado",
                metodo=f"O mesmo número no título de {len(confirm)} veículos diferentes que publicaram o assunto.",
                section=story.section,
                story_id=story.id,
                chave=core,
                qualificador=q.group(1).upper() if q else "",
                passo=2,
                prova=_prova_triangulado(edition, topic, core, valor),
            )
            # Primeiro o número que a maioria dos veículos do assunto pôs no título.
            key = (2 * len(confirm) >= len(sources), story.section in SECOES_ECONOMIA, len(confirm), story.importance)
            out.append((key, numero))
    out.sort(key=lambda kv: kv[0], reverse=True)
    return [n for _, n in out]


def _mercado(edition: Edition, history: list[Edition], z_min: float, passo: int) -> Numero | None:
    best: tuple[float, Numero] | None = None
    labels = {q.id: q.label for q in edition.quotes}
    day = date.fromisoformat(edition.date)
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
        label = labels.get(qid, qid)
        verb = "sobe" if q.change_pct > 0 else "cai"
        when = selo_leitura(qid, p, day)
        sign = "+" if q.change_pct > 0 else "−"
        valor = f"{sign}{abs(q.change_pct):.2f}%".replace(".", ",")
        recent = [x for x in series if x.change_pct is not None and not stale_reading(x)][-29:]
        variacoes = [{**_ponto_json(qid, x, day), "hoje": False} for x in recent]
        variacoes.append({**_ponto_json(qid, p, day), "hoje": True})
        numero = Numero(
            valor=valor,
            tese=f"{label} {verb} {abs(q.change_pct):.2f}%".replace(".", ","),
            selo=f"{q.source or 'Yahoo Finance'} · {when}",
            tipo="mercado",
            metodo=f"Variação do dia {num(z, 1)} vezes a típica do ativo "
            f"(desvio robusto de {len(series)} leituras desde {series[0].as_of.astimezone(TZ):%d/%m}).",
            section="mercados",
            chave=f"{qid}:{valor}",
            passo=passo,
            prova={
                "qid": qid,
                "ativo": label,
                "variacoes": variacoes,
                "escala": scale,
                "hoje": q.change_pct,
                "z": z,
                "leitura": when,
                "fonte": q.source or "Yahoo Finance",
                "anotacao": f"hoje: {valor}, {num(z, 1)} vezes a variação típica",
            },
        )
        if best is None or z > best[0]:
            best = (z, numero)
    return best[1] if best else None


def _titulo_assunto(edition: Edition, topic: IndexTopic) -> str:
    """Título do assunto para a capa, sem adjetivo da casa e com até 14 palavras; nunca o
    título literal de um veículo ("" se o assunto não tem outro título)."""
    story = next((s for s in edition.stories if s.id == topic.story_id), None) if topic.story_id else None
    literal = _plain(topic.title) in {_plain(i.title) for i in topic.items}
    if story is not None and (literal or len(palavras(topic.title)) > TESE_MAX_PALAVRAS):
        return tese_da_materia(edition, story)
    if literal:
        return ""
    title = sem_adj_casa(topic.title, titulos_por_veiculo(topic), dek=story.dek if story else "")
    return _corta_palavras(title or _plain(topic.title), TESE_MAX_PALAVRAS)


def _concentracao_numero(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = NOMES_ELEICAO,
    exclude: Iterable[str] = (),
    usados: set[str] = frozenset(),
) -> Numero | None:
    conc = concentracao(edition, election=election, nomes=nomes)
    if conc is None:
        return None
    for topic, k in assuntos_mais_cobertos(edition, election=election, nomes=nomes, exclude=exclude):
        if k < CONCENTRACAO_MIN:
            return None
        chave = f"conc:{_fold(topic.title)[:40]}"
        tese = _titulo_assunto(edition, topic)
        if chave in usados or not tese:
            continue
        rank = rank_assunto(edition, topic)
        onde = "o assunto mais coberto" if rank == 1 else f"o {rank}º assunto mais coberto"
        textos = topic_texts(topic)
        return Numero(
            valor=f"{k} veículos",
            tese=tese,
            selo=f"{onde} entre {num(conc.assuntos)} do dia",
            tipo="concentracao",
            metodo=f"Veículos diferentes que publicaram o assunto; {fracao(conc.uma_fonte, conc.assuntos)} "
            "dos assuntos do dia saíram em uma fonte só.",
            section=topic.section,
            story_id=topic.story_id,
            chave=chave,
            passo=3,
            prova={
                "contagens": [len(topic_sources(t)) for t in edition.index],
                "destaque": edition.index.index(topic),
                "assuntos": conc.assuntos,
                "uma_fonte": conc.uma_fonte,
                "com3": conc.com3,
                "com5": conc.com5,
                "com10": conc.com10,
                "veiculos": k,
                "textos": textos,
                "rank": rank,
                "linha": conc.linha,
            },
        )
    return None


def _acumulados(edition: Edition, history: list[Edition], min_pontos: int) -> list[Numero]:
    """Acumulado ponta a ponta de cada ativo com série suficiente, do maior |%| ao menor."""
    labels = {q.id: q.label for q in edition.quotes}
    day = date.fromisoformat(edition.date)
    found: list[tuple[str, list[Ponto], Acumulado]] = []
    for qid in ACUMULADO_IDS:
        pontos = serie(history, qid)
        acc = acumulado(pontos)
        if acc is None or len(pontos) < min_pontos:
            continue
        found.append((qid, pontos, acc))
    out: list[Numero] = []
    for qid, pontos, acc in sorted(found, key=lambda f: -abs(f[2].pct)):
        label = labels.get(qid, qid)
        valor = sinal(acc.pct)
        desde = f"{acc.first.day:%d/%m}"
        ida, volta = selo_leitura(qid, acc.first) or f"{acc.first.day:%d/%m}", selo_leitura(qid, acc.last) or f"{acc.last.day:%d/%m}"
        out.append(
            Numero(
                valor=valor,
                tese=f"{label} acumula {valor} desde {desde}",
                selo=f"{ida} → {volta}",
                tipo="acumulado",
                metodo=f"Uma leitura por dia ({len(pontos)} leituras, Yahoo Finance), variação ponta a ponta "
                f"de {desde} a {acc.last.day:%d/%m}; não é soma de variações diárias.",
                section="mercados",
                chave=f"acum:{qid}",
                passo=5,
                prova={
                    "qid": qid,
                    "ativo": label,
                    "pontos": [_ponto_json(qid, p, day) for p in pontos],
                    "faltantes": [{"dia": f"{d:%d/%m}", "data": d.isoformat()} for d in pregoes_faltantes(pontos, qid)],
                    "primeiro": _ponto_json(qid, acc.first),
                    "ultimo": _ponto_json(qid, acc.last),
                    "max": _ponto_json(qid, acc.high),
                    "min": _ponto_json(qid, acc.low),
                    "pct": acc.pct,
                    "leituras": len(pontos),
                    "outros": [
                        {"qid": o, "ativo": labels.get(o, o), "pct": a.pct, "pct_fmt": sinal(a.pct)}
                        for o, _, a in found
                        if o != qid
                    ],
                },
            )
        )
    return out


def _juro_real_numero(edition: Edition) -> Numero | None:
    real = juro_real(edition)
    if real is None:
        return None
    by_id = {q.id: q for q in edition.quotes}
    selic, ipca = by_id["selic"], by_id["ipca12m"]
    ipca_ref = ipca.as_of or ""
    year, _, month = ipca_ref.partition("-")
    meses = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    ipca_mes = f"{meses[int(month) - 1]}/{year[2:]}" if month.isdigit() and 1 <= int(month) <= 12 else ipca_ref
    valor = f"{num(real, 2)}%"
    return Numero(
        valor=valor,
        tese=f"Juro real ex-post de {valor}",
        selo=f"Selic {num(selic.value, 2)}% · IPCA 12m {num(ipca.value, 2)}% ({ipca_mes})",
        tipo="juro_real",
        metodo=f"(1 + Selic) ÷ (1 + IPCA de 12 meses até {ipca_mes}) − 1, com as taxas do Banco Central.",
        section="mercados",
        chave="juroreal",
        passo=6,
        prova={
            "selic": selic.value,
            "ipca": ipca.value,
            "real": real,
            "selic_ref": selic.as_of or "",
            "ipca_ref": ipca_mes,
            "formula": f"(1 + {num(selic.value, 2)}%) ÷ (1 + {num(ipca.value, 2)}%) − 1 = {valor}",
            "fonte": selic.source or "Banco Central (SGS)",
        },
    )


def _usados_legado(past: list[Edition], day: date, *, election: bool, nomes: tuple[str, ...]) -> set[str]:
    """Sem histórico na fila: os números triangulados que teriam saído nos 7 dias anteriores."""
    return {
        n.chave
        for e in past
        if (day - date.fromisoformat(e.date)).days <= REPETICAO_DIAS and e.mode == "ai"
        for n in triangulados(e, election=election, nomes=nomes)[:1]
    }


def numero_do_dia(
    edition: Edition,
    history: list[Edition],
    *,
    election: bool,
    nomes: Iterable[str] = NOMES_ELEICAO,
    exclude: Iterable[str] = (),
    usados: Iterable[str] | None = None,
    min_pontos: int = ACUMULADO_MIN_PONTOS,
) -> Numero | None:
    """O Número do dia, pelo primeiro passo que tiver candidato (§4.4):

    1. mercado fora do comum (variação ≥ 3 desvios robustos do próprio ativo);
    2. número triangulado (no título de 3+ veículos), fora de ``exclude`` e de ``usados``;
    3. o assunto mais coberto fora de ``exclude``, se tiver 12+ veículos;
    4. mercado com variação ≥ 2 desvios robustos;
    5. o maior acumulado da série (10+ leituras), com chave ``acum:<ativo>`` fora de ``usados``;
    6. o juro real ex-post (chave ``juroreal``) fora de ``usados``;
    7. o maior acumulado, mesmo repetido: o 12:00 nunca fica vazio.

    Sem IA, só os passos 1, 4, 5, 6 e 7. ``usados`` são as chaves publicadas nos últimos
    7 dias; ``None`` (fila sem histórico) cai na regra antiga: o número triangulado de
    cada um dos 7 dias anteriores.
    """
    nomes = tuple(nomes)
    day = date.fromisoformat(edition.date)
    past = [e for e in history if date.fromisoformat(e.date) < day]
    upto = [e for e in history if date.fromisoformat(e.date) <= day]
    excluded = set(exclude)
    ja = set(usados) if usados is not None else set()
    forte = _mercado(edition, past, Z_FORTE, 1)
    if forte:
        return forte
    if edition.mode == "ai":
        repetidos = ja if usados is not None else _usados_legado(past, day, election=election, nomes=nomes)
        for n in triangulados(edition, election=election, nomes=nomes):
            if n.story_id not in excluded and n.chave not in repetidos:
                return n
        conc = _concentracao_numero(edition, election=election, nomes=nomes, exclude=excluded, usados=ja)
        if conc:
            return conc
    medio = _mercado(edition, past, Z_MEDIO, 4)
    if medio:
        return medio
    acumulados = _acumulados(edition, upto, min_pontos)
    novo = next((n for n in acumulados if n.chave not in ja), None)
    if novo:
        return novo
    real = _juro_real_numero(edition)
    if real and real.chave not in ja:
        return real
    if acumulados:
        acumulados[0].passo = 7
        return acumulados[0]
    if real:
        real.passo = 7
    return real


# ── Selo de cobertura (Em 1 minuto) ───────────────────────────────────────


def selo_cobertura(edition: Edition, story: Story, raiox_story_id: str | None = None, *, hora_raiox: str = "18h30") -> str:
    """``"12 veículos · dividida 6 × 2 · Raio-X às 18h30"``, ``"8 veículos · sem divisão"``.

    Conta os veículos do assunto no index (órgãos fora); sem assunto, as fontes da matéria.
    """
    topic = topic_of(edition, story.id)
    if topic is not None:
        k = len(topic_sources(topic, veiculos=True))
    else:
        k = len({s.name for s in story.sources if s.name})
    parts = [f"{k} veículo" if k == 1 else f"{k} veículos"]
    c = story.coverage
    if c is not None:
        a, b = c.count("a"), c.count("b")
        parts.append(f"dividida {max(a, b)} × {min(a, b)}" if a >= 1 and b >= 1 else "sem divisão")
    if raiox_story_id and story.id == raiox_story_id:
        parts.append(f"Raio-X às {hora_raiox}")
    return " · ".join(parts)
