"""Em 1 minuto 2.0 (07:00, §5.4): carrossel de 8 em marinho.

1. Capa: o número da manchete em laranja (com o qualificador), a manchete sem adjetivo
   da casa e sem linha fina, a faixa de dados do dia e o assunto mais coberto.
2–6. Os 5 fatos: número em destaque, a frase em serif, o selo de cobertura e as fontes.
   Fato sem número leva a cobertura no lugar dele ("NOTICIADO POR / 12 VEÍCULOS", com 5+
   veículos); sem os dois, a frase sozinha ocupa o quadro. O 1º é a matéria da capa, nunca com o texto da
   capa (Jaccard ≥ 0,6 → linha fina), e ganha a linha do relógio quando ele vale.
7. Mercado em linha (:mod:`.mercado`), no tema marinho.
8. Fechamento: a pauta solitária do dia, a matriz de pontos e o único CTA.

Todo texto sai do JSON da edição (briefing, manchetes, linhas finas, index, cotações),
de contas e de moldes; nada de IA nova.
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
MESMO_ASSUNTO = 0.5  # dois itens com Jaccard ≥ 0,5 são o mesmo assunto
MAIS_COBERTO_MIN = 5
COBERTURA_MIN = 5  # fato sem número: a cobertura vira o número com 5+ veículos (a faixa "com 5+" da matriz)
VESPERA_CADEIA = 3  # dias recalculados para saber o que a véspera publicou
NOTA = "resumo por IA a partir das fontes citadas"
NOTICIADO = "NOTICIADO POR"

# Medidas do quadro (px), para escolher os tamanhos que enchem o slide sem transbordar.
QUADRO = 1122  # altura útil (1350 − 116 da faixa − 112 da assinatura)
NUM_CAPA_MAX, NUM_CAPA_MIN = 280, 160
NUM_FATO_MAX, NUM_FATO_MIN = 260, 180
FRASE_TAMS = (68, 64, 60, 56, 52, 48, 44)  # frase sob o número
SOLO_TAMS = (96, 88, 80, 76, 72, 68, 64, 60)  # frase sozinha
MANCHETE_TAMS = (80, 76, 72, 68, 64)  # Noto Serif 800 com número (§5.4)
MANCHETE_SOLO_TAMS = (96, 88, 80, 76, 72)
SERIF, SERIF_800 = 1.08, 1.12  # a Noto Serif 600/800 mede isso vezes a Archivo 600 da tabela de graficos
OCUPACAO = 0.88  # o bloco do meio ocupa até 88% da área (o resto é respiro e a margem do balance)

# Ordem de preferência das editorias (§5.4); esporte e variedades só se faltar fato.
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
# Na capa, no período eleitoral, nem eleição de outro país ("Israel vai às urnas", 27/10).
_URNAS = re.compile(r"\burnas?\b|\bplebiscito\b|\breferendo\b", re.I)

# A ressalva junto do número (§4): as de estatistica e mais "ao menos", "pelo menos"...
_QUALIFICADOR = re.compile(
    r"\b(cerca de|mais de|menos de|até|quase|aproximadamente|em torno de|ao menos|pelo menos|no mínimo|no máximo)\s*$",
    re.I,
)
# Contagem sem unidade de E._NUMERO ("68 pessoas", "2.400 detidos", "70 mil policiais"), só com
# estes substantivos: número de nome ("Emenda 141", "737 Max 10") e duração ("20 anos") ficam de fora.
_CONTAVEIS = (
    r"pessoas?|mort[oa]s|ferid[oa]s|pres[oa]s|detid[oa]s|vítimas|desabrigad[oa]s|desalojad[oa]s|manifestantes|"
    r"policiais|militares|soldados|bombeiros|trabalhadores|empregos|vagas|usuários|clientes|consumidores|famílias|"
    r"crianças|alunos|estudantes|pacientes|casos|empresas|municípios|cidades|países|veículos|carros|imóveis|casas|"
    r"moradias|voos|passageiros|lojas|funcionários|servidores|(?:ex-)?executivos|palestinos|israelenses|toneladas|"
    r"hectares|quilômetros|litros|barris|doses|leitos|shoppings|agências|fábricas|ppm"
)
_CONTAGEM = re.compile(
    r"(?<![\w.,/-])(\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?)(\s(?:mil|milhão|milhões))?\s(" + _CONTAVEIS + r")\b"
    r"|(?<![\w.,/-])(\d+(?:,\d+)?)()\s?°C"  # temperatura: "2,8°C"
)

# Verbo de sensacionalismo antes de um número vira o verbo neutro ("desabam até 10%" → "caem até 10%").
_NEUTRO = {
    "desaba": "cai", "desabam": "caem", "desabou": "caiu", "desabaram": "caíram",
    "despenca": "cai", "despencam": "caem", "despencou": "caiu", "despencaram": "caíram",
    "derrete": "cai", "derretem": "caem", "derreteu": "caiu", "derreteram": "caíram",
    "dispara": "sobe", "disparam": "sobem", "disparou": "subiu", "dispararam": "subiram",
}
_NEUTRO_RE = re.compile(
    r"\b(" + "|".join(_NEUTRO) + r")(?=\s(?:(?:até|mais de|quase|cerca de)\s)?(?:R\$|US\$|€)?\s?\d)",
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
# Palavras que não dizem o assunto (para comparar dois itens do mesmo dia).
_VAZIAS = set(
    "a o as os um uma uns umas de da do das dos em na no nas nos para por pela pelo pelas pelos com que sem apos ate "
    "mais menos entre sobre ao aos seu sua seus suas e ou se nao diz contra como mas ja ser foi sao tem".split()
)
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


def _adj_sobrando(texto: str, titulos: list[str]) -> bool:
    """Ainda há adjetivo da casa que não está no título de metade dos veículos?"""
    return any(not E._adj_nos_titulos(E._raiz_adj(m.group(0)), titulos) for m in E.ADJ_CASA.finditer(texto))


def _sem_adj(texto: str, titulos: list[str], *, juncoes=_JUNCOES) -> tuple[str, bool]:
    """Sem adjetivo da casa que não esteja no título de metade dos veículos (§4): sai a palavra
    sozinha quando é adjetivo depois do substantivo ("fusão histórica após"); senão, a frase é
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
        if E._raiz_adj(bad.group(0)) in _ADJ_SOLTOS and antes and antes[-1].lower() not in _ANTES_NAO:
            texto = re.sub(r"\s+([,;:.])", r"\1", f"{texto[: bad.start()].rstrip()} {texto[bad.end():].lstrip()}").strip()
            continue
        texto, cortou = _corte(texto, bad.start(), juncoes=juncoes), True
        if not texto:
            return "", True


