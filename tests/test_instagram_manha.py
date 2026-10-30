"""Em 1 minuto 2.0 (§5.4), com as edições reais de data/."""

from __future__ import annotations

import functools
import json
import os
from datetime import date
from pathlib import Path

import pytest

from qijournal.config import load_config
from qijournal.instagram import estatistica as E
from qijournal.instagram import gerar, redacao
from qijournal.instagram.conteudo import Peca, is_electoral
from qijournal.instagram.formatos import manha as MH
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-29.json").is_file(), reason="sem os JSONs reais em data/")
pytestmark = real

NOMES = ("Lula", "Flávio Bolsonaro", "Bolsonaro")
DIAS_IA = range(14, 30)
ELEICAO_ATE = 27  # election_until = 2026-10-27


@functools.lru_cache(maxsize=None)
def historico(day: int) -> tuple[Edition, ...]:
    return tuple(E.load_history(DATA, date(2026, 10, day)))


def edicao(day: int) -> Edition:
    return next(e for e in historico(day) if e.date == f"2026-10-{day:02d}")


def eleicao(day: int) -> bool:
    return day <= ELEICAO_ATE


@functools.lru_cache(maxsize=None)
def peca(day: int, numero_serie: int = 31) -> Peca | None:
    return MH.em_1_minuto(edicao(day), {}, list(historico(day)), election=eleicao(day), nomes=NOMES, numero_serie=numero_serie)


def fatos(p: Peca):
    return [s for s in p.slides if s.template == "m1_fato"]


def capa(p: Peca):
    return p.slides[0]


def materia(e: Edition, prefixo: str):
    return next(s for s in e.stories if s.id.startswith(prefixo))


def veiculos(e: Edition) -> list[str]:
    return sorted({o.name for s in e.stories if s.coverage for o in s.coverage.outlets})


# ── 21/10: a capa sem adjetivo, o qualificador, o slide 2 e o mercado ──────


def test_21_10_capa_sem_gigante_com_cerca_de():
    p = peca(21)
    c = capa(p).ctx
    assert c["manchete"] == "iFood Benefícios compra a Caju por cerca de R$ 1 bi"
    assert "gigante" not in c["manchete"].lower()
    assert (c["qual"], c["valor"]) == ("CERCA DE", "R$ 1 bi")
    assert c["selo"] == "EM 1 MINUTO Nº 031 · QUA 21 OUT"
    assert c["faixa"] == "1.640 notícias · 46 fontes · 1.159 assuntos"
    assert c["mais_coberto"] == {"nome": "SpaceX e Tesla", "k": 12, "proprio": False}
    assert "dek" not in c and "linha_fina" not in c  # sem linha fina na capa


def test_21_10_slide_2_nao_repete_a_capa():
    p = peca(21)
    manchete = capa(p).ctx["manchete"]
    s2 = fatos(p)[0].ctx
    assert s2["frase"].rstrip(".") != manchete
    assert redacao.similaridade(s2["frase"], manchete) < MH.JACCARD_CAPA
    assert s2["valor"] != "R$ 1 bi"  # nem o número da capa
    assert s2["relogio"][:3] == [
        {"nome": "Folha de S.Paulo", "hora": "20/10 15h57"},
        {"nome": "g1", "hora": "16h00"},
        {"nome": "NeoFeed", "hora": "16h35"},
    ]
    assert s2["relogio_mais"] == 5


def test_21_10_nenhum_fato_repete_o_mercado_nem_esporte():
    p = peca(21)
    mercado = next(s for s in p.slides if s.template == "mercado_linha")
    paineis = mercado.ctx["paineis"]
    ibov = next(x for x in paineis if x["id"] == "ibov")
    assert ibov["change"] == "+1,57%"
    for s in fatos(p):
        assert not MH._repete_mercado([s.ctx["frase"]], paineis)
        assert "1,57" not in s.ctx["frase"] and "Ibovespa" not in s.ctx["frase"]
        assert "Vasco" not in s.ctx["frase"] and s.ctx["section"] != "esporte"
    assert all("Vasco" not in s.alt for s in p.slides)
    assert not any(sid.startswith("vasco") or sid.startswith("ibovespa") for sid in p.story_ids)


