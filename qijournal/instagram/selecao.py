"""Escolha das matérias de cada publicação a partir da edição do dia."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from qijournal.config import LimiaresConfig
from qijournal.models import Coverage, Edition, Story

from . import estatistica as E


def ordered_stories(edition: Edition) -> list[Story]:
    """Matérias na ordem editorial: manchete, chamadas, destaques e o resto por importância."""
    by_id = {s.id: s for s in edition.stories}
    order: list[str] = []
    for sid in [edition.lead, *edition.secondary, *edition.highlights]:
        if sid in by_id and sid not in order:
            order.append(sid)
    rest = sorted((s for s in edition.stories if s.id not in order), key=lambda s: -s.importance)
    return [by_id[sid] for sid in order] + rest


def diverse_stories(edition: Edition, n: int, *, exclude: Iterable[str] = ()) -> list[Story]:
    """Até ``n`` matérias na ordem editorial, no máximo uma por editoria enquanto der."""
    excluded = set(exclude)
    pool = [s for s in ordered_stories(edition) if s.id not in excluded]
    chosen: list[Story] = []
    sections: set[str] = set()
    for story in pool:
        if len(chosen) == n:
            break
        if story.section not in sections:
            chosen.append(story)
            sections.add(story.section)
    for story in pool:  # menos editorias que vagas: completa na ordem editorial
        if len(chosen) == n:
            break
        if story not in chosen:
            chosen.append(story)
    order = {s.id: i for i, s in enumerate(pool)}
    return sorted(chosen, key=lambda s: order[s.id])


def divergence_score(coverage: Coverage | None) -> float:
    """Quanto a cobertura se dividiu: 0 sem debate; maior com lados equilibrados, poucos
    neutros e mais veículos."""
    if coverage is None or not coverage.has_debate:
        return 0.0
    a, b = coverage.count("a"), coverage.count("b")
    if a == 0 or b == 0:
        return 0.0
    balance = min(a, b) / max(a, b)  # 1 = empate entre os lados
    engaged = (a + b) / len(coverage.outlets)  # fração que tomou um lado
    return balance * engaged * math.log2(1 + len(coverage.outlets))


def coverage_story(edition: Edition, *, exclude: Iterable[str] = (), min_outlets: int = 3) -> Story | None:
    """A matéria em que a imprensa mais se dividiu (com pelo menos ``min_outlets`` veículos)."""
    excluded = set(exclude)
    candidates = [
        s
        for s in ordered_stories(edition)
        if s.id not in excluded and s.coverage is not None and len(s.coverage.outlets) >= min_outlets
    ]
    scored = [(divergence_score(s.coverage), -i, s) for i, s in enumerate(candidates)]
    scored = [t for t in scored if t[0] > 0]
    if not scored:
        return None
    return max(scored, key=lambda t: (t[0], t[1]))[2]


# ── Rótulo de cada lado ───────────────────────────────────────────────────

# Verbos que abrem o lado ("Destaca a sinergia em IA..."): saem do rótulo. Os da spec e
# as variações que aparecem nos lados de 14 a 25/10 (plural, "enfoca", "destaque").
VERBOS_LADO = (
    "destaca", "enfatiza", "foca", "foco", "ressalta", "aponta", "prioriza", "sublinha", "salienta", "realça",
    "privilegia", "concentra-se", "explora", "aborda", "mostra", "evidencia", "registra", "trata", "valoriza",
    "reforça", "sinaliza", "alerta", "critica", "defende",
    "destacam", "enfatizam", "focam", "ressaltam", "apontam", "priorizam", "enfoca", "enfocam", "enfoque", "destaque",
    "condena", "celebra", "comemora", "enquadra", "vê", "lamenta", "apoia", "sustenta", "lembra", "noticia", "reproduz",
    "questiona", "contesta", "justifica", "sugere", "mantém", "frisa", "exalta", "enxerga", "direciona", "detalha",
    "denuncia", "culpabiliza", "cobra", "avalia", "atribui", "apresenta", "aposta", "alega", "adverte", "problematiza",
)
# Verbos que invertem o sentido do que vem depois ("Afasta interferência..."): sem rótulo.
VERBOS_INVERSOS = ("afasta", "minimiza", "nega", "rejeita", "descarta", "relativiza", "ignora")
BLOQUEIO_ROTULO = re.compile(r"\b(?:heroic\w*|heróic\w*|corajos\w*|dúbi\w*|supost\w*)", re.I)
ROTULO_MAX_PALAVRAS = 4

_ARTIGOS = {"a", "o", "as", "os", "um", "uma", "uns", "umas"}
_PREPOSICOES = {
    "de", "da", "do", "das", "dos", "em", "na", "no", "nas", "nos", "num", "numa", "para", "por", "pela", "pelo",
    "pelas", "pelos", "com", "sobre", "ao", "aos", "à", "às", "entre", "sob", "contra", "sem", "após", "até", "ante",
    "desde", "perante", "frente",
}
_VERBO_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(v) for v in sorted(VERBOS_LADO, key=len, reverse=True)) + r")(?![\w-])\s*", re.I
)
_INVERSO_RE = re.compile(r"^\s*(?:" + "|".join(VERBOS_INVERSOS) + r")(?![\w-])", re.I)
_CORTE_RE = re.compile(r",|;|:|\s\(|\se\s|\s[—–-]\s")
_ORACAO_RE = re.compile(r"que\s+", re.I)
_PREDICADO_RE = re.compile(r"\s(?:não|foi|foram|é|são|será|seria|era|eram|está|estão|tem|têm)\b", re.I)


def rotulo_lado(side: str, nomes: Iterable[str] = E.NOMES_ELEICAO) -> str | None:
    """Rótulo curto e determinístico de um lado da cobertura (§4.2).

    "Enfatiza a sinergia em IA e o salto de receita da SpaceX" → "sinergia em IA". Tira o
    verbo inicial e os artigos e preposições que vêm depois, corta no primeiro ",", ";",
    " e " ou " — ", fica com até 4 palavras (recuando até antes da última preposição se o
    corte quebrou a frase) e tira o que ficou pendurado no fim. ``None`` se sobrar vazio,
    se o verbo inverte o sentido ("afasta", "nega"...) ou se o rótulo tem adjetivo da
    casa, palavra da lista de bloqueio, partido ou político. Depois de "que" ("Alega que
    limites de sinal foram..."), fica o sujeito da oração.
    """
    text = E._plain(side).strip().rstrip(".")
    if not text or _INVERSO_RE.match(text):
        return None
    text = _VERBO_RE.sub("", text, count=1)
    oracao = _ORACAO_RE.match(text)
    if oracao:  # "Alega que limites de sinal foram manobra...": fica o sujeito da oração
        text = text[oracao.end():]
        predicado = _PREDICADO_RE.search(text)
        text = text[: predicado.start()] if predicado else text
    words = text.split()
    while words and words[0].lower() in _ARTIGOS | _PREPOSICOES:
        words.pop(0)
    text = " ".join(words)
    cut = _CORTE_RE.search(text)
    if cut:
        text = text[: cut.start()]
    words = text.split()
    if len(words) > ROTULO_MAX_PALAVRAS:
        words = words[:ROTULO_MAX_PALAVRAS]
        last = max((i for i, w in enumerate(words) if w.lower() in _PREPOSICOES), default=0)
        if last > 0:
            words = words[:last]
    while words and words[-1].lower() in _ARTIGOS | _PREPOSICOES:
        words.pop()
    label = " ".join(words).strip(" .;:,")
    if not label:
        return None
    if E.ADJ_CASA.search(label) or BLOQUEIO_ROTULO.search(label) or E.cita_partido(label) or E.cita_nome(label, nomes):
        return None
    return label


@dataclass
class Lados:
    """Maioria e minoria da cobertura (pela contagem; no empate, A é a maioria e B a minoria)."""

    maioria: str  # "a" | "b"
    minoria: str
    n_maioria: int
    n_minoria: int
    neutros: int
    total: int  # veículos analisados (len(outlets))
    rot_maioria: str | None
    rot_minoria: str | None

    @property
    def empate(self) -> bool:
        return self.n_maioria == self.n_minoria


def lados(cov: Coverage, nomes: Iterable[str] = E.NOMES_ELEICAO) -> Lados:
    a, b = cov.count("a"), cov.count("b")
    rot = {"a": rotulo_lado(cov.side_a, nomes), "b": rotulo_lado(cov.side_b, nomes)}
    maj, mi = ("a", "b") if a >= b else ("b", "a")
    counts = {"a": a, "b": b}
    return Lados(maj, mi, counts[maj], counts[mi], len(cov.outlets) - a - b, len(cov.outlets), rot[maj], rot[mi])


# ── Gancho da capa do Raio-X ──────────────────────────────────────────────

GANCHO_MAX = 90


@dataclass
class Gancho:
    formula: str  # "G2" | "G3" | "G0" | "compacto" | "sem_rotulo" (vai para Post.formula)
    texto: str  # em caixa alta, até 90 caracteres
    destaque: str  # o trecho em laranja (caixa alta, substring de ``texto``), ou ""
    legenda: str  # a mesma frase em caixa normal: 1ª linha da legenda
    lados: Lados


def gancho_raiox(cov: Coverage, nomes: Iterable[str] = E.NOMES_ELEICAO) -> Gancho:
    """O gancho da capa pelos moldes do §4.2, o primeiro que cabe em 90 caracteres.

    G2 (minoria, n ≥ 5 e min ≤ n/4), G3 (empate; "metade" só quando todos tomaram lado),
    G0 (os demais), compacto (o escolhido passou de 90) e sem rótulo (algum rótulo é None).
    Em laranja vai só o rótulo da minoria (no G3, o do lado B).
    """
    ld = lados(cov, nomes)
    n, maj, mi = ld.total, ld.n_maioria, ld.n_minoria
    if ld.rot_maioria is None or ld.rot_minoria is None:
        texto = f"{n} veículos, mesmo fato: {maj} contaram de um jeito, {mi} de outro."
        return Gancho("sem_rotulo", texto.upper(), "", texto, ld)
    rmaj, rmin = ld.rot_maioria, ld.rot_minoria
    if n >= 5 and mi <= n / 4:
        formula, texto = "G2", f"{n} veículos, mesmo fato. {maj} destacaram {rmaj}. Só {mi}, {rmin}."
        legenda = f"{n} veículos, mesmo fato: {maj} destacaram {rmaj}; só {mi}, {rmin}."
    elif ld.empate and maj + mi == n:
        formula, texto = "G3", f"Mesmo fato, {n} veículos: metade destacou {rmaj}; metade, {rmin}."
        legenda = texto
    else:
        formula, texto = "G0", f"{n} veículos, mesmo fato: {maj} destacaram {rmaj}; {mi}, {rmin}."
        legenda = texto
    if len(texto) > GANCHO_MAX:
        formula, texto = "compacto", f"{maj} de {n} destacaram {rmaj}; {mi}, {rmin}."
        legenda = texto
    if len(texto) > GANCHO_MAX:
        texto = f"{n} veículos, mesmo fato: {maj} contaram de um jeito, {mi} de outro."
        return Gancho("sem_rotulo", texto.upper(), "", texto, ld)
    return Gancho(formula, texto.upper(), rmin.upper(), legenda[0].upper() + legenda[1:], ld)


# ── Raio-X: elegibilidade, nota e escolha ─────────────────────────────────

PESO_SECAO = {
    "brasil": 3, "mercados": 3,
    "tecnologia": 2, "imobiliario": 2,
    "juridico": 1, "natureza": 1,
    "mundo": 0, "politica": 0,
    "esporte": -2, "variedades": -2,
}
_ENTIDADE = re.compile(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕ][\wÀ-ú]{3,}")
ENTIDADES_FORA = {"Governo", "Brasil"}


def entidades(story: Story) -> list[str]:
    """Palavras com inicial maiúscula (4+ letras) da manchete e da linha fina, sem Governo e Brasil."""
    text = f"{E._plain(story.headline)} {E._plain(story.dek)}"
    return list(dict.fromkeys(w for w in _ENTIDADE.findall(text) if w not in ENTIDADES_FORA))


def repete(story: Story, entidades_recentes: Iterable[Iterable[str]]) -> bool:
    """A matéria tem 2+ entidades em comum com o Raio-X de algum dos dias anteriores?"""
    mine = set(entidades(story))
    return any(len(mine & set(prev)) >= 2 for prev in entidades_recentes)


def raiox_elegivel(
    story: Story,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    limiares: LimiaresConfig | None = None,
) -> bool:
    """Matéria com cobertura dividida: 4+ veículos, 2+ de cada lado, 4+ enquadramentos, os
    dois lados descritos; no período eleitoral, nada político nem eleitoral."""
    lim = limiares or LimiaresConfig()
    c = story.coverage
    if c is None or not c.side_a.strip() or not c.side_b.strip():
        return False
    if len(c.outlets) < lim.raiox_min_veiculos:
        return False
    if c.count("a") < lim.raiox_min_por_lado or c.count("b") < lim.raiox_min_por_lado:
        return False
    if sum(1 for o in c.outlets if o.framing.strip()) < lim.raiox_min_framings:
        return False
    if election and (E.is_political_coverage(c, story, nomes) or E.story_is_electoral(story, nomes)):
        return False
    return True


def nota_raiox(story: Story, entidades_recentes: Iterable[Iterable[str]] = ()) -> float:
    """``2×importância + 4×min(a, b) + 0,5×(a + b) + 2×peso da seção − 8×repetição``."""
    c = story.coverage
    assert c is not None
    a, b = c.count("a"), c.count("b")
    rep = 1 if repete(story, entidades_recentes) else 0
    return 2 * story.importance + 4 * min(a, b) + 0.5 * (a + b) + 2 * PESO_SECAO.get(story.section, 0) - 8 * rep


def candidatos_raiox(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    entidades_recentes: Iterable[Iterable[str]] = (),
    limiares: LimiaresConfig | None = None,
    exclude: Iterable[str] = (),
) -> list[tuple[float, Story]]:
    """As matérias elegíveis com a nota, da maior para a menor (empate: ordem editorial)."""
    nomes, recentes, excluded = tuple(nomes), [list(e) for e in entidades_recentes], set(exclude)
    out = []
    for i, story in enumerate(ordered_stories(edition)):
        if story.id in excluded or not raiox_elegivel(story, election=election, nomes=nomes, limiares=limiares):
            continue
        out.append((nota_raiox(story, recentes), -i, story))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [(nota, story) for nota, _, story in out]


def escolher_raiox(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    entidades_recentes: Iterable[Iterable[str]] = (),
    limiares: LimiaresConfig | None = None,
    exclude: Iterable[str] = (),
) -> Story | None:
    """A matéria do Raio-X: a elegível de maior nota (``None``: o dia vira Placar do dia)."""
    if edition.mode != "ai":
        return None
    ranked = candidatos_raiox(
        edition, election=election, nomes=nomes, entidades_recentes=entidades_recentes, limiares=limiares, exclude=exclude
    )
    return ranked[0][1] if ranked else None


@dataclass
class Noite:
    tipo: str  # "raiox" | "placar" | "mercado"
    story: Story | None
    placar: E.Placar


def escolher_noite(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    entidades_recentes: Iterable[Iterable[str]] = (),
    limiares: LimiaresConfig | None = None,
) -> Noite:
    """O post das 18:30: Raio-X; sem matéria elegível, Placar do dia (base ≥ 10); senão,
    ou sem IA, Mercado em linha. Lint ou render reprovado também levam ao Mercado (no gerar)."""
    lim = limiares or LimiaresConfig()
    p = E.placar(edition, election=election, nomes=nomes)
    if edition.mode != "ai":
        return Noite("mercado", None, p)
    story = escolher_raiox(edition, election=election, nomes=nomes, entidades_recentes=entidades_recentes, limiares=lim)
    if story is not None:
        return Noite("raiox", story, p)
    return Noite("placar" if p.base >= lim.placar_min_base else "mercado", None, p)


# ── Relógio: a virada do slide 5 e o substituto "A atenção" ───────────────


@dataclass
class Virada:
    tipo: str  # "stance" (os 2 primeiros do mesmo lado) | "idioma" (inglês saiu antes)
    texto: str  # a frase pronta
    stance: str | None = None
    rotulo: str | None = None
    primeiro_en: E.Batida | None = None
    primeiro_pt: E.Batida | None = None


def virada(
    edition: Edition,
    story: Story,
    relogio: E.Relogio | None = None,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
) -> Virada | None:
    """A linha de virada do relógio (§4.2), só fato e nunca causa:

    1. os 2 primeiros horários com a mesma stance a ou b (e rótulo): "Os 2 primeiros a publicar destacaram {rótulo}.";
    2. há textos em inglês e em português e o primeiro é em inglês: os dois primeiros com a hora;
    3. nenhuma das duas: ``None``.
    """
    r = relogio or E.relogio(edition, story)
    if r is None or len(r.batidas) < 2:
        return None
    first, second = r.batidas[:2]
    c = story.coverage
    if c is not None and first.stance in ("a", "b") and first.stance == second.stance:
        rot = rotulo_lado(c.side_a if first.stance == "a" else c.side_b, nomes)
        if rot:
            return Virada("stance", f"Os 2 primeiros a publicar destacaram {rot}.", stance=first.stance, rotulo=rot)
    day = date.fromisoformat(edition.date)
    en = next((b for b in r.batidas if b.lang == "en"), None)
    pt = next((b for b in r.batidas if b.lang == "pt"), None)
    if en is not None and pt is not None and first is en:
        texto = (
            f"Primeiro texto em inglês: {en.source}, {E.hora(en.at, day)}. "
            f"Primeiro em português: {pt.source}, {E.hora(pt.at, day)}."
        )
        return Virada("idioma", texto, primeiro_en=en, primeiro_pt=pt)
    return None


@dataclass
class Atencao:
    """Dados do slide "A atenção" (substituto do relógio)."""

    veiculos: int
    textos: int
    rank: int  # posição do assunto pelo número de veículos (1 = o mais coberto)
    assuntos: int
    uma_fonte: int
    textos_en: int

    @property
    def linhas(self) -> list[str]:
        out = [
            f"{self.veiculos} VEÍCULOS, {self.textos} TEXTOS",
            f"{self.rank}º entre {E.num(self.assuntos)} assuntos do dia; {E.num(self.uma_fonte)} saíram em uma fonte só",
        ]
        if self.textos_en:
            out.append(f"{self.textos_en} dos {self.textos} textos em inglês")
        return out


def atencao(edition: Edition, story: Story) -> Atencao | None:
    topic = E.topic_of(edition, story.id)
    conc = E.concentracao(edition, election=False)
    if topic is None or conc is None:
        return None
    items = [i for i in topic.items if i.source and i.source not in E.ORGAOS]
    return Atencao(
        veiculos=len(E.topic_sources(topic, veiculos=True)),
        textos=len(items),
        rank=E.rank_assunto(edition, topic),
        assuntos=conc.assuntos,
        uma_fonte=conc.uma_fonte,
        textos_en=sum(1 for i in items if i.lang == "en"),
    )