def neutro(texto: str) -> str:
    """O verbo de sensacionalismo seguido de número vira o neutro, no mesmo tempo e número
    ("Ações da Alphabet desabam até 10%" → "caem até 10%"); o resto fica para o corte."""

    def troca(m: re.Match) -> str:
        novo = _NEUTRO[m.group(1).lower()]
        return novo.capitalize() if m.group(1)[0].isupper() else novo

    return _NEUTRO_RE.sub(troca, texto)


def _sem_proibidas(texto: str) -> tuple[str, bool]:
    """Sem palavra proibida do lint ("em meio a", "dispara"...): antes de número, o verbo vira o
    neutro; senão a frase é cortada antes dela. Devolve o texto ("" se não der) e se houve corte."""
    texto = neutro(texto)
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
    """A manchete da capa (§4 e §5.4): :func:`.estatistica.capa_sem_adj` (corte no último " e "
    ou "," com 5+ palavras; senão a linha fina), sem palavra proibida nem verbo que julga veículo
    e com até 90 caracteres; "" se nada disso sobrar."""
    titulos = E.titulos_por_veiculo(E.topic_of(edition, story.id))
    texto = _frase(E.capa_sem_adj(edition, story))
    if _adj_sobrando(texto, titulos):  # a linha fina de reserva também tinha adjetivo
        texto = ""
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