def test_21_10_estrutura_e_legenda():
    p = peca(21)
    assert (p.slot, p.kind, p.serie, p.numero_serie) == ("manha", "carousel", "em1min", 31)
    assert [s.template for s in p.slides] == ["m1_capa"] + ["m1_fato"] * 5 + ["mercado_linha", "m1_fechamento"]
    assert p.slides[6].ctx["selo"] == "EM 1 MINUTO Nº 031" and p.slides[6].ctx["contador"] is True
    assert p.slides[6].ctx["tema"] == "t-dark"
    linhas = p.caption.split("\n")
    assert linhas[0] == "iFood Benefícios compra a Caju por cerca de R$ 1 bi · e mais 4 fatos da quarta"
    assert sum(1 for x in linhas if x.startswith("▸ ")) == 5
    assert "▸ A operação combina plataformas que somam 3 milhões de usuários" in p.caption
    assert "(8 veículos · sem divisão)" in p.caption
    assert "Resumo por IA a partir de 1.640 notícias de 46 fontes." in p.caption
    assert MH.CTA in p.caption
    tags = linhas[-1].split()
    assert 3 <= len(tags) <= 4 and tags[:2] == ["#triangula", "#noticias"]
    fim = p.slides[-1].ctx
    assert (fim["valor"], fim["frase"]) == ("1.039", "de 1.159 assuntos de hoje (90%) saíram em uma fonte só.")
    assert fim["cta"] == MH.CTA and "link na bio" not in p.slides[-1].alt  # um CTA só


def test_21_10_raiox_da_noite_nao_vira_capa_e_ganha_o_selo():
    e = edicao(21)
    spacex = materia(e, "spacex")
    p = MH.em_1_minuto(e, {}, list(historico(21)), election=True, nomes=NOMES, numero_serie=31, raiox_story_id=spacex.id)
    assert capa(p).ctx["manchete"].startswith("iFood")
    s = next(s for s in fatos(p) if "SpaceX" in s.ctx["frase"])
    assert "Raio-X às 18h30" in s.ctx["cobertura"] and "Raio-X às 18h30" in s.alt
    assert "12 veículos · dividida 6 × 2 · Raio-X às 18h30" in p.caption


def test_numero_do_dia_nao_vira_capa_nem_fato_e_tira_o_ativo_do_titulo():
    e = edicao(21)
    ifood = materia(e, "ifood")
    numero = Peca(slot="numero", kind="carousel", title="Número do dia", slides=[], caption="",
                  story_ids=[ifood.id], chave="acum:ibov")
    p = MH.em_1_minuto(e, {}, list(historico(21)), election=True, nomes=NOMES, numero_serie=31, numero=numero)
    assert ifood.id not in p.story_ids and not capa(p).ctx["manchete"].startswith("iFood")
    mercado = next(s for s in p.slides if s.template == "mercado_linha")
    assert not mercado.ctx["titulo"].startswith("Ibovespa")


# ── 26 e 27/10: período eleitoral ──────────────────────────────────────


def politica(section: str | None, texto: str) -> bool:
    """O item ocupa a vaga de política do período eleitoral (a seção, ou eleição de outro país)?"""
    return section == "politica" or bool(MH._ELEICAO.search(texto) or MH._URNAS.search(texto))


