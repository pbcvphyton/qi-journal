"""Stories da manhã (07:10, §4.6): 4 telas de 1080 × 1920, sem clima.

1. Bom dia: a data, o número vivo do dia ("Hoje lemos 1.640 notícias de 46 fontes") e a
   agenda só com o que foi gerado.
2. Manchete: a do dia ou, no modo eleitoral, a primeira que não é política nem eleitoral;
   número em destaque, "por que importa", selo de cobertura e fontes.
3. Mercado: valor, variação do dia com a hora da leitura e o acumulado desde o início da série.
4. Chamada para o carrossel da manhã.

Os stories não levam link nem figurinha (limite da API). Tudo sai da edição, do histórico e
das contas de :mod:`..estatistica`; nada de IA.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from qijournal.models import Edition, Story

from .. import estatistica as E
from .. import graficos as G
from ..conteudo import (
    DIAS,
    Peca,
    Slide,
    alt,
    edition_day,
    is_political,
    kicker_date,
    plain,
    section_info,
    short_date,
    source_names,
    sources_line,
    trend,
)
from ..redacao import palavras_proibidas, verbos_proibidos
from ..selecao import ordered_stories
from .mercado import faixa_juros

SLOT = "stories"
TITULO = "Stories da manhã"
IDS_MERCADO = ["usd", "ibov", "spx", "brent", "btc"]  # a Selic vai na faixa de juros, com o IPCA e o juro real
SECOES_FIM = {"esporte", "variedades"}  # só viram a tela da manchete se faltar matéria
NUM_MAX, NUM_MIN = 240, 180  # px do número em destaque da manchete (o data-fit ainda encolhe até 140)
NOTA_MANCHETE = "resumo por IA das fontes citadas"
NOTA_MERCADO = "fonte: Yahoo Finance e BCB"
RODAPE_MERCADO = "Horário de Brasília · fech. = fechamento do pregão · acumulado ponta a ponta desde a 1ª leitura da série"

# O nome curto de cada formato na agenda e o que ele traz (texto fixo, sempre verdadeiro).
NOME_AGENDA = {"Raio-X da cobertura": "Raio-X"}
DESCRICAO_AGENDA = {
    "Em 1 minuto": "os fatos da manhã e o mercado",
    "Terminal da manhã": "os indicadores do mercado",
    "Número do dia": "um número, a prova e o método",
    "Raio-X": "como cada veículo contou o mesmo fato",
    "Placar do dia": "quantos assuntos dividiram a cobertura",
    "Mercado em linha": "4 ativos, a série e os juros",
}
SLOTS_FEED = ("manha", "numero", "noite")

# Reforço local do filtro eleitoral para a tela da manchete: "eleição" e "eleitor" fora de Mundo
# (o is_electoral não pega "Eleição presidencial deixa fatura fiscal…", 25/10, em Brasil).
_ELEICAO = re.compile(r"\belei(?:ç|c)(?:ão|ao|ões|oes)\b|\beleitor(?:es|as?|ado)?\b", re.I)


# ── Agenda ────────────────────────────────────────────────────────────────


def hora_curta(hhmm: str) -> str:
    """``"07:00"`` → ``"07h"``; ``"18:30"`` → ``"18h30"``."""
    h, _, m = (hhmm or "").partition(":")
    return f"{int(h):02d}h" + (m if m and m != "00" else "") if h.isdigit() else hhmm


def agenda_das_pecas(pecas: Iterable[Peca], horarios: Mapping[str, str]) -> list[tuple[str, str]]:
    """A agenda do dia com as peças de feed que de fato foram geradas, em ordem de horário.

    ``horarios``: {slot: "HH:MM"} do dia (o horário-alvo de cada slot, já com o fim de semana).
    """
    itens = []
    for p in pecas:
        if p.kind == "story" or p.slot not in SLOTS_FEED or p.slot not in horarios:
            continue
        itens.append((horarios[p.slot], NOME_AGENDA.get(p.title, p.title)))
    return [(hora_curta(h), nome) for h, nome in sorted(itens)]


def _agenda_ctx(agenda: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"hora": h, "nome": n, "desc": DESCRICAO_AGENDA.get(n, "")} for h, n in agenda]


# ── Tela 1: bom dia ───────────────────────────────────────────────────────


def _bom_dia(edition: Edition, agenda: Sequence[tuple[str, str]]) -> Slide:
    day = edition_day(edition)
    artigos = edition.stats.articles_collected
    fontes = edition.rationale.outlets if edition.rationale else 0
    # "fontes": a contagem do rationale inclui órgãos públicos (§4.0.2)
    titulo = f"Hoje lemos {E.num(artigos)} notícias" + (f" de {fontes} fontes" if fontes else "")
    destaque = f"{E.num(artigos)} notícias"
    funil = []
    if edition.index:
        funil.append(f"{E.num(len(edition.index))} assuntos")
    if edition.stories:
        funil.append(f"{len(edition.stories)} matérias na edição")
    itens = _agenda_ctx(agenda)
    selo = f"Bom dia · {DIAS[day.weekday()]}, {short_date(day)}"
    ctx = {
        "selo": selo,
        "titulo": titulo,
        "destaque": destaque,
        "tam": G.tamanho_gancho(titulo, max_linhas=4, tam_max=176, tam_min=112),
        "funil": "→ " + " → ".join(funil) if funil else "",
        "agenda": itens,
        "nota": "",
        "section": None,
    }
    agenda_txt = " · ".join(f"{i['hora']} {i['nome']}" for i in itens)
    texto = f"{selo}. {titulo}." + (f" {' → '.join([destaque, *funil])}." if funil else "") + (f" Hoje no feed: {agenda_txt}." if itens else "")
    return Slide("st_bomdia", ctx, alt=alt(texto))


# ── Tela 2: manchete ──────────────────────────────────────────────────────


def _texto_ok(texto: str, secao: str, *, election: bool, nomes: Iterable[str]) -> bool:
    """Texto que pode ir para a tela: sem palavra proibida do lint e, no modo eleitoral, sem
    léxico eleitoral."""
    if not texto or palavras_proibidas(texto) or verbos_proibidos(texto):
        return False
    if election and (E.is_electoral(texto, secao, nomes) or (secao != "mundo" and _ELEICAO.search(texto))):
        return False
    return True


def textos_manchete(edition: Edition, story: Story, *, election: bool, nomes: Iterable[str]) -> tuple[str, str] | None:
    """O título (a manchete depois do adjetivo da casa) e o "por que importa" da tela 2, ou None se a
    matéria não serve: política ou eleitoral no modo eleitoral, palavra proibida no título, ou sem uma
    segunda frase limpa (o "por que importa" ou, na falta, a linha fina; nunca igual ao título, sem
    adjetivo da casa e sem palavra proibida)."""
    nomes = tuple(nomes)
    if election and is_political(story, f"{story.headline} {story.dek}", nomes):
        return None
    if election and story.section != "mundo" and _ELEICAO.search(plain(f"{story.headline} {story.dek}")):
        return None
    titulo = E.capa_sem_adj(edition, story)
    if not _texto_ok(titulo, story.section, election=election, nomes=nomes):
        return None
    for frase in (plain(story.why_it_matters), plain(story.dek)):
        # a frase não passa pelo corte do adjetivo da casa: com "recorde", "histórico"…, fica de fora
        if frase and E._fold(frase) != E._fold(titulo) and not E.ADJ_CASA.search(frase) and _texto_ok(
            frase, story.section, election=election, nomes=nomes
        ):
            return titulo, frase
    return None


def escolher_manchete(
    edition: Edition,
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    capa: Story | None = None,
) -> Story | None:
    """A matéria da tela 2: ``capa`` (a da capa do Em 1 minuto, se o gerador passar) ou a manchete;
    no modo eleitoral, a primeira na ordem editorial que não é política, pesquisa nem eleitoral.
    Esporte e variedades só se faltar outra. Fica de fora a que :func:`textos_manchete` recusa.
    Sem IA, None: a manchete da edição automática é o título literal de um veículo."""
    if edition.mode != "ai":
        return None
    nomes = tuple(nomes)
    if capa is not None and textos_manchete(edition, capa, election=election, nomes=nomes):
        return capa
    pool = ordered_stories(edition)
    for fim in (False, True):
        for story in pool:
            if (story.section in SECOES_FIM) == fim and textos_manchete(edition, story, election=election, nomes=nomes):
                return story
    return None


def _manchete(
    edition: Edition,
    story: Story,
    *,
    election: bool,
    nomes: tuple[str, ...],
    raiox_story_id: str | None,
) -> Slide:
    day = edition_day(edition)
    textos = textos_manchete(edition, story, election=election, nomes=nomes)
    titulo, por_que = textos if textos else (E.capa_sem_adj(edition, story), "")
    numero = E.numero_destaque(titulo)
    qual, valor = numero if numero else ("", "")
    c = story.coverage
    a, n, b = (c.count("a"), sum(1 for o in c.outlets if o.stance not in ("a", "b")), c.count("b")) if c else (0, 0, 0)
    selo_cob = E.selo_cobertura(edition, story, raiox_story_id)
    fontes = sources_line(source_names(story))
    secao = section_info(edition, story.section)
    rotulo = "Manchete" if story.id == edition.lead else "Destaque"
    ctx = {
        "selo": f"{rotulo} · {kicker_date(day)}",
        "secao": secao["title"],
        "section": secao,
        "qual": qual,
        "numero": valor,
        "destaque": valor,
        "tam_num": tamanho_numero(valor) if valor else 0,
        "titulo": titulo,
        "tam_titulo": _tamanho_titulo(titulo, com_numero=bool(valor)),
        "por_que": por_que,
        "rotulo_por_que": "Por que importa" if por_que and por_que == plain(story.why_it_matters) else "O fato",
        "selo_cob": selo_cob,
        "a": a,
        "n": n,
        "b": b,
        "fontes": fontes,
        "nota": NOTA_MANCHETE,
    }
    partes = [
        f"{rotulo} da edição, {short_date(day)}, {secao['title']}.",
        f"{qual} {valor}.".strip() if valor else "",
        f"{titulo}.",
        f"{ctx['rotulo_por_que']}: {por_que}" if por_que else "",
        f"{selo_cob}." if selo_cob else "",
        f"Fontes: {', '.join(source_names(story))}." if fontes else "",
    ]
    return Slide("st_manchete", ctx, alt=alt(" ".join(p for p in partes if p)))


def tamanho_numero(valor: str) -> int:
    """O px do número da tela 2: o maior (até 240) em que ele ocupa até 880 px de largura; nunca abaixo de 180."""
    por_px = G.largura_texto(valor.upper(), "hook", 100) / 100
    return int(max(NUM_MIN, min(NUM_MAX, 880 / por_px))) if por_px else NUM_MAX


def _tamanho_titulo(titulo: str, *, com_numero: bool) -> int:
    """Manchete em Noto Serif 800: o maior tamanho em que cabe em 4 linhas com o número na tela,
    ou em 5 sem ele (até 104 px)."""
    for tam in ((92, 86, 80, 74, 68, 64) if com_numero else (104, 98, 92, 86, 80, 74, 68, 64)):
        # Noto Serif 800 tem ~0,55 em de largura média em caixa baixa
        por_linha = 936 / (tam * 0.55)
        linhas = -(-len(titulo) // int(por_linha))
        if linhas <= (4 if com_numero else 5):
            return tam
    return 64


# ── Tela 3: mercado ───────────────────────────────────────────────────────


def _linha_mercado(edition: Edition, history: Sequence[Edition], qid: str) -> dict[str, Any] | None:
    q = next((x for x in edition.quotes if x.id == qid), None)
    if q is None or not q.display:
        return None
    day = edition_day(edition)
    hoje = E.ponto_da_cotacao(q)
    selo = E.selo_leitura(qid, hoje, day) if hoje is not None else ""
    mostra = bool(selo) and q.change_pct is not None  # leitura sem variação (o dólar às 09h00) não mostra variação
    # uma leitura por dia, terminando na de hoje; leitura que não serve de dado não vira ponta
    pontos = [p for p in E.serie([*history, edition], qid) if not E.stale_reading(p)]
    acc = E.acumulado(pontos)
    linha: dict[str, Any] = {
        "id": qid,
        "nome": q.label,
        "valor": q.display,
        "change": E.sinal(q.change_pct, 2) if mostra else "",
        "trend": trend(q.change_pct) if mostra else "",
        "selo": selo or "leitura sem variação",
        "acum": "",
        "acum_trend": "",
        "desde": "",
        "ate": "",
    }
    if acc is not None:
        linha.update(acum=E.sinal(acc.pct), acum_trend=trend(acc.pct), desde=f"{acc.first.day:%d/%m}")
        if hoje is None or acc.last.as_of != hoje.as_of:
            linha["ate"] = E.selo_leitura(qid, acc.last, day).removeprefix("leitura de ").removeprefix("leitura das ")
    return linha


def _titulo_mercado(edition: Edition) -> str:
    """Em dia útil de pregão, "antes da abertura" (o story sai às 07:10); no fim de semana e no
    feriado da B3, "as últimas leituras"."""
    day = edition_day(edition)
    if day.weekday() < 5 and day not in E.FERIADOS["ibov"]:
        return "Mercado antes da abertura"
    return "Mercado: as últimas leituras"


def _mercado(edition: Edition, history: Sequence[Edition]) -> Slide | None:
    linhas = [x for x in (_linha_mercado(edition, history, qid) for qid in IDS_MERCADO) if x]
    if not linhas:
        return None
    day = edition_day(edition)
    desdes = {x["desde"] for x in linhas if x["desde"]}
    # a data do início vai no cabeçalho quando é a mesma para todos; senão, em cada linha
    cabecalho = f"desde {desdes.pop()}" if len(desdes) == 1 else "acumulado"
    titulo = _titulo_mercado(edition)
    faixa = faixa_juros(edition)
    ctx = {
        "selo": f"Mercado · {kicker_date(day)}",
        "titulo": titulo,
        "tam": G.tamanho_gancho(titulo, max_linhas=2, tam_max=132, tam_min=100),
        "col_dia": "no dia",
        "col_acum": cabecalho,
        "por_linha": cabecalho == "acumulado",
        "linhas": linhas,
        "faixa": faixa,
        "pe": RODAPE_MERCADO,
        "nota": NOTA_MERCADO,
        "section": "mercados",
    }
    frases = []
    for x in linhas:
        partes = [f"{x['change']} no dia, {x['selo']}" if x["change"] else x["selo"]]
        if x["acum"]:
            partes.append(f"{x['acum']} desde {x['desde']}" + (f" (até {x['ate']})" if x["ate"] else ""))
        frases.append(f"{x['nome']} {x['valor']} ({'; '.join(partes)})")
    texto = f"{titulo}, {short_date(day)}. " + ". ".join(frases) + "." + (f" {faixa[0].upper()}{faixa[1:]}." if faixa else "")
    return Slide("st_mercado", ctx, alt=alt(texto))


# ── Tela 4: chamada para o carrossel ──────────────────────────────────────


def _chamada(edition: Edition, agenda: Sequence[tuple[str, str]]) -> Slide:
    day = edition_day(edition)
    hora = next((h for h, n in agenda if n == "Em 1 minuto"), "")
    titulo = "Os fatos da manhã e o mercado, no perfil"
    ctx = {
        "selo": f"Em 1 minuto · {kicker_date(day)}",
        "kicker": f"No feed às {hora}" if hora else "No feed",
        "titulo": titulo,
        "destaque": "fatos da manhã",
        "tam": G.tamanho_gancho(titulo, max_linhas=4, tam_max=164, tam_min=112),
        "cta": "Toque no perfil",
        "nota": "",
        "section": None,
    }
    texto = f"Em 1 minuto: {titulo}" + (f", às {hora}" if hora else "") + ". Toque no perfil."
    return Slide("st_feed", ctx, alt=alt(texto))


# ── A peça ────────────────────────────────────────────────────────────────


def stories_manha(
    edition: Edition,
    brand: dict[str, Any],
    history: Sequence[Edition],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    agenda: Sequence[tuple[str, str]] = (),
    capa: Story | None = None,
    raiox_story_id: str | None = None,
) -> Peca | None:
    """A sequência de stories da manhã (até 4 telas); None com menos de 2.

    - ``history``: :func:`.estatistica.load_history` com o dia (para o acumulado do mercado).
    - ``agenda``: [(hora, formato)] só do que foi gerado, ex. ``[("07h", "Em 1 minuto"),
      ("12h", "Número do dia"), ("18h30", "Raio-X")]`` (:func:`agenda_das_pecas`).
    - ``capa``: a matéria da capa do Em 1 minuto, para a tela 2 contar a mesma.
    - ``raiox_story_id``: a matéria do Raio-X da noite (o selo de cobertura ganha "Raio-X às 18h30").

    A chamada para o carrossel só sai com o Em 1 minuto na agenda (sem agenda, em dia com IA).
    """
    nomes = tuple(nomes)
    slides = [_bom_dia(edition, agenda)]
    story = escolher_manchete(edition, election=election, nomes=nomes, capa=capa)
    if story is not None:
        slides.append(_manchete(edition, story, election=election, nomes=nomes, raiox_story_id=raiox_story_id))
    mercado = _mercado(edition, history)
    if mercado is not None:
        slides.append(mercado)
    tem_em1min = any(n == "Em 1 minuto" for _, n in agenda) if agenda else edition.mode == "ai"
    if tem_em1min and edition.mode == "ai":
        slides.append(_chamada(edition, agenda))
    if len(slides) < 2:
        return None
    return Peca(
        slot=SLOT,
        kind="story",
        title=TITULO,
        slides=slides,
        story_ids=[story.id] if story is not None else [],
    )