def _qualificador(texto: str, pos: int) -> str:
    q = _QUALIFICADOR.search(texto[:pos])
    return q.group(1).upper() if q else ""


def numero_fato(
    texto: str, evitar: Iterable[str] = (), *, contagem: bool = True, minimo_px: int = NUM_FATO_MIN
) -> tuple[str, str] | None:
    """O número em destaque do texto: ``(qualificador, número)``.

    Primeiro o número com unidade, como :func:`.estatistica.numero_destaque` ("R$ 1 bi", "0,32%");
    sem ele (e com ``contagem``), a contagem de gente ou coisa ("68 pessoas", "2.400 presos") ou a
    temperatura ("2,8°C").
    Pula os números de ``evitar`` (o slide 2 não repete o da capa). O qualificador inclui "ao
    menos" e "pelo menos": a ressalva nunca se perde. A contagem que não cabe numa linha com
    ``minimo_px`` perde o substantivo ("1,8 mil"), que está na frase logo abaixo.
    """
    t = plain(texto)
    fora = {E._core(x) for x in evitar if x}
    fora_digitos = {_digitos(x) for x in evitar if x}
    for m in E._NUMERO.finditer(t):
        raw = m.group(0).strip()
        if E._ANO.fullmatch(raw):
            continue
        valor = E._display_number(raw)
        if E._core(valor) in fora:
            continue
        return _qualificador(t, m.start()), _extenso(valor)
    if not contagem:
        return None
    for m in _CONTAGEM.finditer(t):
        if E._ANO.fullmatch(m.group(1) or "") or _digitos(m.group(0)) in fora_digitos:
            continue
        valor = m.group(0)
        if m.group(1) and px_cabe(valor) < minimo_px:  # "1,8 mil manifestantes" não cabe: fica "1,8 mil"
            valor = m.group(1) + (m.group(2) or "")
        return _qualificador(t, m.start()), valor
    return None


def _digitos(valor: str) -> str:
    """Os algarismos do primeiro número ("2.400 presos" → "2400"), para comparar contagens."""
    m = re.search(r"\d[\d.,]*", valor)
    return re.sub(r"\D", "", m.group(0)) if m else ""


def numero_capa(texto: str) -> tuple[str, str] | None:
    """O número da capa: :func:`.estatistica.numero_destaque`, com a unidade por extenso sem moeda
    ("7 milhões") e a ressalva "ao menos"/"pelo menos" que ele não conhece; sem número com
    unidade, a contagem ("2.400 presos")."""
    n = E.numero_destaque(texto)
    if n is None:
        return numero_fato(texto, minimo_px=NUM_CAPA_MIN)
    qual, valor = n
    if not qual:
        t = plain(texto)
        m = next((m for m in E._NUMERO.finditer(t) if not E._ANO.fullmatch(m.group(0).strip())), None)
        qual = _qualificador(t, m.start()) if m else ""
    return qual, _extenso(valor)


def px_cabe(valor: str) -> float:
    """O maior px em que o número (Archivo condensada, caixa alta) cabe numa linha da área útil."""
    w = G.largura_texto(valor.upper(), "hook", 100) / 100
    return (G.LARGURA - 8) / w if w else float("inf")


def tamanho_numero(valor: str, *, maximo: int = NUM_FATO_MAX, minimo: int = NUM_FATO_MIN) -> int:
    """O maior px (até ``maximo``, nunca abaixo de ``minimo``) em que o número cabe numa linha da área útil."""
    return int(max(minimo, min(maximo, px_cabe(valor))))


def _numeros(texto: str) -> set[str]:
    return set(_NUMEROS.findall(plain(texto)))


# ── Medidas ───────────────────────────────────────────────────────────────


def _linhas(texto: str, tam: float, fator: float = SERIF, largura: float = G.LARGURA) -> int:
    """Linhas de ``texto`` em Noto Serif no tamanho ``tam`` (estimativa pela tabela da Archivo)."""
    return max(1, len(G.linhas_texto(texto, "label", tam * fator, largura)))