@pytest.mark.parametrize("dia", [26, 27])
def test_periodo_eleitoral_capa_nunca_eleitoral_e_politica_por_ultimo(dia):
    e = edicao(dia)
    sel = MH.selecionar(e, election=True, nomes=NOMES)
    assert sel is not None
    assert not MH.eleitoral_capa(sel.capa, [sel.manchete], NOMES)
    assert not is_electoral(sel.capa, sel.manchete, NOMES)
    pol = [politica(f.story.section if f.story else None, f.texto) for f in sel.fatos[1:]]
    assert sum(pol) <= 1
    if any(pol):
        assert pol[-1]
    for f in sel.fatos:
        assert not MH._eleitoral(f.story, [f.texto], NOMES)


def test_26_10_so_o_bola_de_ouro_escapa_e_o_07h_vira_terminal():
    sel = MH.selecionar(edicao(26), election=True, nomes=NOMES)
    assert sel.capa.section == "esporte" and len(sel.fatos) < MH.FATOS_MIN
    assert peca(26) is None


def test_27_10_capa_nao_e_a_eleicao_de_israel():
    e = edicao(27)
    israel = materia(e, "israel-vai-as-urnas")
    assert MH.eleitoral_capa(israel, [], NOMES)
    p = peca(27)
    c = capa(p).ctx
    assert c["manchete"] == "Itaú Unibanco anuncia a aquisição total da startup NeoSpace por R$ 2,925 bilhões"
    assert c["valor"] == "R$ 2,925 bi"
    assert c["mais_coberto"] is None  # o mais coberto do dia é a eleição de Israel: a linha sai
    for s in p.slides:
        assert not E.is_electoral(s.alt, s.ctx.get("section"), NOMES)
    # a eleição de Israel fica, como fato de Mundo, na vaga única de política: por último
    assert fatos(p)[-1].ctx["frase"].startswith("Israel vai às urnas")
    assert "Ibovespa" not in " ".join(s.ctx["frase"] for s in fatos(p))  # o circuit breaker está no slide 7


def test_eleicao_de_outro_pais_so_na_linha_fina_nao_tira_a_capa():
    trump = materia(edicao(18), "trump-suspende")
    assert "eleições" in trump.dek and not MH.eleitoral_capa(trump, [], NOMES)
    assert capa(peca(18)).ctx["manchete"].startswith("Trump suspende sanções")


# ── 29/10: o mesmo assunto duas vezes no briefing ─────────────────────────


def test_29_10_sem_fato_duplicado():
    e = edicao(29)
    p = peca(29)
    flavio = [s.id for s in e.stories if s.id.startswith("flavio-bolsonaro-desembarca")]
    assert len(flavio) == 2 and sum(1 for b in e.briefing if "Flávio Bolsonaro desembarca" in b) == 2
    assert sum(1 for sid in p.story_ids if sid in flavio) == 1
    frases = [s.ctx["frase"] for s in fatos(p)]
    textos = [capa(p).ctx["manchete"], *frases]
    for i, a in enumerate(textos):
        for b in textos[i + 1:]:
            if a is textos[0] and b is frases[0]:
                continue  # o slide 2 é a matéria da capa, com outro texto
            assert not MH.mesmo_assunto(a, b), (a, b)
    assert len(set(p.story_ids)) == len(p.story_ids) == 5
    assert sum(1 for f in frases if f.startswith("Fed mantém")) <= 1
    assert sum(1 for f in frases if "trens" in f) <= 1


def test_29_10_verbo_neutro_e_ressalva_ao_menos():
    p = peca(29)
    alphabet = next(s.ctx for s in fatos(p) if "Alphabet" in s.ctx["frase"])
    assert alphabet["frase"].startswith("Ações da Alphabet caem até 10%")
    assert (alphabet["qual"], alphabet["valor"]) == ("ATÉ", "10%")
    trens = next(s.ctx for s in fatos(p) if "trens" in s.ctx["frase"])
    assert (trens["qual"], trens["valor"]) == ("AO MENOS", "68 pessoas")


# ── Sem IA e outras regras ──────────────────────────────────────────────


def test_edicao_sem_ia_devolve_none():
    e = Edition.from_dict(json.loads((DATA / "2026-10-13.json").read_text(encoding="utf-8")))
    assert e.mode == "heuristic"
    assert MH.em_1_minuto(e, {}, [e], election=True, nomes=NOMES, numero_serie=1) is None


