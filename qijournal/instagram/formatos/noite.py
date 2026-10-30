"""A publicação da noite (18:30): Raio-X 2.0 (§4.2) e Placar do dia (§4.3).

Os dois são da série do Raio-X ("RAIO-X Nº 014") e saem em papel. Todo texto vem de
campos da edição, das contas de :mod:`..estatistica` e da escolha de :mod:`..selecao`:
nenhuma IA nova. Os gráficos vêm de :mod:`..graficos`, com a frase do texto alternativo
gerada pela mesma função que desenha.

Cores por papel, não por lado: a maioria fica sempre à esquerda em ardósia e a minoria
à direita em petróleo (no empate, o lado A é a "maioria"), na barra, no relógio, no
espectro e no fundo dos slides 3 e 4.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

from qijournal.config import LimiaresConfig
from qijournal.models import Coverage, CoverageOutlet, Edition, IndexTopic, Story

from .. import estatistica as E
from .. import graficos as G
from .. import redacao as R
from .. import selecao as S
from ..conteudo import (
    Peca,
    Slide,
    alt,
    edition_day,
    finish_caption,
    hashtags,
    kicker_date,
    plain,
    section_info,
    source_names,
    sources_line,
    truncate_sentence,
)

SERIE = "raiox"
SLOT = "noite"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
SECAO_CURTA = {
    "brasil": "Brasil", "mercados": "Mercados", "juridico": "Jurídico", "politica": "Política", "mundo": "Mundo",
    "tecnologia": "Tecnologia", "imobiliario": "Imobiliário", "esporte": "Esporte", "natureza": "Natureza",
    "variedades": "Cultura",
}

CTA_RAIOX = "Leu só uma versão? Mande para quem leu a outra."
CTA_PLACAR = "Salve: o placar sai todo dia."
METODO = "Classificação de ênfase feita por IA sobre os textos publicados. Enquadramento é escolha do que destacar, não erro."
DEFINICAO = "Dividir: pelo menos 2 veículos de cada lado, entre os assuntos com 4 ou mais veículos comparados."
SERIE_COMPLETA = "Série completa no destaque Raio-X."
RODAPE_RELOGIO = "Horário informado por cada veículo, em Brasília. Não indica quem apurou primeiro."
SUB_PLACAR = "assuntos com 4 ou mais veículos comparados; dividir = pelo menos 2 veículos de cada lado."

FATO_MAX = 260  # slide 2: dek + 1ª frase do corpo, se couber
SUBTITULO_MAX_PALAVRAS = 18
FRAMING_MAX = 160  # o §4.2 diz 120, mas 99 de 267 enquadramentos dos Raio-X elegíveis passam disso (máx. 158)
LADO_FRAMINGS = 3  # veículos com enquadramento no slide do lado; o resto vai em "Também"
RACHAS_MAX = 3  # linhas do slide 2 do Placar com rachas
QUADRO = 1122  # altura útil do slide de feed no sistema 2 (1350 − faixa − assinatura)
RELOGIO_RX = 630  # px para as linhas do relógio no Raio-X (12 linhas de 52 px no pior caso)
RELOGIO_PLACAR = 540  # no Placar do dia, que tem kicker e assunto acima do gráfico


# ── Texto ─────────────────────────────────────────────────────────────────

_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"(])")
_EM_MEIO = re.compile(r"\bem meio [àa]\b", re.I)
_LADO_ENTRE_PARENTESES = re.compile(r"\s*\(lados? [ab](?: e [ab])?\)", re.I)


def _limpo(texto: str) -> bool:
    """Sem palavra proibida nem verbo que julga veículo (o lint reprovaria) e sem "em meio à"."""
    return not (R.palavras_proibidas(texto) or R.verbos_proibidos(texto) or _EM_MEIO.search(texto or ""))


def _frases(texto: str) -> list[str]:
    return [f.strip() for f in _FRASE.split(plain(texto)) if f.strip()]


def _tem_dado(texto: str) -> bool:
    """Pelo menos 1 número ou 1 nome próprio (palavra com maiúscula fora do começo de frase)."""
    if re.search(r"\d", texto):
        return True
    palavras = texto.split()
    for antes, w in zip(palavras, palavras[1:]):
        if w.strip("(“\"'")[:1].isupper() and not antes.endswith((".", "!", "?", ":")):
            return True
    return False


def _norm(texto: str) -> str:
    return re.sub(r"\s+", " ", E._fold(plain(texto))).strip(" .")


def _sem_proibidas(texto: str, min_palavras: int = 4) -> str:
    """O texto cortado antes do primeiro termo proibido ("…de Ormuz em meio a atritos" → "…de Ormuz"),
    se sobrarem ``min_palavras``; ``""`` se não der."""
    texto = plain(texto).strip()
    if _limpo(texto):
        return texto
    ms = [m.start() for m in re.finditer(r"\S+", texto) if not _limpo(texto[: m.end()])]
    head = texto[: ms[0]].rstrip(" ,;:—–-") if ms else ""
    head = re.sub(r"\s+(?:e|de|da|do|em|com|para|por|que|a|o)$", "", head)
    return head if len(head.split()) >= min_palavras and _limpo(head) else ""


def _sem_adj(texto: str, titulos: list[str]) -> str:
    """Texto corrido sem adjetivo da casa (§4.1): fica se estiver no título de metade dos veículos;
    senão, corta na última "," ou " e " antes dele se sobrar pelo menos 40% do texto; se não,
    tira só a palavra ("risco fiscal bilionário da derrota" → "risco fiscal da derrota")."""
    texto = plain(texto).strip()
    if not any(not E._adj_nos_titulos(E._raiz_adj(m.group(0)), titulos) for m in E.ADJ_CASA.finditer(texto)):
        return texto
    cut = E.sem_adj_casa(texto, titulos)
    if cut and len(cut) >= 0.4 * len(texto):
        return cut.rstrip(" ,;:") + "."
    out = E.ADJ_CASA.sub(lambda m: m.group(0) if E._adj_nos_titulos(E._raiz_adj(m.group(0)), titulos) else "", texto)
    return re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()


def _cita_veiculo(texto: str, nomes: Iterable[str]) -> bool:
    return any(re.search(r"(?<![\wÀ-ú])" + re.escape(n) + r"(?![\wÀ-ú])", texto) for n in nomes if n)


def _selo(numero_serie: int, *partes: str) -> str:
    """``RAIO-X Nº 014 · QUA 21 OUT · TECNOLOGIA`` (sem número, só ``RAIO-X``)."""
    head = f"RAIO-X Nº {numero_serie:03d}" if numero_serie > 0 else "RAIO-X"
    return " · ".join([head, *(p for p in partes if p)]).upper()


def _data_extensa(edition: Edition) -> str:
    day = edition_day(edition)
    return f"{day.day} de {MESES[day.month - 1]}"


def _hora_completa(at: datetime) -> str:
    """``"20/10 13h17"`` (horário de Brasília, sempre com a data: vai na legenda)."""
    return f"{at.astimezone(E.TZ):%d/%m %Hh%M}"


def _lado_waffle(base: int) -> int:
    """Quadrados maiores com base pequena (até 5 linhas de 7), 64 px acima disso."""
    return 76 if base <= 35 else 64


def _rotulos_waffle(rachas: int) -> tuple[str, str]:
    return ("dividiu" if rachas == 1 else "dividiram", "sem divisão")


def _lista(nomes: list[str]) -> str:
    return ", ".join(nomes)


def _titulos_literais(edition: Edition) -> set[str]:
    return {_norm(i.title) for t in edition.index for i in t.items if i.title}


# ── Raio-X: peças ─────────────────────────────────────────────────────────


def _subtitulo(edition: Edition, story: Story, veiculos: list[str]) -> str:
    """Subtítulo da capa (§4.2): o assunto da cobertura se passar no adjetivo da casa e tiver até 18
    palavras; senão a linha fina. Nunca título literal de veículo, nome de veículo nem palavra proibida."""
    c = story.coverage
    titulos = E.titulos_por_veiculo(E.topic_of(edition, story.id))
    literais = _titulos_literais(edition)
    candidatas = []
    topic = plain(c.topic) if c else ""
    if topic and E.sem_adj_casa(topic, titulos) == topic and len(topic.split()) <= SUBTITULO_MAX_PALAVRAS:
        candidatas.append(topic)
    candidatas += [plain(story.dek), E.capa_sem_adj(edition, story)]
    for texto in candidatas:
        if texto and _limpo(texto) and _norm(texto) not in literais and not _cita_veiculo(texto, veiculos):
            return texto
    return _sem_proibidas(story.dek) or _sem_proibidas(E.capa_sem_adj(edition, story))


def _fato(story: Story, evitar: str, titulos: list[str]) -> str:
    """Slide 2 (§4.2): a linha fina e a 1ª frase do corpo, se o total couber em 260 caracteres; sem
    número nem nome próprio, entra a frase seguinte. Se a capa já usou a linha fina (``evitar``), o
    slide 2 sai só do corpo, para não repetir a capa (T3). Frases com palavra proibida ficam de fora."""
    def ok(f: str) -> bool:
        return _limpo(f) and _sem_adj(f, titulos) == f

    dek = plain(story.dek)
    frases = [f for p in story.body for f in _frases(p) if ok(f)]
    partes = [dek] if dek and ok(dek) and _norm(dek) != _norm(evitar) else []
    for f in frases:
        if not partes:
            if len(f) <= FATO_MAX:
                partes.append(f)
            continue
        if len(" ".join([*partes, f])) <= FATO_MAX:
            partes.append(f)
        break
    if not partes:
        base = frases[0] if frases else dek
        return truncate_sentence(base, FATO_MAX) if base else ""
    texto = " ".join(partes)
    if not _tem_dado(texto):
        prox = next((f for f in frases if f not in partes), None)
        if prox:
            texto = f"{texto} {prox}"
    return texto


def _conclusao(c: Coverage) -> str:
    """A conclusão da cobertura sem "(lado A)"/"(lado B)": aqui os lados se chamam maioria e minoria."""
    return re.sub(r"\s+([,.;])", r"\1", _LADO_ENTRE_PARENTESES.sub("", plain(c.conclusion))).strip()


def _papel(ld: S.Lados) -> dict[str, str]:
    """Stance da cobertura → papel na arte ("a" = maioria, "b" = minoria)."""
    return {ld.maioria: "a", ld.minoria: "b", "neutro": "neutro"}


def _ordem(outlets: list[CoverageOutlet], horas: dict[str, datetime]) -> list[CoverageOutlet]:
    """Em ordem de horário (relógio); os sem horário no fim, na ordem da cobertura."""
    fim = datetime.max.replace(tzinfo=E.TZ)
    return sorted(outlets, key=lambda o: (o.name not in horas, horas.get(o.name, fim)))


def _linhas_relogio(n: int, disponivel: int = RELOGIO_RX) -> dict[str, int]:
    """Altura e número máximo de linhas do relógio (kwargs de :func:`.graficos.relogio`): com poucos
    veículos as linhas abrem (até 100 px) e ocupam o slide; com muitos, encolhem até 44 px e, se nem
    assim couberem 12, as do meio viram "+k veículos"."""
    linhas = max(min(n, 12), 1)
    if disponivel // linhas >= 44:
        return {"linha": min(100, disponivel // linhas), "max_linhas": 12}
    return {"linha": 44, "max_linhas": max(6, disponivel // 44)}


def _linhas_serif(texto: str, tam: float, largura: float = G.LARGURA, fator: float = 0.53) -> int:
    """Linhas que ``texto`` ocupa em Noto Serif de ``tam`` px (quebra gulosa por palavra; ``fator`` é a
    largura média do caractere em em: 0,515 no peso 500 e 0,54 no 600, medidos no Chromium)."""
    cabe = largura / (fator * tam)
    linhas, atual = 0, 0.0
    for w in texto.split():
        if atual and atual + 1 + len(w) <= cabe:
            atual += 1 + len(w)
        else:
            linhas, atual = linhas + 1, float(len(w))
    return max(linhas, 1)


def _tam_serif(texto: str, altura: float, maximo: int, minimo: int, entrelinha: float, fator: float = 0.53) -> int:
    """O maior corpo (de 2 em 2 px) em que ``texto`` cabe em ``altura`` px; o ``data-fit`` do template
    ainda encolhe se a estimativa errar."""
    for tam in range(maximo, minimo - 1, -2):
        if _linhas_serif(texto, tam, fator=fator) * tam * entrelinha <= altura:
            return tam
    return minimo


def _altura_svg(svg: str) -> float:
    m = re.search(r"height='([\d.]+)'", svg or "")
    return float(m.group(1)) if m else 0.0


def _tam_lista(titulo: str, descricao: str, veiculos: list[dict[str, str]], rodape: list[str]) -> int:
    """Corpo da lista de veículos dos slides 3 e 4: o maior (de 30 a 46 px) que cabe no que sobra do quadro."""
    titulo_h = len(G.linhas_texto(titulo, "hook", 96, G.LARGURA * 0.985, tracking=-0.01)) * 96 * 0.92 + 23
    desc_h = 18 + _linhas_serif(descricao, 32, fator=0.47) * 32 * 1.3 if descricao else 0
    pe_h = 26 + sum(_linhas_serif(r, 28, fator=0.55) * 38 + 12 for r in rodape if r) if any(rodape) else 0
    livre = QUADRO - (34 + 30 + 14 + titulo_h + desc_h + 34 + pe_h) - 16
    for tam in range(46, 29, -1):
        alto = sum(tam * (0.88 * 1.15 + 1.28 + 0.3) + _linhas_serif(v["framing"], tam, fator=0.5) * tam * 1.3
                   for v in veiculos)
        if alto <= livre:
            return tam
    return 30


def _relogio_do_assunto(topic: IndexTopic) -> E.Relogio | None:
    """Relógio de um assunto do index sem matéria (mesma conta de :func:`.estatistica.relogio`)."""
    first: dict[str, tuple[datetime, str]] = {}
    for item in topic.items:
        at = E._parse_dt(item.published)
        if at is None or not item.source or item.source in E.ORGAOS:
            continue
        if item.source not in first or at < first[item.source][0]:
            first[item.source] = (at, item.lang)
    if len(first) < 2:
        return None
    return E.Relogio(sorted((E.Batida(s, at, None, lang) for s, (at, lang) in first.items()), key=lambda b: b.at))


def _tam_gancho(texto: str) -> int:
    """Corpo do gancho: o maior entre 112 e 150 px em 4 linhas; se não couber, em 5 (até 100 px)."""
    largura = G.LARGURA * 0.985
    tam = G.tamanho_gancho(texto, max_linhas=4)
    if len(G.linhas_texto(texto.upper(), "hook", tam, largura, tracking=-0.01)) <= 4:
        return tam
    return G.tamanho_gancho(texto, max_linhas=5, tam_min=100)


def _titulo_lado(contagem: str, rotulo: str) -> str:
    """``"6 de 11 · sinergia em IA"`` numa linha; se não couber, ``"4 de 6: restabelecimento"`` (os dois
    pontos fecham bem a 1ª linha; o "·" ficaria sozinho no fim dela)."""
    titulo = f"{contagem} · {rotulo}"
    if len(G.linhas_texto(titulo.upper(), "hook", 96, G.LARGURA * 0.985, tracking=-0.01)) <= 1:
        return titulo
    return f"{contagem}: {rotulo}"


def _lado_ctx(
    outlets: list[CoverageOutlet],
    horas: dict[str, datetime],
    day: date,
) -> tuple[list[dict[str, str]], list[str]]:
    """Até 3 veículos com enquadramento ("Veículo · framing", com a hora) e os demais só pelo nome."""
    mostrados, resto = [], []
    for o in outlets:
        framing = plain(o.framing)
        if framing and len(mostrados) < LADO_FRAMINGS:
            at = horas.get(o.name)
            mostrados.append(
                {"name": o.name, "hora": E.hora(at, day) if at else "", "framing": truncate_sentence(framing, FRAMING_MAX)}
            )
        else:
            resto.append(o.name)
    return mostrados, resto


def raio_x(
    edition: Edition,
    brand: dict[str, Any],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    numero_serie: int,
    entidades_recentes: Iterable[Iterable[str]] = (),
    limiares: LimiaresConfig | None = None,
    story: Story | None = None,
) -> Peca | None:
    """Raio-X 2.0 (§4.2): carrossel de 8 slides (7 sem "Por que importa") sobre a cobertura dividida do dia.

    1 capa-tensão (gancho pelos moldes, subtítulo, barra maioria | sem lado | minoria) · 2 o fato ·
    3 a maioria · 4 a minoria e os sem lado · 5 Relógio da notícia (ou "A atenção", se o relógio não
    vale) · 6 o que isso significa (conclusão + espectro) · 7 por que importa · 8 Placar do dia,
    método e CTA. ``story`` (opcional) é a matéria já escolhida por :func:`.selecao.escolher_noite`;
    sem ela, a escolha é :func:`.selecao.escolher_raiox`. ``None`` sem IA ou sem matéria elegível.
    """
    if edition.mode != "ai":
        return None
    nomes, lim = tuple(nomes), limiares or LimiaresConfig()
    if story is None:
        story = S.escolher_raiox(
            edition, election=election, nomes=nomes, entidades_recentes=entidades_recentes, limiares=lim
        )
    if story is None or story.coverage is None:
        return None
    c: Coverage = story.coverage
    day = edition_day(edition)
    section = section_info(edition, story.section)
    ld = S.lados(c, nomes)
    gancho = S.gancho_raiox(c, nomes)
    papel = _papel(ld)
    n_total = ld.total
    rot_maj, rot_min = ld.rot_maioria, ld.rot_minoria
    nome_maj, nome_min = rot_maj or "maioria", rot_min or "minoria"
    veiculos = [o.name for o in c.outlets]
    nota = f"análise por IA de {n_total} veículos"

    rel = E.relogio(edition, story)
    horas = {b.source: b.at for b in rel.batidas} if rel else {}
    maioria = _ordem([o for o in c.outlets if o.stance == ld.maioria], horas)
    minoria = _ordem([o for o in c.outlets if o.stance == ld.minoria], horas)
    neutros = _ordem([o for o in c.outlets if o.stance not in ("a", "b")], horas)
    topic = E.topic_of(edition, story.id)
    fora = [v for v in (E.topic_sources(topic, veiculos=True) if topic else []) if v not in set(veiculos)]
    if rel:
        fora = sorted(fora, key=lambda v: horas.get(v, datetime.max.replace(tzinfo=E.TZ)))

    selo_capa = _selo(numero_serie, kicker_date(day), SECAO_CURTA.get(story.section, section["title"]))
    selo = _selo(numero_serie)
    slides: list[Slide] = []

    # 1. Capa-tensão
    subtitulo = _subtitulo(edition, story, veiculos)
    if not ld.empate:
        rot_barra = {"a": "maioria", "n": "sem lado", "b": "minoria"}
    else:  # no empate não há maioria: a barra leva os rótulos dos lados
        rot_barra = {"a": rot_maj, "n": "sem lado", "b": rot_min} if rot_maj and rot_min else None
    barra, d_barra = G.barra_anb(len(maioria), len(neutros), len(minoria), rot_barra, altura=140)
    numero_txt = f" nº {numero_serie}" if numero_serie > 0 else ""
    slides.append(
        Slide(
            "rx_capa",
            {
                "selo": selo_capa,
                "gancho": gancho.texto,
                "destaque": gancho.destaque,
                "gancho_px": _tam_gancho(gancho.texto),
                "subtitulo": subtitulo,
                "grafico": barra,
                "nota": nota,
                "section": section,
            },
            alt=alt(f"Raio-X da cobertura{numero_txt}, {_data_extensa(edition)}. {gancho.legenda} "
                    + (f"{subtitulo.rstrip('.')}. " if subtitulo else "") + d_barra),
        )
    )

    # 2. O fato
    titulos = E.titulos_por_veiculo(topic)
    fato = _fato(story, subtitulo, titulos)
    fontes = source_names(story)
    slides.append(
        Slide(
            "rx_fato",
            {
                "selo": selo,
                "kicker": "O fato",
                "texto": fato,
                "texto_px": _tam_serif(fato, 780, 76, 46, 1.22, 0.515),
                "fontes": sources_line(fontes, 4 if len(", ".join(fontes[:4])) <= 58 else 3),
                "cobertura": f"{n_total} veículos · {len(maioria)} × {len(minoria)}"
                + (f" · {len(neutros)} sem lado" if neutros else ""),
                "a": len(maioria),
                "n": len(neutros),
                "b": len(minoria),
                "nota": nota,
                "section": section,
            },
            alt=alt(f"O fato: {fato}" + (f" Fontes: {_lista(fontes)}." if fontes else "")),
        )
    )

    # 3 e 4. A maioria e a minoria
    empate = ld.empate
    for chave, outlets, rotulo, nome, kicker in (
        ("a", maioria, rot_maj, "maioria", "Metade" if empate else "A maioria"),
        ("b", minoria, rot_min, "minoria", "A outra metade" if empate else "A minoria"),
    ):
        mostrados, resto = _lado_ctx(outlets, horas, day)
        lado_txt = _sem_adj(c.side_a if (ld.maioria if chave == "a" else ld.minoria) == "a" else c.side_b, titulos)
        contagem = f"{len(outlets)} de {n_total}"
        titulo = _titulo_lado(contagem, rotulo or f"a {nome}")
        extra = []
        if chave == "b" and neutros:
            extra = [f"Sem tomar lado ({len(neutros)}): {_lista([o.name for o in neutros])}"]
        ctx = {
            "selo": selo,
            "lado": chave,
            "kicker": kicker,
            "titulo": titulo.upper(),
            "destaque": contagem.upper(),
            "descricao": lado_txt if lado_txt and _limpo(lado_txt) else "",
            "veiculos": mostrados,
            "tambem": _lista(resto),
            "neutros": extra[0] if extra else "",
            "nota": nota,
            "section": section,
        }
        ctx["lista_px"] = _tam_lista(ctx["titulo"], ctx["descricao"], mostrados, [ctx["tambem"], ctx["neutros"]])
        texto_alt = (
            f"{kicker}, {contagem} veículos: {rotulo or nome}."
            + (f" {ctx['descricao'].rstrip('.')}." if ctx["descricao"] else "")
            + "".join(f" {v['name']}" + (f", {v['hora']}" if v["hora"] else "") + f": {v['framing']}" for v in mostrados)
            + (f" Também: {_lista(resto)}." if resto else "")
            + (f" {extra[0]}." if extra else "")
        )
        slides.append(Slide("rx_lado", ctx, alt=alt(texto_alt)))

    # 5. Relógio da notícia (ou "A atenção")
    if E.relogio_valido(rel, min_veiculos=lim.relogio_min_veiculos, min_span_min=lim.relogio_min_span_min):
        assert rel is not None
        k = len(rel.batidas)
        dur = E.duracao(rel.span)
        vir = S.virada(edition, story, rel, nomes)
        contexto = _contexto_atencao(edition, story) if k <= 8 else []
        stance_papel = {o.name: papel.get(o.stance) for o in c.outlets}
        svg, d_rel = G.relogio(
            rel.batidas,
            stance_papel,
            day,
            rotulos={"a": nome_maj, "b": nome_min},
            **_linhas_relogio(len(rel.batidas), RELOGIO_RX + _folga_virada(vir) - _altura_contexto(contexto)),
            base="",
        )
        slides.append(
            Slide(
                "rx_relogio",
                {
                    "selo": f"{selo} · Relógio da notícia".upper(),
                    "titulo": f"{k} veículos em {dur}".upper(),
                    "destaque": dur.upper(),
                    "grafico": svg,
                    "virada": vir.texto if vir else "",
                    "contexto": contexto,
                    "rodape": RODAPE_RELOGIO,
                    "nota": nota,
                    "section": section,
                },
                alt=alt(f"Relógio da notícia: {k} veículos em {dur}. {d_rel} "
                        + (f"{vir.texto} " if vir else "") + " ".join(contexto) + (" " if contexto else "") + RODAPE_RELOGIO),
            )
        )
        linha_relogio = (
            f"▸ Relógio: {k} veículos em {dur}, de {rel.batidas[0].source} ({_hora_completa(rel.batidas[0].at)}) "
            f"a {rel.batidas[-1].source} ({_hora_completa(rel.batidas[-1].at)}), horário informado por cada veículo."
        )
    else:
        slides.append(_slide_atencao(edition, story, selo, nota, section))
        at = S.atencao(edition, story)
        linha_relogio = f"▸ A atenção: {at.veiculos} veículos, {at.textos} textos; {at.linhas[1]}." if at else ""

    # 6. O que isso significa
    conclusao = _sem_adj(_conclusao(c), titulos)
    svg, d_esp = G.espectro(
        [o.name for o in maioria],
        [o.name for o in neutros],
        [o.name for o in minoria],
        rotulos=(nome_maj, nome_min),
        lado_maioria="a",
        fora=fora,
    )
    slides.append(
        Slide(
            "rx_significado",
            {"selo": selo, "kicker": "O que isso significa", "conclusao": conclusao, "grafico": svg, "nota": nota,
             "conclusao_px": _tam_serif(conclusao, QUADRO - 130 - _altura_svg(svg), 60, 34, 1.2, 0.54),
             "section": section},
            alt=alt(f"O que isso significa: {conclusao} {d_esp}"),
        )
    )

    # 7. Por que importa (some sem o campo)
    why = _sem_adj(story.why_it_matters or "", titulos) if story.why_it_matters else ""
    why = why if _limpo(why) else _sem_proibidas(why)
    if why:
        slides.append(
            Slide(
                "rx_importa",
                {"selo": selo, "kicker": "Por que importa", "texto": why, "nota": nota, "section": section,
                 "texto_px": _tam_serif(why, 780, 100, 56, 1.12, 0.54)},
                alt=alt(f"Por que importa: {why}"),
            )
        )

    # 8. Placar do dia + método + CTA
    p = E.placar(edition, election=election, nomes=nomes)
    rachas = E.rachas_do_dia(edition, election=election, nomes=nomes)
    idx = next((i for i, (cov, _) in enumerate(rachas) if cov is c), None)
    svg, d_waf = G.waffle(p.base, p.rachas, idx, rotulos=_rotulos_waffle(p.rachas), lado=_lado_waffle(p.base))
    titulo_placar = f"{p.texto_dia} assuntos dividiram a cobertura hoje"
    slides.append(
        Slide(
            "rx_placar",
            {
                "selo": f"{selo} · Placar do dia".upper(),
                "kicker": "",
                "titulo": titulo_placar.upper(),
                "destaque": p.texto_dia.upper(),
                "grafico": svg,
                "definicao": DEFINICAO,
                "metodo": METODO,
                "cta": CTA_RAIOX,
                "serie": SERIE_COMPLETA,
                "nota": "análise por IA",
                "section": section,
            },
            alt=alt(f"Placar do dia: {titulo_placar}. {d_waf} {DEFINICAO} {METODO} {CTA_RAIOX} {SERIE_COMPLETA}"),
        )
    )

    # Legenda
    fato_legenda = plain(story.dek) if _limpo(plain(story.dek)) else fato
    linhas = [
        f"▸ O fato: {fato_legenda}",
        f"▸ {nome_maj[0].upper() + nome_maj[1:]} ({len(maioria)}): {_lista([o.name for o in maioria])}",
        f"▸ {nome_min[0].upper() + nome_min[1:]} ({len(minoria)}): {_lista([o.name for o in minoria])}",
    ]
    opcionais = [
        f"▸ Sem tomar lado ({len(neutros)}): {_lista([o.name for o in neutros])}" if neutros else "",
        linha_relogio,
        f"▸ Placar do dia: {p.texto_dia} assuntos com 4 ou mais veículos dividiram a cobertura.",
    ]
    fecho = [f"Por que importa: {why}" if why else "", METODO, CTA_RAIOX, hashtags("#jornalismo", [story.section], election=election)]
    caption = _legenda(gancho.legenda, linhas, opcionais, fecho)

    return Peca(
        slot=SLOT,
        kind="carousel",
        title="Raio-X da cobertura",
        slides=slides,
        caption=caption,
        story_ids=[story.id],
        serie=SERIE,
        numero_serie=numero_serie,
        entidades=S.entidades(story),
        formula=gancho.formula,
    )


def _folga_virada(vir: S.Virada | None) -> int:
    """Quanto o relógio ganha (ou perde) conforme a linha de virada tem menos (ou mais) que as 2 linhas previstas."""
    if vir is None:
        return 112
    return 44 * (2 - _linhas_serif(vir.texto, 34, fator=0.5))


def _altura_contexto(linhas: list[str]) -> int:
    """Altura que as linhas de contexto tiram do relógio (30 px de respiro + ~72 px por linha de até 2 linhas)."""
    return 30 + sum(28 + 39 * _linhas_serif(t, 30, fator=0.55) for t in linhas) if linhas else 0


def _contexto_atencao(edition: Edition, story: Story) -> list[str]:
    """As linhas de "A atenção" em frase, para o pé do relógio quando há poucos veículos (o espaço sobra)."""
    at = S.atencao(edition, story)
    if at is None:
        return []
    out = [
        f"{at.textos} textos de {at.veiculos} veículos: o {at.rank}º assunto mais coberto entre os {E.num(at.assuntos)} do dia.",
        f"{E.num(at.uma_fonte)} dos {E.num(at.assuntos)} assuntos do dia saíram em uma fonte só.",
    ]
    if at.textos_en:
        out.append(f"{at.textos_en} dos {at.textos} textos em inglês.")
    return out


def _slide_atencao(edition: Edition, story: Story, selo: str, nota: str, section: dict[str, str]) -> Slide:
    """Substituto do relógio (§4.2): "{k} VEÍCULOS, {t} TEXTOS", o lugar do assunto entre os do dia e,
    se houver, os textos em inglês; a matriz de pontos com o assunto destacado."""
    at = S.atencao(edition, story)
    topic = E.topic_of(edition, story.id)
    if at is None or topic is None:
        k = len(story.coverage.outlets) if story.coverage else len(source_names(story))
        texto = f"{k} veículos analisados"
        return Slide("rx_atencao", {"selo": f"{selo} · A atenção".upper(), "titulo": texto.upper(), "destaque": str(k),
                                    "linhas": [], "grafico": "", "nota": nota, "section": section}, alt=alt(f"A atenção: {texto}."))
    contagens = [len(E.topic_sources(t)) for t in edition.index]
    svg, d = G.matriz_pontos(contagens, edition.index.index(topic), rotulo_destaque="este assunto", altura_max=700)
    titulo, *resto = at.linhas
    return Slide(
        "rx_atencao",
        {
            "selo": f"{selo} · A atenção".upper(),
            "titulo": titulo,
            "destaque": f"{at.veiculos} VEÍCULOS",
            "linhas": resto,
            "grafico": svg,
            "nota": nota,
            "section": section,
        },
        alt=alt(f"A atenção: {at.veiculos} veículos, {at.textos} textos. " + ". ".join(resto) + f". {d}"),
    )


def _legenda(linha1: str, linhas: list[str], opcionais: list[str], fecho: list[str]) -> str:
    """Legenda no molde do §4.1, até 1.300 caracteres: sem caber, saem as linhas "▸" opcionais
    (de trás para a frente) e, por fim, o "Por que importa" é encurtado."""
    opcionais = [o for o in opcionais if o]
    fecho = [f for f in fecho if f]

    def montar() -> str:
        return finish_caption([linha1, "\n".join(linhas + opcionais), "\n".join(fecho)])

    caption = montar()
    while len(caption) > R.LEGENDA_MAX and opcionais:
        opcionais.pop()
        caption = montar()
    if len(caption) > R.LEGENDA_MAX and fecho and fecho[0].startswith("Por que importa"):
        sobra = len(fecho[0]) - (len(caption) - R.LEGENDA_MAX)
        fecho[0] = truncate_sentence(fecho[0], max(sobra, 40))
        caption = montar()
    return caption


# ── Placar do dia ─────────────────────────────────────────────────────────


def _titulo_cobertura(edition: Edition, cov: Coverage, story: Story | None) -> str:
    """O assunto de uma cobertura para a arte: sem adjetivo da casa, sem palavra proibida e nunca
    o título literal de um veículo (``""`` se não sobrar título)."""
    literais = _titulos_literais(edition)
    titulos = E.titulos_por_veiculo(E.topic_of(edition, story.id)) if story else []
    texto = E.sem_adj_casa(cov.topic, titulos, dek=story.dek if story else "")
    if not texto:  # sem linha fina para trocar: tira só o adjetivo
        texto = re.sub(r"\s{2,}", " ", E.ADJ_CASA.sub("", plain(cov.topic))).strip()
    texto = _sem_proibidas(texto)
    if not texto or _norm(texto) in literais:
        texto = _sem_proibidas(E.capa_sem_adj(edition, story)) if story else ""
    return "" if not texto or _norm(texto) in literais else texto


def _titulo_topico(edition: Edition, topic: IndexTopic) -> str:
    story = next((s for s in edition.stories if s.id == topic.story_id), None) if topic.story_id else None
    if story is not None and story.coverage is not None:
        texto = _titulo_cobertura(edition, story.coverage, story)
        if texto:
            return texto
    return _sem_proibidas(E._titulo_assunto(edition, topic))


def placar_do_dia(
    edition: Edition,
    brand: dict[str, Any],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    numero_serie: int,
    limiares: LimiaresConfig | None = None,
) -> Peca | None:
    """Placar do dia (§4.3): 4 slides, variante da noite quando nenhuma matéria é elegível ao Raio-X.

    1 capa "X DE Y ASSUNTOS DIVIDIRAM A COBERTURA HOJE" com o waffle (com 0 rachas, "DIA DE
    CONSENSO") · 2 o maior consenso (ou os rachas, até 3) · 3 o assunto mais coberto, com o relógio
    se valer · 4 método + CTA. ``None`` sem IA ou com base menor que ``limiares.placar_min_base``.
    """
    lim = limiares or LimiaresConfig()
    if edition.mode != "ai":
        return None
    nomes = tuple(nomes)
    p = E.placar(edition, election=election, nomes=nomes)
    if p.base < lim.placar_min_base:
        return None
    day = edition_day(edition)
    consenso = p.rachas == 0
    variante = "Dia de consenso" if consenso else "Placar do dia"
    selo = _selo(numero_serie)
    nota = f"{kicker_date(day).lower()} · classificação por IA"
    story_ids: list[str] = []
    secoes: list[str] = []
    slides: list[Slide] = []

    # 1. Capa
    titulo = f"{p.texto_dia} assuntos dividiram a cobertura hoje"
    svg, d_waf = G.waffle(p.base, p.rachas, rotulos=_rotulos_waffle(p.rachas), lado=88 if p.base <= 21 else _lado_waffle(p.base))
    slides.append(
        Slide(
            "placar_capa",
            {
                "selo": _selo(numero_serie, variante),
                "gancho": titulo.upper(),
                "gancho_px": _tam_gancho(titulo),
                "destaque": p.texto_dia.upper(),
                "subtitulo": SUB_PLACAR,
                "grafico": svg,
                "nota": nota,
            },
            alt=alt(f"{variante}, Raio-X da cobertura" + (f" nº {numero_serie}" if numero_serie > 0 else "")
                    + f", {_data_extensa(edition)}. {titulo}. Base: {SUB_PLACAR} {d_waf}"),
        )
    )

    # 2. O maior consenso, ou os rachas
    if consenso:
        par = E.maior_consenso(edition, election=election, nomes=nomes)
        if par is None:  # nenhuma sem lado: a maior cobertura da base
            par = max(p.itens, key=lambda cs: len(cs[0].outlets))
        cov, st = par
        assunto = _titulo_cobertura(edition, cov, st)
        a, b = cov.count("a"), cov.count("b")
        n_neu = len(cov.outlets) - a - b
        sem_lado = a == 0 and b == 0
        svg, d_bar = G.barra_anb(a, n_neu, b, {"a": "de um lado", "n": "sem lado", "b": "do outro"}, base="", altura=140)
        conclusao = _conclusao(cov)
        conclusao = conclusao if _limpo(conclusao) else _sem_proibidas(conclusao)
        cabeca = f"{len(cov.outlets)} veículos, nenhum lado" if sem_lado else f"{len(cov.outlets)} veículos, sem divisão"
        section = section_info(edition, E.coverage_section(cov, st))
        slides.append(
            Slide(
                "placar_lista",
                {
                    "selo": selo,
                    "kicker": "O maior consenso",
                    "titulo": cabeca.upper(),
                    "destaque": f"{len(cov.outlets)} VEÍCULOS",
                    "assunto": assunto,
                    "assunto_px": _tam_serif(assunto, 200, 56, 36, 1.16, 0.56),
                    "texto": conclusao,
                    "texto_px": _tam_serif(conclusao, 250, 40, 28, 1.32, 0.5),
                    "itens": [],
                    "nomes": _lista([o.name for o in cov.outlets]),
                    "grafico": svg,
                    "nota": nota,
                    "section": section,
                },
                alt=alt(f"O maior consenso: {assunto}. {cabeca}. {conclusao} {d_bar} Veículos: {_lista([o.name for o in cov.outlets])}."),
            )
        )
        linha2 = f"▸ Maior consenso: {assunto}, {cabeca}."
        if st is not None:
            story_ids.append(st.id)
        secoes.append(section["id"])
    else:
        pares = []
        for cov, st in E.rachas_do_dia(edition, election=election, nomes=nomes):
            assunto = _titulo_cobertura(edition, cov, st)
            if assunto:
                pares.append((cov, st, assunto))
            if len(pares) == RACHAS_MAX:
                break
        itens, descricoes = [], []
        sozinho = len(pares) == 1
        for cov, st, assunto in pares:
            ld = S.lados(cov, nomes)
            rot = f" · {ld.rot_maioria} × {ld.rot_minoria}" if ld.rot_maioria and ld.rot_minoria else ""
            rotulos = {"a": ld.rot_maioria or "maioria", "n": "sem lado", "b": ld.rot_minoria or "minoria"}
            svg, d = G.barra_anb(ld.n_maioria, ld.neutros, ld.n_minoria, rotulos, base="", altura=112 if sozinho else 64)
            conclusao = _sem_adj(_conclusao(cov), E.titulos_por_veiculo(E.topic_of(edition, st.id)) if st else [])
            itens.append({
                "assunto": assunto,
                "linha": f"{ld.n_maioria} × {ld.n_minoria}{rot}",
                "contagem": f"{ld.n_maioria} × {ld.n_minoria}" + (f" · {ld.neutros} sem lado" if ld.neutros else ""),
                "grafico": svg,
                "texto": conclusao if sozinho and _limpo(conclusao) else "",
                "section": E.coverage_section(cov, st),
            })
            descricoes.append(d)
            if st is not None:
                story_ids.append(st.id)
        secoes += [i["section"] for i in itens]
        unica = {i["section"] for i in itens}
        section = section_info(edition, unica.pop()) if len(unica) == 1 else {"id": "", "title": "", "color": ""}
        slides.append(
            Slide(
                "placar_lista",
                {
                    "selo": selo,
                    "kicker": f"{p.texto_dia} assuntos",
                    "titulo": "Onde a cobertura dividiu".upper(),
                    "destaque": "",
                    "assunto": "",
                    "texto": "",
                    "itens": [{k: v for k, v in i.items() if k != "section"} for i in itens],
                    "itens_px": 46 if sozinho else (40 if len(itens) == 2 else 36),
                    "nomes": "",
                    "grafico": "",
                    "nota": nota,
                    "section": section,
                },
                alt=alt("Onde a cobertura dividiu: " + " ".join(
                    f"{i['assunto']}: {i['linha']}." + (f" {i['texto']}" if i["texto"] else "") + f" {d}"
                    for i, d in zip(itens, descricoes))),
            )
        )
        linha2 = "▸ Dividiram: " + "; ".join(f"{i['assunto']} ({i['linha']})" for i in itens)

    # 3. O mais coberto
    conc = E.concentracao(edition, election=election, nomes=nomes)
    linha3 = ""
    if conc is not None and conc.topo is not None:
        topo = conc.topo
        st = next((s for s in edition.stories if s.id == topo.story_id), None) if topo.story_id else None
        k, t = conc.topo_veiculos, E.topic_texts(topo)
        mesmo = st is not None and st.id in story_ids
        assunto = _titulo_topico(edition, topo)
        rel = E.relogio(edition, st) if st is not None else _relogio_do_assunto(topo)
        section = section_info(edition, topo.section)
        texto_assunto = (
            f"O mesmo assunto foi o mais coberto do dia, entre {E.num(conc.assuntos)}."
            if mesmo else f"{assunto}: o mais coberto do dia, entre {E.num(conc.assuntos)} assuntos."
        )
        titulo3 = f"{k} veículos, {t} textos"
        if E.relogio_valido(rel, min_veiculos=lim.relogio_min_veiculos, min_span_min=lim.relogio_min_span_min):
            assert rel is not None
            stance = {o.name: o.stance for o in st.coverage.outlets} if st is not None and st.coverage else None
            svg, d = G.relogio(
                rel.batidas,
                stance,
                day,
                rotulos={"a": "lado A", "b": "lado B"},
                **_linhas_relogio(len(rel.batidas), RELOGIO_PLACAR - 42 * (_linhas_serif(texto_assunto, 32, fator=0.54) - 1)),
                base="",
            )
            dur = E.duracao(rel.span)
            vir = S.virada(edition, st, rel, nomes) if st is not None else None
            primeiro, ultimo = rel.batidas[0], rel.batidas[-1]
            virada_txt = vir.texto if vir else (
                f"Primeiro horário: {primeiro.source}, {E.hora(primeiro.at, day)}. "
                f"Último: {ultimo.source}, {E.hora(ultimo.at, day)}. São {dur} entre os dois."
            )
            slides.append(
                Slide(
                    "rx_relogio",
                    {
                        "selo": f"{selo} · Relógio da notícia".upper(),
                        "kicker": "O mais coberto",
                        "titulo": titulo3.upper(),
                        "destaque": f"{k} VEÍCULOS",
                        "assunto": texto_assunto,
                        "grafico": svg,
                        "virada": virada_txt,
                        "rodape": RODAPE_RELOGIO,
                        "nota": nota,
                        "section": section,
                    },
                    alt=alt(f"O mais coberto: {titulo3}. {texto_assunto} {d} {virada_txt} {RODAPE_RELOGIO}"),
                )
            )
            linha3 = (f"▸ O mais coberto: {'o mesmo assunto' if mesmo else assunto}, {k} veículos e {t} textos, de {rel.batidas[0].source} "
                      f"({_hora_completa(rel.batidas[0].at)}) a {rel.batidas[-1].source} ({_hora_completa(rel.batidas[-1].at)}).")
        else:
            contagens = [len(E.topic_sources(x)) for x in edition.index]
            svg, d = G.matriz_pontos(contagens, edition.index.index(topo), rotulo_destaque="o mais coberto", altura_max=640)
            linhas = [texto_assunto, f"{E.num(conc.uma_fonte)} dos {E.num(conc.assuntos)} assuntos saíram em uma fonte só"]
            slides.append(
                Slide(
                    "rx_atencao",
                    {
                        "selo": f"{selo} · O mais coberto".upper(),
                        "kicker": "O mais coberto",
                        "titulo": titulo3.upper(),
                        "destaque": f"{k} VEÍCULOS",
                        "linhas": linhas,
                        "grafico": svg,
                        "nota": nota,
                        "section": section,
                    },
                    alt=alt(f"O mais coberto: {titulo3}. " + ". ".join(linhas) + f". {d}"),
                )
            )
            linha3 = f"▸ O mais coberto: {'o mesmo assunto' if mesmo else assunto}, {k} veículos e {t} textos."
        if st is not None and st.id not in story_ids:
            story_ids.append(st.id)
        secoes.append(topo.section)

    # 4. Método + CTA
    base_txt = f"Base: os {p.base} assuntos do dia com 4 ou mais veículos comparados" + (
        "; no período eleitoral, política e eleição ficam fora." if election else "."
    )
    exemplos = [
        G.barra_anb(2, 1, 2, largura=420, altura=72, base="dividiu: 2 × 2"),
        G.barra_anb(3, 2, 1, largura=420, altura=72, base="não dividiu: 3 × 1"),
    ]
    itens_metodo = [DEFINICAO, base_txt, "Limite: classificação por IA dos textos publicados, numa única leitura. "
                    "Enquadramento é escolha do que destacar, não erro."]
    slides.append(
        Slide(
            "placar_metodo",
            {
                "selo": selo,
                "kicker": "Como medimos",
                "titulo": "O que conta como dividir".upper(),
                "destaque": "",
                "exemplos": [svg for svg, _ in exemplos],
                "itens": itens_metodo,
                "cta": CTA_PLACAR,
                "nota": nota,
            },
            alt=alt("Como medimos: o que conta como dividir. " + " ".join(d for _, d in exemplos) + " "
                    + " ".join(itens_metodo) + f" {CTA_PLACAR}"),
        )
    )

    por_secao = " · ".join(
        f"{SECAO_CURTA.get(sid, section_info(edition, sid)['title'])} {r} de {b}"
        for sid, (r, b) in sorted(p.por_secao.items(), key=lambda kv: (-kv[1][1], kv[0]))
    )
    linha1 = (f"Dia de consenso: {p.texto_dia} assuntos com 4 ou mais veículos dividiram a cobertura hoje."
              if consenso else f"Placar do dia: {p.texto_dia} assuntos com 4 ou mais veículos dividiram a cobertura hoje.")
    caption = _legenda(
        linha1,
        [linha2],
        [linha3, f"▸ Por editoria: {por_secao}." if por_secao else ""],
        ["Dividir = pelo menos 2 veículos de cada lado. " + METODO, CTA_PLACAR,
         hashtags("#jornalismo", secoes, election=election)],
    )
    return Peca(
        slot=SLOT,
        kind="carousel",
        title="Placar do dia",
        slides=slides,
        caption=caption,
        story_ids=story_ids,
        serie=SERIE,
        numero_serie=numero_serie,
        formula="consenso" if consenso else "placar",
    )