def _palavra_solta(texto: str, tam: float, fator: float = SERIF) -> bool:
    """Alguma linha fica só com uma palavra curta ("para indenizações / em / desapropriações")?"""
    return any(len(linha) <= 3 for linha in G.linhas_texto(texto, "label", tam * fator, G.LARGURA))


def _altura_numero(tam: int, qual: str) -> float:
    """O bloco do número: a linha (entrelinha .84) e o qualificador acima (20% do número)."""
    return tam * 0.84 + (tam * 0.2 + 4 if qual else 0)


def area_fato(*, relogio: bool, selo: bool, fontes: bool, chip: bool = True) -> float:
    """Altura livre do miolo de um fato: o quadro menos o chip, o respiro e o pé (relógio, selo, fontes)."""
    pe = [h for h, tem in ((112, relogio), (30, selo), (34, fontes)) if tem]
    base = (26 + sum(pe) + 18 * (len(pe) - 1)) if pe else 0
    return QUADRO - (74 if chip else 0) - 80 - base


def tamanhos_fato(valor: str, qual: str, frase: str, area: float) -> tuple[int, int]:
    """(px do número, px da frase): a maior frase de :data:`FRASE_TAMS` que cabe com o número
    de 260 a 180 px; o número encolhe antes da frase. Sem número, ``(0, px)`` de :data:`SOLO_TAMS`,
    em até 6 linhas. Tamanho que deixa uma palavra curta sozinha numa linha passa a vez."""
    limite = area * OCUPACAO
    if not valor:  # Noto Serif 700: mede como a 800
        for f in SOLO_TAMS:
            linhas = _linhas(frase, f, SERIF_800)
            if linhas <= 6 and linhas * f * 1.14 <= limite and not _palavra_solta(frase, f, SERIF_800):
                return 0, f
        return 0, SOLO_TAMS[-1]
    n_max = tamanho_numero(valor)
    for f in FRASE_TAMS:
        if _palavra_solta(frase, f) and f > FRASE_TAMS[-1]:
            continue
        h_frase = 30 + _linhas(frase, f) * f * 1.2
        for n in range(n_max, NUM_FATO_MIN - 1, -10):
            if _altura_numero(n, qual) + h_frase <= limite:
                return n, f
    return NUM_FATO_MIN, FRASE_TAMS[-1]


def area_capa(mc: dict[str, Any] | None) -> float:
    """Altura livre do miolo da capa: o quadro menos o letreiro, o respiro e o pé (mais coberto e faixa)."""
    pe = 55
    if mc:
        pe += 78 + (0 if mc["proprio"] else 10 + 41 * min(2, _linhas(mc["nome"], 34, 1.0)))
    return QUADRO - 70 - 88 - pe


def tamanhos_capa(valor: str, qual: str, manchete: str, area: float) -> tuple[int, int]:
    """(px do número, px da manchete): manchete em Noto Serif 800 de 64 a 80 px com número (até 96
    sem ele) e o número de até 280 px, o maior par que cabe."""
    limite = area * OCUPACAO
    if not valor:
        for m in MANCHETE_SOLO_TAMS:
            if _linhas(manchete, m, SERIF_800) * m * 1.07 <= limite:
                return 0, m
        return 0, MANCHETE_SOLO_TAMS[-1]
    n_max = tamanho_numero(valor, maximo=NUM_CAPA_MAX, minimo=NUM_CAPA_MIN)
    for m in MANCHETE_TAMS:
        h = 34 + _linhas(manchete, m, SERIF_800) * m * 1.07
        for n in range(n_max, NUM_CAPA_MIN - 1, -10):
            if _altura_numero(n, qual) + h <= limite:
                return n, m
    return NUM_CAPA_MIN, MANCHETE_TAMS[-1]


# ── Seleção ───────────────────────────────────────────────────────────────


def _rank(section: str | None) -> int:
    return PREFERENCIA.index(section) if section in PREFERENCIA else len(PREFERENCIA) - 2


def _titulos(edition: Edition, story: Story | None) -> list[str]:
    return E.titulos_por_veiculo(E.topic_of(edition, story.id)) if story is not None else []