@pytest.mark.parametrize("dia", [d for d in DIAS_IA if d != 26])
def test_lint_e_regras_em_todos_os_dias(dia):
    e = edicao(dia)
    p = peca(dia)
    assert p is not None and len(p.slides) == 8
    assert redacao.lint(p, election=eleicao(dia), nomes=NOMES, veiculos=veiculos(e)) == []
    assert redacao.lint(p, election=False, nomes=NOMES, veiculos=veiculos(e)) == []
    c = capa(p).ctx
    assert len(c["manchete"]) <= MH.MANCHETE_MAX and not MH._adj_sobrando(c["manchete"], MH._titulos(e, materia(e, p.story_ids[0])))
    if eleicao(dia):
        assert not MH.eleitoral_capa(materia(e, p.story_ids[0]), [c["manchete"]], NOMES)
        pol = [politica(s.ctx["section"], s.ctx["frase"]) for s in fatos(p)[1:]]
        assert sum(pol) <= 1 and (not any(pol) or pol[-1])
    if c["mais_coberto"]:
        assert c["mais_coberto"]["k"] >= MH.MAIS_COBERTO_MIN
    for s in fatos(p):
        x = s.ctx
        assert x["frase"].endswith((".", "!", "?"))
        if x["heroi"] == "cobertura":
            assert x["qual"] == MH.NOTICIADO and int(x["valor"].split()[0]) >= MH.COBERTURA_MIN
        if x["valor"]:
            assert x["tam"] >= MH.NUM_FATO_MIN and MH.px_cabe(x["valor"]) >= MH.NUM_FATO_MIN
        else:
            assert x["solo"] and x["tam_frase"] >= 60
    linha1 = p.caption.split("\n", 1)[0]
    assert len(linha1) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX


def test_selos_e_fatos_de_varios_dias():
    # 19/10: o slide 2 não repete o número da capa nem por outra palavra ("2.400 presos" / "2.400 detidos")
    p = peca(19)
    assert capa(p).ctx["valor"] == "2.400 presos"
    assert "2.400" not in fatos(p)[0].ctx["valor"]
    # 17/10: contagem que não cabe numa linha perde o substantivo, que está na frase
    p = peca(17)
    americanas = next(s.ctx for s in fatos(p) if "ex-executivos" in s.ctx["frase"])
    assert americanas["valor"] == "12"
    # 16/10: no período eleitoral, a política (não eleitoral) entra uma vez e por último
    p = peca(16)
    assert fatos(p)[-1].ctx["section"] == "politica"


# ── Peças de texto ──────────────────────────────────────────────────────


def test_numero_fato_contagem_qualificador_e_nomes():
    assert MH.numero_fato("Colisão de trens mata ao menos 68 pessoas na Espanha") == ("AO MENOS", "68 pessoas")
    assert MH.numero_fato("Mais de 7 milhões de israelenses votam") == ("MAIS DE", "7 milhões")
    assert MH.numero_fato("Emenda Constitucional 141 reduz jornada para 40 horas") is None
    assert MH.numero_fato("Abstenção registra menor patamar em 20 anos") is None
    assert MH.numero_fato("França mobiliza 70 mil policiais") == ("", "70 mil")  # não cabe a 180 px
    assert MH.numero_fato("Protestos deixam 2.400 presos", minimo_px=MH.NUM_CAPA_MIN) == ("", "2.400 presos")
    assert MH.numero_fato("iFood compra a Caju por cerca de R$ 1 bilhão", evitar=["R$ 1 bi"]) is None
    assert MH.numero_capa("iFood Benefícios compra a Caju por cerca de R$ 1 bi e cria gigante") == ("CERCA DE", "R$ 1 bi")
    assert MH.numero_capa("Azul fecha financiamento de pelo menos R$ 2 bilhões") == ("PELO MENOS", "R$ 2 bi")
    assert MH.numero_fato("o planeta caminha para um aquecimento de 2,8°C") == ("", "2,8°C")


