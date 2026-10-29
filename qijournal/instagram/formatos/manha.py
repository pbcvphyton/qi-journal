"""Em 1 minuto 2.0 (07:00, §4.5): carrossel de 8 em marinho.

1. Capa: o número da manchete em laranja (com o qualificador), a manchete sem adjetivo
   da casa e sem linha fina, a faixa de dados do dia e o assunto mais coberto.
2–6. Os 5 fatos: número em destaque, a frase em serif, o selo de cobertura e as fontes.
   O 1º é a matéria da capa, nunca com o texto da capa (Jaccard ≥ 0,6 → linha fina), e
   ganha a linha do relógio quando ele vale.
7. Mercado em linha (:mod:`.mercado`), no tema marinho.
8. Fechamento: a pauta solitária do dia, a matriz de pontos e o único CTA.

Todo texto sai do JSON da edição (briefing, manchetes, linhas finas, index, cotações);
nada de IA nova.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from qijournal.config import LimiaresConfig
from qijournal.models import Edition, IndexTopic, Story

from .. import estatistica as E
from .. import graficos as G
from ..conteudo import (
    DIAS,
    Peca,
    Slide,
    edition_day,
    finish_caption,
    hashtags,
    is_electoral,
    is_poll,
    kicker_date,
    match_story,
    num,
    plain,
    section_info,
    short_date,
    source_names,
    sources_line,
)
from ..redacao import LEGENDA_MAX, LINHA1_MAX, palavras_proibidas, similaridade, verbos_proibidos
from ..selecao import entidades, ordered_stories
from . import mercado as M

SLOT = "manha"
SERIE = "em1min"
HASHTAG = "#noticias"
CTA = "Mande no grupo de quem precisa começar o dia informado."
FATOS = 5
FATOS_MIN = 3  # com menos, o 07:00 vira o Terminal da manhã
MANCHETE_MAX = 90
FRASE_MAX = 230
JACCARD_CAPA = 0.6  # o slide 2 com texto parecido com a capa troca pela linha fina
MAIS_COBERTO_MIN = 5
VESPERA_CADEIA = 3  # dias recalculados para saber o que a véspera publicou
NUM_CAPA = 200
NUM_FATO_MAX, NUM_FATO_MIN = 220, 180
NOTA = "resumo por IA a partir das fontes citadas"

# Ordem de preferência das editorias (§4.5); esporte e variedades só se faltar fato.
PREFERENCIA = (
    "brasil", "mercados", "tecnologia", "imobiliario", "mundo", "juridico", "natureza", "politica", "esporte", "variedades",
)
SO_SE_FALTAR = {"esporte", "variedades"}

# Ativos do slide de mercado: o fato que cita um deles com a mesma variação sai.
ATIVO_RE = {
    "ibov": re.compile(r"\bIbov(?:espa)?\b", re.I),
    "usd": re.compile(r"\bd[óo]lar(?:es)?\b", re.I),
    "brent": re.compile(r"\bBrent\b|\bpetr[óo]leo\b", re.I),
    "btc": re.compile(r"\bbitcoins?\b|\bBTC\b", re.I),
}
_PCT = re.compile(r"(\d+(?:,\d+)?)\s?%")
_NUMEROS = re.compile(r"\d+(?:[.,]\d+)*")
_ENTIDADE = re.compile(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕ][\wÀ-ú]{3,}")
_ROTULO = re.compile(r"^\s*\*\*([^*\n]{1,60}?):?\*\*\s*:?\s+(?=\S)")
# Além do is_electoral: a eleição dita com todas as letras ("Eleição presidencial deixa
# fatura fiscal pesada para o vencedor", 25/10, em Brasil), fora de Mundo.
_ELEICAO = re.compile(
    r"\belei[çc](?:ão|ões)\b|\beleit(?:or|ora|ores|oras|oral|orais|o|a|os|as)\b|\bpresidencial\b|\breelei[çc](?:ão|ões)\b",
    re.I,
)

# Adjetivos da casa que saem sozinhos depois do substantivo ("fusão histórica após" →
# "fusão após"); "gigante" e "recorde" costumam ser substantivo e só saem com o corte.
_ADJ_SOLTOS = ("historic", "inedit", "bilionari", "polemic", "dramatic", "explosiv", "chocante", "colossa")
_ANTES_NAO = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "na", "no", "nas", "nos",
    "com", "por", "pela", "pelo", "após", "sobre", "para", "à", "ao", "é", "são", "foi", "foram", "será", "seria",
    "era", "está", "estão", "fica", "ficou", "e", "ou",
}
_PENDURADAS = E._PENDURADAS | {"que", "se"}
# Onde uma frase pode ser cortada sem perder o sentido: primeiro as junções de oração, depois as preposições.
_JUNCOES = ((", ", "; ", " e ", " após ", " enquanto ", " mas ", " em meio a "), (" com ", " para ", " em "))
_LIGA = {"e", "de", "da", "do", "das", "dos"}
_DA = {5: "do", 6: "do"}  # "e mais 4 fatos do sábado"; nos dias úteis, "da"
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


@dataclass
class Fato:
    texto: str  # a frase do slide, sem markdown, terminada em ponto
    story: Story | None  # a matéria (None: item do briefing sem casamento; sai sem selo)
    origem: str  # "briefing" | "manchete" | "dek"
    briefing: str = ""  # o item do briefing casado com a matéria


@dataclass
class Selecao:
    capa: Story
    manchete: str  # a manchete da capa, sem adjetivo da casa e com até 90 caracteres
    fatos: list[Fato]  # o 1º é a matéria da capa


# ── Texto ─────────────────────────────────────────────────────────────────


def _sem_rotulo(item: str, story: Story | None) -> str:
    """O item do briefing sem o rótulo em negrito do começo ("**Escala 6x1**: Senado abre…"), a não
    ser que o rótulo traga um nome que a frase não repete ("**Petrobras**: Estatal bate…",
    "**Caso Americanas**: Justiça torna réus… da varejista"): aí ele fica, como "Petrobras: …"."""
    m = _ROTULO.match(item or "")
    if m is None or not item[m.end():].lstrip().startswith(tuple("**")) and ":" not in item[: m.end()]:
        return item
    rotulo, resto = m.group(1).strip(), item[m.end():]
    textos = [story.headline, story.dek, *story.body[:1]] if story is not None else []
    proprios = _proprios(textos)
    words = rotulo.split()
    nomes = [
        w for i, w in enumerate(words)
        if w[:1].isupper() and (i > 0 or w in proprios or any(c.isupper() for c in w[1:]))
    ]
    falta = [w for w in nomes if w.lower() not in plain(resto).lower()]
    return f"{rotulo}: {resto}" if falta else resto


def _frase(texto: str) -> str:
    """Item do briefing ou campo da matéria como frase de slide: sem markdown, sem espaços
    repetidos e com maiúscula no começo (menos em nome como "iFood")."""
    texto = " ".join(plain(texto or "").split())
    if not texto:
        return ""
    primeira = texto.split(" ", 1)[0]
    return texto if any(c.isupper() for c in primeira[1:]) else texto[:1].upper() + texto[1:]


def _ponto(texto: str) -> str:
    texto = texto.rstrip(" .;,:")
    return f"{texto}." if texto and texto[-1] not in "!?…" else texto


def _sem_ponto(texto: str) -> str:
    return texto.rstrip(" .")


def _maiuscula(palavra: str) -> bool:
    core = palavra.strip(".,;:!?()“”\"'‘’")
    return bool(core) and core[0].isupper()


def _entre_nomes(texto: str, pos: int, juncao: str) -> bool:
    """A junção liga dois nomes próprios ("Tesla e SpaceX", "China, México e Chile")?"""
    if juncao not in (", ", " e "):
        return False
    antes = texto[:pos].split()
    depois = texto[pos + len(juncao):].split()
    return bool(antes and depois) and _maiuscula(antes[-1]) and _maiuscula(depois[0])


def _corte(texto: str, fim: int, *, minimo: int = 5, juncoes: tuple[tuple[str, ...], ...] = _JUNCOES) -> str:
    """O texto até a última junção antes de ``fim`` que deixe ``minimo`` palavras ou mais, não
    separe dois nomes próprios nem deixe preposição pendurada; "" se não houver."""
    for grupo in juncoes:
        posicoes = sorted(
            ((m.start(), j) for j in grupo for m in re.finditer(re.escape(j), texto[:fim])),
            reverse=True,
        )
        for pos, j in posicoes:
            head = texto[:pos].rstrip(" ,;:—–-")
            words = head.split()
            if len(words) < minimo:
                break
            if _entre_nomes(texto, pos, j) or words[-1].lower() in _PENDURADAS:
                continue
            return head
    return ""


def _cabe(texto: str, limite: int) -> str:
    """O texto com até ``limite`` caracteres, cortado numa junção ("" se não der)."""
    return texto if len(texto) <= limite else _corte(texto, limite + 1)


def _sem_adj(texto: str, titulos: list[str], *, solta: bool = True, juncoes=_JUNCOES) -> tuple[str, bool]:
    """Sem adjetivo da casa que não esteja no título de metade dos veículos (§4.1): sai a
    palavra sozinha quando é adjetivo depois do substantivo (``solta``); senão, a frase é
    cortada na junção anterior. Devolve o texto ("" se não der) e se houve corte."""
    cortou = False
    while True:
        bad = next(
            (m for m in E.ADJ_CASA.finditer(texto) if not E._adj_nos_titulos(E._raiz_adj(m.group(0)), titulos)),
            None,
        )
        if bad is None:
            return texto, cortou
        antes = texto[: bad.start()].split()
        no_fim = not texto[bad.end():].strip(" .;:!?")
        corte = _corte(texto, bad.start(), juncoes=juncoes) if no_fim else ""
        if corte:  # "… com investimento bilionário" → corta antes de "com", não deixa "com investimento"
            texto, cortou = corte, True
            continue
        if solta and E._raiz_adj(bad.group(0)) in _ADJ_SOLTOS and antes and antes[-1].lower() not in _ANTES_NAO:
            texto = re.sub(r"\s+([,;:.])", r"\1", f"{texto[: bad.start()].rstrip()} {texto[bad.end():].lstrip()}").strip()
            continue
        texto, cortou = _corte(texto, bad.start(), juncoes=juncoes), True
        if not texto:
            return "", True


def _sem_proibidas(texto: str) -> tuple[str, bool]:
    """Sem palavra proibida do lint ("em meio a", "dispara"...): a frase é cortada antes dela.
    Devolve o texto ("" se não der) e se houve corte."""
    cortou = False
    while True:
        achadas = palavras_proibidas(texto)
        if not achadas:
            return texto, cortou
        pos = texto.lower().find(achadas[0])
        cortou = True
        if achadas[0] == "em meio a" and len(texto[:pos].split()) >= 5:
            texto = texto[:pos].rstrip(" ,;:")
            continue
        texto = _corte(texto, pos)
        if not texto:
            return "", True


def _limpo(texto: str, titulos: list[str], limite: int = FRASE_MAX) -> tuple[str, bool]:
    """O texto pronto para o slide (sem adjetivo da casa, palavra proibida nem verbo que julga
    veículo, até ``limite`` caracteres) e se o corte foi pesado (sobrou menos de 8 palavras e
    de 60% da frase); ``("", False)`` se não houver versão limpa."""
    original = _frase(texto)
    t, _ = _sem_adj(original, titulos)
    t, _ = _sem_proibidas(t) if t else ("", False)
    if not t or verbos_proibidos(t):
        return "", False
    curto = _cabe(t, limite)
    n, total = len(curto.split()), len(original.split())
    return curto, bool(curto) and n < 8 and n < 0.6 * total


def _manchete_capa(edition: Edition, story: Story) -> str:
    """A manchete da capa (§4.1 e §4.5): sem adjetivo da casa (corte no último " e " ou ","
    com 5 palavras ou mais, nunca entre dois nomes; senão, a linha fina), sem palavra proibida
    e com até 90 caracteres."""
    titulos = E.titulos_por_veiculo(E.topic_of(edition, story.id))
    so_e = ((", ", " e "),)
    texto, _ = _sem_adj(_frase(story.headline), titulos, solta=False, juncoes=so_e)
    if not texto:
        texto, _ = _sem_adj(_frase(story.dek), titulos, solta=False, juncoes=so_e)
    texto, _ = _sem_proibidas(texto) if texto else ("", False)
    if not texto or verbos_proibidos(texto):
        return ""
    return _sem_ponto(_cabe(texto, MANCHETE_MAX))


# ── Número em destaque ────────────────────────────────────────────────────


def _extenso(valor: str) -> str:
    """Sem moeda, a unidade por extenso ("3 mi" → "3 milhões"); com moeda, fica ("R$ 1 bi")."""
    m = re.fullmatch(r"(\d[\d.,]*) (mi|bi|tri)", valor)
    if not m:
        return valor
    um = m.group(1).split(",")[0] == "1"
    nome = {"mi": ("milhão", "milhões"), "bi": ("bilhão", "bilhões"), "tri": ("trilhão", "trilhões")}[m.group(2)]
    return f"{m.group(1)} {nome[0] if um else nome[1]}"


def numero_fato(texto: str, evitar: Iterable[str] = ()) -> tuple[str, str] | None:
    """O número em destaque do texto: ``(qualificador, número)``, como :func:`.estatistica.numero_destaque`,
    pulando os números de ``evitar`` (o slide 2 não repete o número da capa)."""
    t = plain(texto)
    fora = {E._core(x) for x in evitar if x}
    for m in E._NUMERO.finditer(t):
        raw = m.group(0).strip()
        if E._ANO.fullmatch(raw):
            continue
        valor = E._display_number(raw)
        if E._core(valor) in fora:
            continue
        q = E._QUALIFICADOR.search(t[: m.start()])
        return (q.group(1).upper() if q else "", _extenso(valor))
    return None


def tamanho_numero(valor: str, *, maximo: int = NUM_FATO_MAX, minimo: int = NUM_FATO_MIN) -> int:
    """O maior px (até ``maximo``) em que o número cabe numa linha da área útil."""
    w = G.largura_texto(valor.upper(), "hook", 100) / 100
    return int(max(minimo, min(maximo, (G.LARGURA - 8) / w if w else maximo)))


def _numeros(texto: str) -> set[str]:
    return set(_NUMEROS.findall(plain(texto)))


# ── Seleção ───────────────────────────────────────────────────────────────


def _rank(section: str | None) -> int:
    return PREFERENCIA.index(section) if section in PREFERENCIA else len(PREFERENCIA) - 2


def _titulos(edition: Edition, story: Story | None) -> list[str]:
    return E.titulos_por_veiculo(E.topic_of(edition, story.id)) if story is not None else []


def _entidades(fato_story: Story | None, texto: str) -> set[str]:
    if fato_story is not None:
        return set(entidades(fato_story))
    return {w for w in _ENTIDADE.findall(texto) if w not in {"Governo", "Brasil"}}


def _marca(f: Fato) -> tuple[set[str], set[str]]:
    """(entidades, números) de um fato publicado, para a regra da véspera."""
    s = f.story
    return _entidades(s, f.texto), _numeros(" ".join([f.texto, s.headline, s.dek] if s else [f.texto]))


def _repete_vespera(story: Story | None, textos: list[str], vespera: list[tuple[set[str], set[str]]]) -> bool:
    """2+ entidades em comum com a capa ou um fato da véspera e nenhum número novo (§4.5)."""
    if not vespera:
        return False
    ents = _entidades(story, " ".join(textos))
    nums = _numeros(" ".join(textos))
    return any(len(ents & e) >= 2 and not (nums - n) for e, n in vespera)


def _repete_mercado(textos: Iterable[str], paineis: list[dict[str, Any]]) -> bool:
    """O fato é do slide de mercado (o número de mercado vem sempre de ``quotes``): cita Ibovespa,
    dólar, Brent ou bitcoin com a variação que está no slide, ou abre com o ativo e um número
    ("Petróleo supera US$ 116 o barril", quando a leitura do Brent é US$ 111,70)."""
    for texto in textos:
        texto = plain(texto)
        for p in paineis:
            rx = ATIVO_RE.get(p["id"])
            m_ativo = rx.search(texto) if rx is not None else None
            if m_ativo is None:
                continue
            if len(texto[: m_ativo.start()].split()) <= 3 and (_PCT.search(texto) or E._NUMERO.search(texto)):
                return True
            valores = [abs(v) for v in (p.get("variacao"), p.get("pct")) if v is not None]
            for m in _PCT.finditer(texto):
                casas = len(m.group(1).partition(",")[2])
                x = float(m.group(1).replace(",", "."))
                if any(abs(round(v, casas) - x) < 1e-9 for v in valores):
                    return True
    return False


def _eleitoral(story: Story | None, textos: list[str], nomes: tuple[str, ...]) -> bool:
    """No período eleitoral: pesquisa, :func:`.estatistica.is_electoral` ou a eleição com todas as letras."""
    junto = " ".join(textos)
    if is_poll(junto) or is_electoral(story, junto, nomes):
        return True
    section = story.section if story is not None else None
    return section != "mundo" and bool(_ELEICAO.search(" ".join([junto, story.headline, story.dek] if story else [junto])))


def _texto(edition: Edition, story: Story | None, briefing: str, evitar: str = "") -> Fato | None:
    """O texto do fato: o item do briefing; senão a manchete; senão a linha fina. Vale a primeira
    versão limpa sem corte pesado; sem ela, a primeira com corte. Com ``evitar`` (a manchete da
    capa), pula o que tiver Jaccard ≥ 0,6 com ela."""
    titulos = _titulos(edition, story)
    candidatos = [("briefing", briefing)]
    if story is not None:
        candidatos += [("manchete", story.headline), ("dek", story.dek)]
    limpos = []
    for origem, bruto in candidatos:
        t, pesado = _limpo(bruto, titulos) if bruto else ("", False)
        if t and not (evitar and similaridade(t, evitar) >= JACCARD_CAPA):
            limpos.append((pesado, origem, t))
    if not limpos:
        return None
    _, origem, t = min(limpos, key=lambda c: c[0])
    return Fato(_ponto(t), story, origem, briefing)


def _briefing(edition: Edition) -> tuple[dict[str, str], list[str]]:
    """Itens do briefing por matéria (o 1º de cada) e os que não casaram com nenhuma."""
    por_story: dict[str, str] = {}
    orfaos: list[str] = []
    for b in edition.briefing:
        if not plain(b):
            continue
        st = match_story(edition, b)
        if st is None:
            orfaos.append(_sem_rotulo(b, None))
        else:
            por_story.setdefault(st.id, _sem_rotulo(b, st))
    return por_story, orfaos


def selecionar(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    evitar_capa: Iterable[str] = (),
    fora: Iterable[str] = (),
    paineis: list[dict[str, Any]] | None = None,
    vespera: list[tuple[set[str], set[str]]] | None = None,
) -> Selecao | None:
    """A capa e os 5 fatos do dia (§4.5).

    - Capa: a manchete (no período eleitoral, a 1ª não política), fora de ``evitar_capa``
      (as matérias do Raio-X e do Número: os três posts do dia nunca têm a mesma peça principal).
      ``fora`` (a matéria do Número) não entra nem como fato: o número dela é a peça das 12:00.
    - Fatos: o 1º é a matéria da capa; depois os itens do briefing e, para completar, as
      matérias, pela ordem de editoria (brasil > mercados > tecnologia > …); o item do briefing
      sem matéria (sem selo nem fontes) só depois delas; esporte e variedades só se faltar fato.
    - Fora: assunto eleitoral no período eleitoral (e só 1 de política, sempre o último); fato que
      cita Ibovespa, dólar, Brent ou bitcoin com a variação do slide de mercado (``paineis``); fato
      com 2+ entidades em comum com a véspera (``vespera``) e sem número novo; o mesmo assunto duas vezes.

    None se nenhuma matéria serve de capa.
    """
    nomes = tuple(nomes)
    paineis = paineis or []
    vespera = vespera or []
    fora = {x for x in fora if x}
    evitar = {x for x in evitar_capa if x} | fora
    por_story, orfaos = _briefing(edition)
    ordem = [s for s in ordered_stories(edition) if s.id not in fora]

    def bloqueado(story: Story | None, textos: list[str]) -> bool:
        if election and _eleitoral(story, textos, nomes):
            return True
        campos = [t for t in textos if t] + ([story.headline, story.dek] if story is not None else [])
        return _repete_mercado(campos, paineis) or _repete_vespera(story, campos, vespera)

    capa: Story | None = None
    manchete = ""
    primeiro: Fato | None = None
    lead = edition.lead
    for passada in (0, 1):
        for story in ordem:
            if story.id in evitar or (passada == 0 and story.section in SO_SE_FALTAR and story.id != lead):
                continue
            if election and story.section == "politica":
                continue
            b = por_story.get(story.id, "")
            if bloqueado(story, [b]):
                continue
            m = _manchete_capa(edition, story)
            f = _texto(edition, story, b, evitar=m) if m else None
            if f is not None:
                capa, manchete, primeiro = story, m, f
                break
        if capa is not None:
            break
    if capa is None or primeiro is None:
        return None

    candidatos: list[tuple[int, int, int, Story | None, str]] = []
    for i, story in enumerate(ordem):
        if story.id == capa.id:
            continue
        b = por_story.get(story.id, "")
        tier = 2 if story.section in SO_SE_FALTAR else (0 if b else 1)
        candidatos.append((tier, _rank(story.section), i, story, b))
    for j, b in enumerate(orfaos):  # sem matéria, o fato sai sem selo nem fontes: só depois das matérias
        candidatos.append((1, len(PREFERENCIA), len(ordem) + j, None, b))
    candidatos.sort(key=lambda c: c[:3])

    escolhidos: list[Fato] = []
    ents_dia = [_entidades(capa, primeiro.texto)]
    politica = 0
    for _, _, _, story, b in candidatos:
        if len(escolhidos) == FATOS - 1:
            break
        if bloqueado(story, [b]):
            continue
        is_pol = story is not None and story.section == "politica"
        if election and is_pol and politica:
            continue
        f = _texto(edition, story, b)
        if f is None:
            continue
        ents = _entidades(story, f.texto)
        if any(len(ents & e) >= 2 for e in ents_dia):
            continue
        politica += is_pol
        ents_dia.append(ents)
        escolhidos.append(f)

    def chave(f: Fato) -> tuple[int, int]:
        sec = f.story.section if f.story is not None else None
        return (1 if election and sec == "politica" else 0, _rank(sec))

    return Selecao(capa, manchete, [primeiro, *sorted(escolhidos, key=chave)])


def vespera(
    edition: Edition,
    history: list[Edition],
    ontem: Edition | None = None,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    min_pontos: int = E.ACUMULADO_MIN_PONTOS,
) -> list[tuple[set[str], set[str]]]:
    """(entidades, números) da capa e dos fatos do Em 1 minuto da véspera.

    ``ontem`` é a edição da véspera (None: procura em ``history``). A véspera é recalculada
    com a mesma seleção, que também filtrou pela véspera dela (até :data:`VESPERA_CADEIA` dias);
    as matérias que o Raio-X e o Número daqueles dias tiraram da capa não são conhecidas aqui.
    """
    day = edition_day(edition)
    por_dia = {e.date: e for e in history}
    if ontem is None:
        ontem = por_dia.get((day - timedelta(days=1)).isoformat())
    if ontem is None or ontem.mode != "ai":
        return []
    cadeia = [ontem]
    d = date.fromisoformat(ontem.date)
    for _ in range(VESPERA_CADEIA - 1):
        d -= timedelta(days=1)
        e = por_dia.get(d.isoformat())
        if e is None or e.mode != "ai":
            break
        cadeia.append(e)
    marcas: list[tuple[set[str], set[str]]] = []
    for e in reversed(cadeia):
        ate = [h for h in history if h.date <= e.date] or [e]
        slide = M.mercado_slide(e, ate, tema="dark", min_pontos=min_pontos)
        sel = selecionar(
            e, election=election, nomes=nomes, paineis=slide.ctx["paineis"] if slide else [], vespera=marcas
        )
        marcas = [_marca(f) for f in sel.fatos] if sel else []
    return marcas


# ── Capa: faixa de dados e o mais coberto ────────────────────────────────


def _proprios(textos: Iterable[str]) -> set[str]:
    """Palavras que aparecem com inicial maiúscula fora do começo de frase em algum dos textos."""
    out: set[str] = set()
    for t in textos:
        words = plain(t or "").split()
        for i, w in enumerate(words):
            core = w.strip(".,;:!?()“”\"'‘’")
            if core and i > 0 and not words[i - 1].endswith((".", ":", "?", "!")) and core[0].isupper():
                out.add(core)
    return out


def _corrida(words: list[str], proprios: set[str]) -> list[str]:
    """A sequência de nomes próprios (com "e", "de", "da"... entre eles) do começo de ``words``."""
    run: list[str] = []
    for w in words:
        core = w.strip(".,;:!?()“”\"'‘’")
        if core and (core in proprios or any(c.isupper() for c in core[1:])):
            run.append(core)
            if w.endswith((",", ";", ":")):
                break
            continue
        if run and core.lower() in _LIGA:
            run.append(core)
            continue
        break
    while run and run[-1].lower() in _LIGA:
        run.pop()
    return run


def nome_do_assunto(edition: Edition, topic: IndexTopic) -> str:
    """Como o assunto aparece na capa: os nomes que abrem o título, se forem 2 ou mais
    ("SpaceX e Tesla"); senão o título do assunto sem adjetivo da casa e com até 14 palavras
    (nunca o título literal de um veículo); sem os dois, a 1ª sequência de 2+ nomes do título."""
    story = next((s for s in edition.stories if s.id == topic.story_id), None) if topic.story_id else None
    titulo = _frase(story.headline if story else topic.title)
    literais = {_frase(i.title) for i in topic.items}
    textos = [i.title for i in topic.items] + ([story.headline, story.dek, *story.body[:1]] if story else [])
    proprios = _proprios(textos)
    words = titulo.split()
    abre = _corrida(words, proprios)
    if sum(1 for w in abre if w.lower() not in _LIGA) >= 2:
        return " ".join(abre)
    if story is not None and titulo not in literais:
        limpo, _ = _limpo(titulo, E.titulos_por_veiculo(topic), limite=MANCHETE_MAX)
        if limpo:
            return _sem_ponto(limpo)
    tese = E._titulo_assunto(edition, topic)
    if tese:
        return _sem_ponto(tese)
    for i in range(1, len(words)):
        run = _corrida(words[i:], proprios)
        if sum(1 for w in run if w.lower() not in _LIGA) >= 2:
            return " ".join(run)
    return ""


def _mais_coberto(
    edition: Edition, capa: Story, *, election: bool, nomes: tuple[str, ...], paineis: list[dict[str, Any]] = ()
) -> dict[str, Any] | None:
    """O assunto com mais veículos (k ≥ 5). Se for o da capa, só a contagem; se for de mercado,
    só o nome do ativo ("Petróleo Brent"): o número de mercado vem do slide 7, nunca do título."""
    conc = E.concentracao(edition, election=election, nomes=nomes)
    if conc is None or conc.topo is None or conc.topo_veiculos < MAIS_COBERTO_MIN:
        return None
    proprio = conc.topo.story_id == capa.id
    nome = "" if proprio else nome_do_assunto(edition, conc.topo)
    if nome and _repete_mercado([nome], list(paineis)):
        fins = [m.end() for rx in ATIVO_RE.values() for m in rx.finditer(nome) if len(nome[: m.start()].split()) <= 3]
        nome = nome[: max(fins)] if fins else ""
    if not proprio and not nome:
        return None
    return {"nome": nome, "k": conc.topo_veiculos, "proprio": proprio}


def _faixa(edition: Edition) -> str:
    """``"1.640 notícias · 46 fontes · 1.159 assuntos"`` (o que houver)."""
    partes = []
    if edition.stats and edition.stats.articles_collected:
        partes.append(f"{num(edition.stats.articles_collected)} notícias")
    if edition.rationale and edition.rationale.outlets:
        partes.append(f"{num(edition.rationale.outlets)} fontes")
    if edition.index:
        partes.append(f"{num(len(edition.index))} assuntos")
    return " · ".join(partes)


def tamanho_manchete(manchete: str, com_numero: bool) -> int:
    """Noto Serif 800 de 64 a 80 px (até 96 sem número), pelo comprimento."""
    n = len(manchete)
    if not com_numero:
        return 96 if n <= 50 else 88 if n <= 75 else 80
    return 80 if n <= 45 else 76 if n <= 60 else 72 if n <= 75 else 64


def tamanho_frase(frase: str, com_numero: bool) -> int:
    """Noto Serif de 52 px abaixo do número (menos, se a frase é longa); sem número, a frase
    vira o destaque do slide: de 60 px para cima, mais quanto mais curta."""
    n = len(frase)
    if com_numero:
        return 52 if n <= 140 else 48 if n <= 180 else 44
    return 76 if n <= 90 else 68 if n <= 140 else 60 if n <= 190 else 56


# ── Slides ────────────────────────────────────────────────────────────────


def selo_serie(numero_serie: int, day: date | None = None) -> str:
    """``"EM 1 MINUTO Nº 031 · QUA 21 OUT"``; sem número de série, sem o "Nº"."""
    base = f"EM 1 MINUTO Nº {numero_serie:03d}" if numero_serie else "EM 1 MINUTO"
    return f"{base} · {kicker_date(day)}" if day else base


def _nome_serie(numero_serie: int) -> str:
    return f"Em 1 minuto nº {numero_serie}" if numero_serie else "Em 1 minuto"


def linha_relogio(
    edition: Edition, story: Story, limiares: LimiaresConfig | None = None, mostrar: int = 3
) -> tuple[list[dict[str, str]], int]:
    """As 3 primeiras batidas do relógio, quando ele vale (``Folha de S.Paulo 20/10 15h57 · g1 16h00 ·
    NeoFeed 16h35 +5``): ``([{nome, hora}], quantos faltam)``. A data só aparece quando muda."""
    lim = limiares or LimiaresConfig()
    r = E.relogio(edition, story)
    if not E.relogio_valido(r, min_veiculos=lim.relogio_min_veiculos, min_span_min=lim.relogio_min_span_min):
        return [], 0
    assert r is not None
    anterior = edition_day(edition)
    out = []
    for b in r.batidas[:mostrar]:
        local = b.at.astimezone(E.TZ)
        hora = local.strftime("%Hh%M")
        if local.date() != anterior:
            hora = f"{local:%d/%m} {hora}"
        anterior = local.date()
        out.append({"nome": b.source, "hora": hora})
    return out, len(r.batidas) - len(out)


def _cobertura(edition: Edition, story: Story | None, raiox_story_id: str | None) -> dict[str, Any]:
    if story is None:
        return {"cobertura": "", "cob_a": 0, "cob_n": 0, "cob_b": 0}
    c = story.coverage
    a, b = (c.count("a"), c.count("b")) if c else (0, 0)
    n = len(c.outlets) - a - b if c else 0
    return {"cobertura": E.selo_cobertura(edition, story, raiox_story_id), "cob_a": a, "cob_n": n, "cob_b": b}


def _slide_fato(
    edition: Edition,
    f: Fato,
    i: int,
    total: int,
    selo: str,
    *,
    raiox_story_id: str | None,
    evitar_numero: Iterable[str] = (),
    relogio: tuple[list[dict[str, str]], int] = ([], 0),
) -> Slide:
    story = f.story
    numero = numero_fato(f.texto, evitar_numero)
    qual, valor = numero if numero else ("", "")
    nomes = source_names(story) if story else []
    cob = _cobertura(edition, story, raiox_story_id)
    batidas, mais = relogio
    section = section_info(edition, story.section) if story else None
    ctx = {
        "selo": selo,
        "contador": True,
        "editoria": section["title"] if section else "",
        "qual": qual,
        "valor": valor,
        "tam": tamanho_numero(valor) if valor else 0,
        "frase": f.texto,
        "tam_frase": tamanho_frase(f.texto, bool(valor)),
        "solo": not valor,
        **cob,
        "fontes": sources_line(nomes),
        "relogio": batidas,
        "relogio_mais": mais,
        "destaque": valor,
        "section": story.section if story else "",
        "nota": NOTA,
    }
    partes = [f"Fato {i} de {total}: {f.texto}"]
    if cob["cobertura"]:
        partes.append(f"{cob['cobertura']}.")
    if batidas:
        lista = ", ".join(f"{b['nome']} {b['hora']}" for b in batidas)
        partes.append(f"Relógio: {lista}" + (f" e mais {mais}" if mais else "") + "; horário informado por cada veículo, em Brasília.")
    if nomes:
        partes.append(f"Fontes: {', '.join(nomes)}.")
    return Slide("m1_fato", ctx, alt=" ".join(partes))


def _colunas(total: int, largura: float, altura: float) -> int:
    """Colunas da matriz para os pontos ocuparem a área (largura × altura) por inteiro."""
    passo = (largura * max(altura, 1.0) / max(total, 1)) ** 0.5
    return max(10, int(-(-largura // passo)))


def _slide_fechamento(edition: Edition, selo: str, *, election: bool, nomes: tuple[str, ...]) -> Slide | None:
    conc = E.concentracao(edition, election=election, nomes=nomes)
    if conc is None or not conc.assuntos:
        return None
    contagens = [len(E.topic_sources(t)) for t in edition.index]
    pct = f" ({conc.uma_fonte_pct}%)" if conc.assuntos >= E.PCT_MIN else ""
    frase = f"de {num(conc.assuntos)} assuntos de hoje{pct} saíram em uma fonte só."
    n_stories = len(edition.stories)
    edicao = f"Os {n_stories} que mais importam estão na edição: link na bio."
    altura = 300
    largura = G.LARGURA - 266
    svg, descricao = G.matriz_pontos(contagens, None, colunas=_colunas(len(contagens), largura, altura - 56), altura_max=altura)
    ctx = {
        "selo": selo,
        "contador": True,
        "kicker": "Pauta solitária",
        "valor": num(conc.uma_fonte),
        "tam": tamanho_numero(num(conc.uma_fonte), maximo=240, minimo=160),
        "frase": frase,
        "grafico": svg,
        "edicao": edicao,
        "cta": CTA,
        "destaque": num(conc.uma_fonte),
        "section": "",
        "nota": f"fonte: índice da edição de {short_date(edition_day(edition))}",
    }
    alt = f"Pauta solitária: {num(conc.uma_fonte)} {frase} {descricao} {edicao} {CTA}"
    return Slide("m1_fechamento", ctx, alt=alt)


def _linha_mercado(paineis: list[dict[str, Any]]) -> list[str]:
    """Cada ativo do slide de mercado numa frase curta: "Ibovespa 208.296 pts, fech. 20/10, +13,8% desde 28/09"."""
    out = []
    for p in paineis:
        valor = p["valor"] + (" pts" if p["id"] in M.SUFIXO else "")
        partes = [x for x in (p["selo"], p["acumulado"]) if x]
        out.append(f"{p['nome']} {valor}" + (f", {', '.join(partes)}" if partes else ""))
    return out


def _legenda(
    edition: Edition,
    sel: Selecao,
    selos: list[str],
    paineis: list[dict[str, Any]],
    *,
    election: bool,
) -> str:
    day = edition_day(edition)
    dia = DIAS[day.weekday()]
    resto = len(sel.fatos) - 1
    linha1 = f"{sel.manchete} · e mais {resto} fatos {_DA.get(day.weekday(), 'da')} {dia}"
    if len(linha1) > LINHA1_MAX:
        linha1 = sel.manchete if len(sel.manchete) <= LINHA1_MAX else _corte(sel.manchete, LINHA1_MAX + 1) or sel.manchete[:LINHA1_MAX]
    artigos = edition.stats.articles_collected if edition.stats else 0
    fontes = edition.rationale.outlets if edition.rationale else 0
    metodo = f"Resumo por IA a partir de {num(artigos)} notícias de {num(fontes)} fontes."
    sections = [f.story.section for f in sel.fatos if f.story is not None]
    tags = hashtags(HASHTAG, sections, election=election)
    mercado = _linha_mercado(paineis)

    def monta(n_mercado: int, corte: int | None) -> str:
        linhas = []
        for f, selo in zip(sel.fatos, selos):
            texto = _sem_ponto(f.texto)
            if corte and len(texto) > corte:
                texto = _corte(texto, corte) or texto
            linhas.append(f"▸ {texto}" + (f" ({selo})" if selo else ""))
        if mercado[:n_mercado]:
            linhas.append("Mercado: " + " · ".join(mercado[:n_mercado]) + ".")
        return finish_caption([linha1, "\n".join([*linhas, metodo, CTA, tags])])

    for n_mercado, corte in ((len(mercado), None), (1, None), (1, 140), (0, 110)):
        caption = monta(n_mercado, corte)
        if len(caption) <= LEGENDA_MAX:
            return caption
    return caption


# ── A peça ────────────────────────────────────────────────────────────────


def em_1_minuto(
    edition: Edition,
    brand: dict[str, Any],
    history: list[Edition],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    numero_serie: int = 0,
    raiox_story_id: str | None = None,
    ontem: Edition | None = None,
    numero: Peca | None = None,
    limiares: LimiaresConfig | None = None,
) -> Peca | None:
    """O carrossel das 07:00: capa, 5 fatos, Mercado em linha e fechamento (8 slides).

    - ``history``: :func:`.estatistica.load_history` até o dia (inclusive), para o mercado e a véspera.
    - ``raiox_story_id``: a matéria do Raio-X da noite (fato com o selo "Raio-X às 18h30"; não vira capa).
    - ``ontem``: a edição da véspera (None: procura em ``history``), para não repetir assunto sem número novo.
    - ``numero``: a :class:`~.conteudo.Peca` do Número do dia: a matéria dele não vira capa e, se o
      Número for o acumulado de um ativo (``acum:ibov``), esse ativo não vira o título do mercado.

    None sem IA, sem briefing nem matérias, ou com menos de 3 fatos (o 07:00 vira o Terminal).
    """
    if edition.mode != "ai" or not edition.stories:
        return None
    nomes = tuple(nomes)
    lim = limiares or LimiaresConfig()
    day = edition_day(edition)
    ate = [h for h in history if h.date <= edition.date] or [edition]
    evitar_capa = {raiox_story_id} | set(numero.story_ids if numero else [])
    chave = numero.chave if numero else ""
    mercado = M.mercado_slide(
        edition, ate, tema="dark", min_pontos=lim.grafico_min_pontos,
        evitar={chave[5:]} if chave.startswith("acum:") else (),
    )
    paineis = mercado.ctx["paineis"] if mercado else []
    marcas = vespera(edition, history, ontem, election=election, nomes=nomes, min_pontos=lim.grafico_min_pontos)
    sel = selecionar(
        edition, election=election, nomes=nomes, evitar_capa=evitar_capa, fora=numero.story_ids if numero else (),
        paineis=paineis, vespera=marcas,
    )
    if sel is None or len(sel.fatos) < FATOS_MIN:
        return None
    capa = sel.capa
    selo_capa, selo = selo_serie(numero_serie, day), selo_serie(numero_serie)
    total = len(sel.fatos)
    numero_capa = E.numero_destaque(f"{capa.headline} {capa.dek}")
    qual, valor = numero_capa if numero_capa else ("", "")
    mc = _mais_coberto(edition, capa, election=election, nomes=nomes, paineis=paineis)
    faixa = _faixa(edition)
    chamada = f"{total} fatos + mercado" if mercado else f"{total} fatos"

    alt_capa = [f"{_nome_serie(numero_serie)}, {day.day} de {MESES[day.month - 1]}."]
    if valor:
        alt_capa.append(f"{(qual.capitalize() + ' ') if qual else ''}{valor}.")
    alt_capa.append(f"{sel.manchete}.")
    if faixa:
        alt_capa.append(f"{faixa.replace(' · ', ', ')}.")
    if mc:
        alt_capa.append(
            f"É o assunto mais coberto do dia: {mc['k']} veículos." if mc["proprio"]
            else f"O mais coberto: {mc['nome']}, {mc['k']} veículos."
        )
    slides = [
        Slide(
            "m1_capa",
            {
                "selo": selo_capa,
                "qual": qual,
                "valor": valor,
                "tam": tamanho_numero(valor, maximo=NUM_CAPA, minimo=150) if valor else 0,
                "manchete": sel.manchete,
                "tam_manchete": tamanho_manchete(sel.manchete, bool(valor)),
                "faixa": faixa,
                "mais_coberto": mc,
                "chamada": chamada,
                "destaque": valor,
                "section": capa.section,
                "nota": NOTA,
            },
            alt=" ".join(alt_capa),
        )
    ]
    selos = []
    for i, f in enumerate(sel.fatos, 1):
        primeiro = i == 1
        slide = _slide_fato(
            edition,
            f,
            i,
            total,
            selo,
            raiox_story_id=raiox_story_id,
            evitar_numero=[valor] if primeiro else (),
            relogio=linha_relogio(edition, capa, lim) if primeiro else ([], 0),
        )
        selos.append(slide.ctx["cobertura"])
        slides.append(slide)
    if mercado is not None:
        mercado.ctx.update(selo=selo, contador=True)
        mercado.alt = mercado.alt.replace(f"Mercado em linha, {short_date(day)}: ", "Mercado: ", 1)
        slides.append(mercado)
    fechamento = _slide_fechamento(edition, selo, election=election, nomes=nomes)
    if fechamento is not None:
        slides.append(fechamento)
    story_ids = [f.story.id for f in sel.fatos if f.story is not None]
    return Peca(
        slot=SLOT,
        kind="carousel",
        title="Em 1 minuto",
        slides=slides,
        caption=_legenda(edition, sel, selos, paineis, election=election),
        story_ids=list(dict.fromkeys(story_ids)),
        serie=SERIE,
        numero_serie=numero_serie,
    )