def _entidades(fato_story: Story | None, texto: str) -> set[str]:
    if fato_story is not None:
        return set(entidades(fato_story))
    return {w for w in _ENTIDADE.findall(texto) if w not in {"Governo", "Brasil"}}


def _palavras(texto: str) -> list[str]:
    """As palavras que dizem o assunto, na ordem (sem acento nem caixa, sem as vazias)."""
    out = []
    for w in E._fold(plain(texto)).split():
        w = w.strip(".,;:!?()[]“”\"'‘’—–-")
        if len(w) >= 3 and w not in _VAZIAS:
            out.append(w)
    return out


def mesmo_assunto(a: str, b: str) -> bool:
    """Dois textos do mesmo dia tratam do mesmo assunto? Jaccard ≥ 0,5, ou 3+ palavras de assunto
    em comum com o sujeito (a 1ª delas) entre elas ("Fed mantém taxa de juros…" e "Fed mantém
    juros na faixa…"; "Colisão entre trens…" e "Colisão de trens…")."""
    if similaridade(a, b) >= MESMO_ASSUNTO:
        return True
    pa, pb = _palavras(a), _palavras(b)
    comuns = set(pa) & set(pb)
    return len(comuns) >= 3 and bool(pa and pb) and (pa[0] in comuns or pb[0] in comuns)


def _marca(f: Fato) -> tuple[set[str], set[str]]:
    """(entidades, números) de um fato publicado, para a regra da véspera."""
    s = f.story
    return _entidades(s, f.texto), _numeros(" ".join([f.texto, s.headline, s.dek] if s else [f.texto]))


def _repete_vespera(story: Story | None, textos: list[str], vespera: list[tuple[set[str], set[str]]]) -> bool:
    """2+ entidades em comum com a capa ou um fato da véspera e nenhum número novo (§5.4)."""
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


def eleitoral_capa(story: Story, textos: Iterable[str] = (), nomes: Iterable[str] = E.NOMES_ELEICAO) -> bool:
    """Na capa do período eleitoral vale a regra mais dura: política, :func:`_eleitoral` ou eleição
    de qualquer país na manchete ou nos ``textos`` ("Israel vai às urnas em eleição decisiva", 27/10).
    A eleição de outro país citada só na linha fina não conta ("a poucas semanas das eleições de
    meio de mandato", na suspensão de sanções de Trump, 18/10)."""
    textos = [t for t in textos if t]
    visivel = " ".join([*textos, story.headline])
    return (
        story.section == "politica"
        or _eleitoral(story, textos, tuple(nomes))
        or bool(_ELEICAO.search(visivel) or _URNAS.search(visivel))
    )