def test_verbo_neutro_so_antes_de_numero():
    assert MH.neutro("Ações da Alphabet desabam até 10% com queima de caixa") == "Ações da Alphabet caem até 10% com queima de caixa"
    assert MH.neutro("Ibovespa despenca 1,15% com cautela") == "Ibovespa cai 1,15% com cautela"
    assert MH.neutro("Ações da Jio disparam mais de 21% em estreia") == "Ações da Jio sobem mais de 21% em estreia"
    assert MH.neutro("Neymar é expulso e dispara contra Nestor") == "Neymar é expulso e dispara contra Nestor"


def test_mesmo_assunto():
    assert MH.mesmo_assunto(
        "Fed mantém taxa de juros inalterada e projeta retomada de altas em dezembro",
        "Fed mantém juros na faixa de 3,75% a 4% ao ano em votação com duas dissidências",
    )
    assert MH.mesmo_assunto(
        "Colisão entre trens de alta velocidade mata ao menos 68 pessoas na Espanha",
        "Colisão de trens de alta velocidade na Espanha deixa vítimas e abre crise sobre segurança",
    )
    assert not MH.mesmo_assunto(
        "DeepSeek levanta US$ 15 bilhões em rodada de financiamento e dobra meta de captação",
        "OpenAI negocia rodada de até US$ 30 bilhões a valuation de US$ 1,25 trilhão antes de IPO",
    )


def test_linha1_corta_a_manchete_e_mantem_os_fatos():
    linha = MH.linha1("Mercados financeiros devolvem parte do estresse com ajuda do Tesouro e falas de Galípolo", 4,
                      date(2026, 10, 28))
    assert linha == "Mercados financeiros devolvem parte do estresse com ajuda do Tesouro · e mais 4 fatos da quarta"
    assert MH.linha1("Curta", 4, date(2026, 10, 24)) == "Curta · e mais 4 fatos do sábado"


def test_tamanhos_enchem_o_quadro_sem_palavra_solta():
    area = MH.area_fato(relogio=False, selo=True, fontes=True)
    n, f = MH.tamanhos_fato("0,32%", "", "IPCA-15 sobe 0,32% em outubro com alívio tarifário, mas alimentos seguem pressionados.", area)
    assert n == MH.NUM_FATO_MAX and f == MH.FRASE_TAMS[0]
    n, f = MH.tamanhos_fato("", "", "STJ fixa novos critérios temporais para indenizações em desapropriações.", area)
    assert n == 0 and f < MH.SOLO_TAMS[0]
    assert not MH._palavra_solta("STJ fixa novos critérios temporais para indenizações em desapropriações.", f, MH.SERIF_800)


# ── Render (só com o Chromium do Playwright: QIJ_CHROMIUM) ────────────────


@pytest.mark.skipif(not os.environ.get("QIJ_CHROMIUM"), reason="sem Chromium (QIJ_CHROMIUM)")
@pytest.mark.parametrize("dia", [21, 24, 27, 29])
def test_render_sem_texto_cortado(dia, tmp_path):
    from qijournal.instagram import conteudo
    from qijournal.instagram.render import Renderer

    config = load_config(ROOT)
    env = gerar.environment()
    brand = gerar.brand_context(config)
    p = MH.em_1_minuto(edicao(dia), brand, list(historico(dia)), election=eleicao(dia), nomes=NOMES, numero_serie=31)
    with Renderer() as r:
        for i, s in enumerate(p.slides, 1):
            html = env.get_template(f"{s.template}.html.j2").render(
                brand=brand, s=conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
            )
            res = r.render(html, tmp_path / f"{i:02d}.jpg", width=1080, height=1350)
            assert res.transbordos == [], (i, [str(t) for t in res.transbordos])