def _texto(edition: Edition, story: Story | None, briefing: str, evitar: str = "",
           evitar_numero: Iterable[str] = ()) -> Fato | None:
    """O texto do fato: o item do briefing, a manchete ou a linha fina. Vale a primeira versão
    limpa sem corte pesado; entre o item e a manchete, a que tem número em destaque passa na
    frente (a linha fina não: costuma não dizer o sujeito, "Querosene… atinge 40% dos custos da
    empresa"); sem versão sem corte pesado, a primeira com corte. Com ``evitar`` (a manchete da
    capa), pula o que tiver Jaccard ≥ 0,6 com ela; ``evitar_numero`` são números que não contam
    (o da capa, no slide 2)."""
    titulos = _titulos(edition, story)
    candidatos = [("briefing", briefing)]
    if story is not None:
        candidatos += [("manchete", story.headline), ("dek", story.dek)]
    evitar_numero = list(evitar_numero)
    limpos = []
    for i, (origem, bruto) in enumerate(candidatos):
        t, pesado = _limpo(bruto, titulos) if bruto else ("", False)
        if t and not (evitar and similaridade(t, evitar) >= JACCARD_CAPA):
            sem_numero = origem == "dek" or numero_fato(t, evitar_numero) is None
            limpos.append((pesado, sem_numero, i, origem, t))
    if not limpos:
        return None
    _, _, _, origem, t = min(limpos)
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
    """A capa e os 5 fatos do dia (§5.4).

    - Capa: a manchete (no período eleitoral, a 1ª que não é política nem eleição, de nenhum
      país), fora de ``evitar_capa`` (as matérias do Raio-X e do Número: os três posts do dia
      nunca têm a mesma peça principal). ``fora`` (a matéria do Número) não entra nem como fato.
    - Fatos: o 1º é a matéria da capa; depois os itens do briefing e, para completar, as
      matérias, pela ordem de editoria (brasil > mercados > tecnologia > …); o item do briefing
      sem matéria (sem selo nem fontes) só depois delas; esporte e variedades só se faltar fato.
    - Fora: assunto eleitoral no período eleitoral (e só 1 de política, sempre o último; a eleição de
      outro país, que o filtro deixa passar em Mundo, conta como esse item de política); fato que
      cita Ibovespa, dólar, Brent ou bitcoin com a variação do slide de mercado (``paineis``); fato
      com 2+ entidades em comum com a véspera (``vespera``) e sem número novo; o mesmo assunto
      duas vezes (2+ entidades em comum ou :func:`mesmo_assunto`).

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
            b = por_story.get(story.id, "")
            if (election and eleitoral_capa(story, [b], nomes)) or bloqueado(story, [b]):
                continue
            m = _manchete_capa(edition, story)
            if not m:
                continue
            n = numero_capa(f"{story.headline} {story.dek}")
            f = _texto(edition, story, b, evitar=m, evitar_numero=[n[1]] if n else ())
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

    escolhidos: list[tuple[Fato, bool]] = []  # (fato, ocupa a vaga de política)
    ents_dia = [_entidades(capa, primeiro.texto)]
    textos_dia = [capa.headline, primeiro.texto]
    for _, _, _, story, b in candidatos:
        if len(escolhidos) == FATOS - 1:
            break
        if bloqueado(story, [b]):
            continue
        f = _texto(edition, story, b)
        if f is None:
            continue
        # no período eleitoral, a eleição de outro país também ocupa a vaga única de política
        is_pol = election and (
            (story is not None and story.section == "politica") or bool(_ELEICAO.search(f.texto) or _URNAS.search(f.texto))
        )
        if is_pol and any(pol for _, pol in escolhidos):
            continue
        ents = _entidades(story, f.texto)
        proprios = [f.texto] + ([story.headline] if story is not None else [])
        if any(len(ents & e) >= 2 for e in ents_dia) or any(mesmo_assunto(p, t) for p in proprios for t in textos_dia):
            continue
        ents_dia.append(ents)
        textos_dia.extend(proprios)
        escolhidos.append((f, is_pol))

    def chave(item: tuple[Fato, bool]) -> tuple[bool, int]:
        f, pol = item
        return (pol, _rank(f.story.section if f.story is not None else None))

    return Selecao(capa, manchete, [primeiro, *(f for f, _ in sorted(escolhidos, key=chave))])


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
    Os dias da cadeia usam o modo eleitoral de hoje: na virada do período, a véspera recalculada
    pode diferir um pouco da que saiu.
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


def _varios_nomes(run: list[str]) -> bool:
    """A sequência junta dois nomes ou mais ("SpaceX e Tesla"), não um nome de duas palavras ("Flávio Bolsonaro")?"""
    return "e" in run and sum(1 for w in run if w.lower() not in _LIGA) >= 2


def nome_do_assunto(edition: Edition, topic: IndexTopic) -> str:
    """Como o assunto aparece na capa: os nomes que abrem o título, se forem 2 ou mais
    ("SpaceX e Tesla"); senão o título da matéria sem adjetivo da casa e com até 90 caracteres
    (nunca o título literal de um veículo); senão a tese do assunto."""
    story = next((s for s in edition.stories if s.id == topic.story_id), None) if topic.story_id else None
    titulo = _frase(story.headline if story else topic.title)
    literais = {_frase(i.title) for i in topic.items}
    textos = [i.title for i in topic.items] + ([story.headline, story.dek, *story.body[:1]] if story else [])
    proprios = _proprios(textos)
    abre = _corrida(titulo.split(), proprios)
    if _varios_nomes(abre):
        return " ".join(abre)
    if story is not None and titulo not in literais:
        limpo, _ = _limpo(titulo, E.titulos_por_veiculo(topic), limite=MANCHETE_MAX)
        if limpo:
            return _sem_ponto(limpo)
    tese = E._titulo_assunto(edition, topic)
    return _sem_ponto(tese) if tese else ""


def _mais_coberto(
    edition: Edition, capa: Story, *, election: bool, nomes: tuple[str, ...], paineis: list[dict[str, Any]] = ()
) -> dict[str, Any] | None:
    """O assunto com mais veículos (k ≥ 5). Se for o da capa, só a contagem; se for de mercado,
    só o nome do ativo ("Petróleo Brent"): o número de mercado vem do slide 7, nunca do título.
    No período eleitoral, se o mais coberto for eleição (de qualquer país), a linha sai."""
    conc = E.concentracao(edition, election=election, nomes=nomes)
    if conc is None or conc.topo is None or conc.topo_veiculos < MAIS_COBERTO_MIN:
        return None
    proprio = conc.topo.story_id == capa.id
    story = next((s for s in edition.stories if s.id == conc.topo.story_id), None)
    if election and not proprio:
        texto = E.topic_text(edition, conc.topo)
        if (story is not None and eleitoral_capa(story, [conc.topo.title], nomes)) or _ELEICAO.search(texto) or _URNAS.search(texto):
            return None
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
    """O selo de cobertura (:func:`.estatistica.selo_cobertura`), a contagem de veículos dele e a mini barra."""
    if story is None:
        return {"cobertura": "", "cob_k": 0, "cob_a": 0, "cob_n": 0, "cob_b": 0}
    c = story.coverage
    a, b = (c.count("a"), c.count("b")) if c else (0, 0)
    n = len(c.outlets) - a - b if c else 0
    selo = E.selo_cobertura(edition, story, raiox_story_id)
    m = re.match(r"(\d+) veículos?\b", selo)
    return {"cobertura": selo, "cob_k": int(m.group(1)) if m else 0, "cob_a": a, "cob_n": n, "cob_b": b}


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
    """Um fato: número (ou a cobertura, ou nada) + frase, com os tamanhos que enchem o miolo."""
    story = f.story
    numero = numero_fato(f.texto, evitar_numero)
    qual, valor = numero if numero else ("", "")
    nomes = source_names(story) if story else []
    cob = _cobertura(edition, story, raiox_story_id)
    selo_pe = cob["cobertura"]
    heroi = "numero" if valor else ""
    if not valor and cob["cob_k"] >= COBERTURA_MIN:  # a cobertura medida vira o número do slide
        heroi, qual = "cobertura", NOTICIADO
        valor = f"{cob['cob_k']} veículos"
        selo_pe = selo_pe.split(" · ", 1)[1] if " · " in selo_pe else ""
    batidas, mais = relogio
    section = section_info(edition, story.section) if story else None
    fontes = sources_line(nomes)
    area = area_fato(relogio=bool(batidas), selo=bool(selo_pe), fontes=bool(fontes), chip=bool(section))
    tam, tam_frase = tamanhos_fato(valor, qual, f.texto, area)
    ctx = {
        "selo": selo,
        "contador": True,
        "editoria": section["title"] if section else "",
        "heroi": heroi,
        "qual": qual,
        "valor": valor,
        "tam": tam,
        "frase": f.texto,
        "tam_frase": tam_frase,
        "solo": not valor,
        **cob,
        "cobertura": selo_pe,
        "fontes": fontes,
        "relogio": batidas,
        "relogio_mais": mais,
        "destaque": valor,
        "section": story.section if story else "",
        "nota": NOTA,
    }
    partes = [f"Fato {i} de {total}: {f.texto}"]
    if cob["cobertura"]:
        partes.append(f"{cob['cobertura'][:1].upper()}{cob['cobertura'][1:]}.")
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
    """A pauta solitária: "1.039 de 1.159 assuntos de hoje (90%) saíram em uma fonte só", a matriz e o CTA."""
    conc = E.concentracao(edition, election=election, nomes=nomes)
    if conc is None or not conc.assuntos:
        return None
    contagens = [len(E.topic_sources(t)) for t in edition.index]
    pct = f" ({conc.uma_fonte_pct}%)" if conc.assuntos >= E.PCT_MIN else ""
    frase = f"de {num(conc.assuntos)} assuntos de hoje{pct} saíram em uma fonte só."
    altura = 330
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
        "cta": CTA,
        "destaque": num(conc.uma_fonte),
        "section": "",
        "nota": f"fonte: índice da edição de {short_date(edition_day(edition))}",
    }
    alt = f"Pauta solitária: {num(conc.uma_fonte)} {frase} {descricao} {CTA}"
    return Slide("m1_fechamento", ctx, alt=alt)


def _linha_mercado(paineis: list[dict[str, Any]]) -> list[str]:
    """Cada ativo do slide de mercado numa frase curta: "Ibovespa 208.296 pts, fech. 20/10, +13,8% desde 28/09"."""
    out = []
    for p in paineis:
        valor = p["valor"] + (" pts" if p["id"] in M.SUFIXO else "")
        partes = [x for x in (p["selo"], p["acumulado"]) if x]
        out.append(f"{p['nome']} {valor}" + (f", {', '.join(partes)}" if partes else ""))
    return out


def linha1(manchete: str, resto: int, day: date) -> str:
    """``"{capa} · e mais 4 fatos da quarta"`` em até 110 caracteres; se não couber, a manchete
    é cortada numa junção; sem junção, fica só a manchete (cortada se precisar)."""
    sufixo = f" · e mais {resto} fatos {_DA.get(day.weekday(), 'da')} {DIAS[day.weekday()]}"
    if len(manchete) + len(sufixo) <= LINHA1_MAX:
        return manchete + sufixo
    curta = _corte(manchete, LINHA1_MAX - len(sufixo) + 1)
    if curta:
        return curta + sufixo
    return manchete if len(manchete) <= LINHA1_MAX else (_corte(manchete, LINHA1_MAX + 1) or manchete[:LINHA1_MAX])


def _legenda(
    edition: Edition,
    sel: Selecao,
    selos: list[str],
    paineis: list[dict[str, Any]],
    *,
    election: bool,
) -> str:
    """Linha 1 com a capa, "▸ fato (selo)" para cada fato, o mercado com o acumulado, o método, o CTA e as hashtags."""
    primeira = linha1(sel.manchete, len(sel.fatos) - 1, edition_day(edition))
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
        return finish_caption([primeira, "\n".join([*linhas, metodo, CTA, tags])])

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
    - ``numero``: a :class:`~.conteudo.Peca` do Número do dia: a matéria dele não vira capa nem fato e,
      se o Número for o acumulado de um ativo (``acum:ibov``), esse ativo não vira o título do mercado.

    None sem IA, sem briefing nem matérias, ou com menos de 3 fatos (o 07:00 vira o Terminal):
    em 26/10, dia seguinte ao 2º turno, só o Bola de Ouro escapa do filtro eleitoral.
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
    num_capa = numero_capa(f"{capa.headline} {capa.dek}")
    qual, valor = num_capa if num_capa else ("", "")
    mc = _mais_coberto(edition, capa, election=election, nomes=nomes, paineis=paineis)
    faixa = _faixa(edition)
    chamada = f"{total} fatos + mercado" if mercado else f"{total} fatos"
    tam, tam_manchete = tamanhos_capa(valor, qual, sel.manchete, area_capa(mc))

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
                "tam": tam,
                "manchete": sel.manchete,
                "tam_manchete": tam_manchete,
                "faixa": faixa,
                "mais_coberto": mc,
                "chamada": chamada,
                # sem número na manchete, o laranja vai para a contagem do mais coberto
                "destaque": valor or (f"{mc['k']} veículos" if mc else ""),
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
        selos.append(_cobertura(edition, f.story, raiox_story_id)["cobertura"])
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
