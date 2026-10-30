"""A publicação da noite: Raio-X 2.0 (§4.2) e Placar do dia (§4.3), com as edições reais de data/."""

from __future__ import annotations

import dataclasses
import functools
import json
import re
from datetime import date
from pathlib import Path

import pytest

from qijournal.config import LimiaresConfig, load_config
from qijournal.instagram import estatistica as E
from qijournal.instagram import gerar, redacao
from qijournal.instagram import selecao as S
from qijournal.instagram.formatos import noite as N
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-26.json").is_file(), reason="sem os JSONs reais em data/")
pytestmark = real

DIAS_IA = range(14, 26)  # 14 a 25/10: edições com IA, todas no período eleitoral
ESCOLHAS = {  # §4.9, item 3 (prefixo do id da matéria)
    14: "restricoes-na-starlink",
    15: "papa-leao-xiv",
    16: "ucrania-retoma-ataques",
    17: "deficit-fiscal-dos-estados",
    18: "trump-suspende-sancoes",
    19: None,  # Placar do dia
    20: "governo-declara-emergencia",
    21: "spacex-e-tesla",
    22: "stf-decide-que-iss",
    23: "bradesco",
    24: "ipca-15",
    25: "musk-acusa-ucrania",
}


@functools.lru_cache(maxsize=None)
def edicao(day: int, month: int = 10) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-{month:02d}-{day:02d}.json").read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def noites() -> dict[int, object]:
    """A peça da noite de 14 a 25/10 como o gerador faria: Raio-X (com as entidades dos 3 dias
    anteriores) ou Placar do dia; a série começa em 001 em 14/10."""
    out, recentes = {}, []
    for n, day in enumerate(DIAS_IA, 1):
        e = edicao(day)
        peca = N.raio_x(e, {}, election=True, numero_serie=n, entidades_recentes=recentes[-3:])
        peca = peca or N.placar_do_dia(e, {}, election=True, numero_serie=n)
        recentes.append(list(peca.entidades) if peca else [])
        out[day] = peca
    return out


def raiox(day: int, numero: int = 14, **kw):
    return N.raio_x(edicao(day), {}, election=True, numero_serie=numero, **kw)


def slide(peca, template: str, i: int = 0):
    return [s for s in peca.slides if s.template == template][i]


def veiculos(e: Edition) -> list[str]:
    return sorted({o.name for s in e.stories if s.coverage for o in s.coverage.outlets})


# ── Raio-X 2.0 ────────────────────────────────────────────────────────────


def test_escolha_da_noite_de_14_a_25_10():
    for day, prefixo in ESCOLHAS.items():
        peca = noites()[day]
        if prefixo is None:
            assert peca.title == "Placar do dia" and peca.formula == "consenso", day
            continue
        assert peca.title == "Raio-X da cobertura", day
        assert peca.story_ids[0].startswith(prefixo), (day, peca.story_ids)
        assert all("tse-intima" not in sid for sid in peca.story_ids)


def test_raiox_de_21_10_capa_g2():
    p = raiox(21)
    assert (p.slot, p.kind, p.serie, p.numero_serie, p.formula) == ("noite", "carousel", "raiox", 14, "G2")
    assert p.story_ids == ["spacex-e-tesla-avaliam-fusao-historica-apos-salt"]
    assert p.entidades == S.entidades(edicao(21).story(p.story_ids[0]))
    assert [s.template for s in p.slides] == [
        "rx_capa", "rx_fato", "rx_lado", "rx_lado", "rx_relogio", "rx_significado", "rx_importa", "rx_placar",
    ]
    capa = p.slides[0].ctx
    assert capa["selo"] == "RAIO-X Nº 014 · QUA 21 OUT · TECNOLOGIA"
    assert capa["gancho"] == "11 VEÍCULOS, MESMO FATO. 6 DESTACARAM SINERGIA EM IA. SÓ 2, COMPLEXIDADE DE GOVERNANÇA."
    assert capa["destaque"] == "COMPLEXIDADE DE GOVERNANÇA"
    assert 96 <= capa["gancho_px"] <= 150
    # o assunto tem "histórica" (1 de 12 títulos): a capa usa a linha fina
    assert capa["subtitulo"] == edicao(21).story(p.story_ids[0]).dek
    assert p.slides[0].alt == (
        "Raio-X da cobertura nº 14, 21 de outubro. 11 veículos, mesmo fato: 6 destacaram sinergia em IA; "
        "só 2, complexidade de governança. Conselhos de administração criam comitês especiais para analisar "
        "negócio de trilhões de dólares impulsionado por inteligência artificial. "
        "Gráfico: barra com 6 veículos na maioria, 3 sem lado e 2 na minoria."
    )
    assert "<svg" in capa["grafico"] and "graf-anb" in capa["grafico"]
    # nenhum nome de veículo no texto da capa
    for nome in veiculos(edicao(21)):
        assert nome not in capa["gancho"] + capa["subtitulo"]


def test_raiox_de_21_10_fato_nao_repete_a_capa():
    p = raiox(21)
    capa, fato = p.slides[0].ctx, p.slides[1].ctx
    assert fato["kicker"] == "O fato"
    assert fato["texto"].startswith("Os conselhos de administração da SpaceX e da Tesla iniciaram conversas formais")
    assert capa["subtitulo"] not in fato["texto"]  # a capa já usou a linha fina (T3)
    assert len(fato["texto"]) <= N.FATO_MAX and N._tem_dado(fato["texto"])
    assert fato["cobertura"] == "11 veículos · 6 × 2 · 3 sem lado"
    assert (fato["a"], fato["n"], fato["b"]) == (6, 3, 2)
    assert fato["fontes"].startswith("Valor Econômico, Folha de S.Paulo")
    assert 46 <= fato["texto_px"] <= 76


def test_raiox_de_21_10_maioria_e_minoria_em_ordem_de_horario():
    p = raiox(21)
    maioria, minoria = (s.ctx for s in p.slides[2:4])
    assert (maioria["lado"], maioria["kicker"], maioria["titulo"]) == ("a", "A maioria", "6 DE 11 · SINERGIA EM IA")
    assert maioria["destaque"] == "6 DE 11"
    assert [v["name"] for v in maioria["veiculos"]] == ["The New York Times", "Valor Econômico", "g1"]
    assert maioria["veiculos"][0]["hora"] == "20/10 13h17"
    assert maioria["tambem"] == "Al Jazeera, CNBC, InfoMoney"
    assert all(len(v["framing"]) <= N.FRAMING_MAX and not v["framing"].endswith("…") for v in maioria["veiculos"])
    assert (minoria["lado"], minoria["titulo"]) == ("b", "2 DE 11: COMPLEXIDADE DE GOVERNANÇA")  # quebra: dois pontos
    assert [v["name"] for v in minoria["veiculos"]] == ["Financial Times", "BBC News"]
    assert minoria["neutros"] == "Sem tomar lado (3): TechCrunch, Folha de S.Paulo, MarketWatch"
    assert "Lado A" not in json.dumps([maioria, minoria], ensure_ascii=False)


def test_raiox_de_21_10_relogio_e_virada():
    s5 = raiox(21).slides[4]
    assert s5.ctx["titulo"] == "12 VEÍCULOS EM 16H43" and s5.ctx["destaque"] == "16H43"
    assert s5.ctx["selo"] == "RAIO-X Nº 014 · RELÓGIO DA NOTÍCIA"
    assert s5.ctx["virada"] == (
        "Primeiro texto em inglês: The New York Times, 20/10 13h17. Primeiro em português: Valor Econômico, 20/10 18h17."
    )
    assert s5.ctx["contexto"] == []  # 12 linhas: não sobra espaço
    assert s5.ctx["rodape"] == "Horário informado por cada veículo, em Brasília. Não indica quem apurou primeiro."
    # a frase do §4.1, gerada pela mesma função que desenha o gráfico
    assert (
        "Gráfico: linha do tempo com 12 pontos, de 20/10 13h17 a 21/10 06h01; 6 do lado sinergia em IA, "
        "2 do lado complexidade de governança, 3 neutros, 1 fora da análise."
    ) in s5.alt


def test_raiox_de_21_10_significado_importa_e_placar():
    p = raiox(21)
    s6, s7, s8 = p.slides[5:8]
    assert "fora da análise: The Wall Street Journal" in s6.alt
    assert "(lado" not in s6.ctx["conclusao"]
    # "unindo gigantes de Musk": adjetivo da casa fora, cortado na vírgula
    assert s7.ctx["texto"] == "A transação remodelaria as indústrias automotiva, espacial e de inteligência artificial."
    assert s8.ctx["titulo"] == "4 DE 25 ASSUNTOS DIVIDIRAM A COBERTURA HOJE"  # a spec simulou 4 de 26 sem o filtro por conteúdo
    assert s8.ctx["destaque"] == "4 DE 25" and "%" not in s8.ctx["titulo"]
    assert s8.ctx["cta"] == "Leu só uma versão? Mande para quem leu a outra."
    assert s8.ctx["serie"] == "Série completa no destaque Raio-X."
    assert "4 dividiram e 21 sem divisão; o contorno marca este Raio-X." in s8.alt


def test_raiox_de_21_10_legenda_no_molde():
    linhas = raiox(21).caption.split("\n")
    assert linhas[0] == "11 veículos, mesmo fato: 6 destacaram sinergia em IA; só 2, complexidade de governança."
    assert linhas[1] == ""
    assert linhas[2] == (
        "▸ O fato: Conselhos de administração criam comitês especiais para analisar negócio de trilhões de "
        "dólares impulsionado por inteligência artificial."
    )
    assert "▸ Sinergia em IA (6): The New York Times, Valor Econômico, g1, Al Jazeera, CNBC, InfoMoney" in linhas
    assert "▸ Complexidade de governança (2): Financial Times, BBC News" in linhas
    assert "▸ Sem tomar lado (3): TechCrunch, Folha de S.Paulo, MarketWatch" in linhas
    assert (
        "▸ Relógio: 12 veículos em 16h43, de The New York Times (20/10 13h17) a The Wall Street Journal "
        "(21/10 06h01), horário informado por cada veículo."
    ) in linhas
    assert "▸ Placar do dia: 4 de 25 assuntos com 4 ou mais veículos dividiram a cobertura." in linhas
    assert linhas[-4].startswith("Por que importa: A transação remodelaria")
    assert linhas[-3] == N.METODO
    assert linhas[-2] == N.CTA_RAIOX
    assert linhas[-1] == "#triangula #jornalismo #tecnologia"


@pytest.mark.parametrize(
    "day, formula, gancho, destaque",
    [
        (20, "G3", "MESMO FATO, 4 VEÍCULOS: METADE DESTACOU IMPACTO COMERCIAL; METADE, CONTENÇÃO SANITÁRIA.",
         "CONTENÇÃO SANITÁRIA"),
        (24, "G3", "MESMO FATO, 6 VEÍCULOS: METADE DESTACOU ALÍVIO; METADE, PERSISTÊNCIA DE ALIMENTOS.",
         "PERSISTÊNCIA DE ALIMENTOS"),
        (23, "compacto", "4 DE 6 DESTACARAM PERDA DE FÔLEGO; 2, CONTINUIDADE DA VIRADA OPERACIONAL.",
         "CONTINUIDADE DA VIRADA OPERACIONAL"),
    ],
)
def test_ganchos_reais_da_spec(day, formula, gancho, destaque):
    p = noites()[day]
    assert p.formula == formula
    assert p.slides[0].ctx["gancho"] == gancho and p.slides[0].ctx["destaque"] == destaque
    assert len(gancho) <= S.GANCHO_MAX


def test_empate_sem_maioria_nem_minoria():
    p = noites()[20]
    assert p.slides[0].alt.endswith("Gráfico: barra com 2 veículos do lado impacto comercial e 2 do lado contenção sanitária.")
    assert [s.ctx["kicker"] for s in p.slides[2:4]] == ["Metade", "A outra metade"]
    assert p.slides[0].ctx["subtitulo"] == "Governo declara emergência zoossanitária em SC após foco da doença de Newcastle"


def test_fato_pula_linha_fina_com_vicio_de_texto():
    p = noites()[23]  # a linha fina tem "em meio à desaceleração"
    texto = p.slides[1].ctx["texto"]
    assert "em meio" not in texto and texto.startswith("O Bradesco registrou lucro líquido recorrente de R$ 7,11 bilhões")


def test_conclusao_sem_lado_a_e_lado_b_entre_parenteses():
    concl = noites()[18].slides[5].ctx["conclusao"]
    assert "lado" not in concl.lower() and concl.endswith("pressão eleitoral interna.")


def test_adjetivo_da_casa_sai_do_texto_corrido():
    assert N._sem_adj("A maior parte deu destaque ao risco fiscal bilionário da derrota da União, enquanto X.", []) == (
        "A maior parte deu destaque ao risco fiscal da derrota da União, enquanto X."
    )
    assert N._sem_adj("Sem adjetivo nenhum.", []) == "Sem adjetivo nenhum."
    assert N._sem_adj("Fusão histórica na bolsa", ["fusão histórica", "a fusão histórica"]) == "Fusão histórica na bolsa"


def test_titulo_do_lado_usa_dois_pontos_quando_quebra():
    assert N._titulo_lado("6 de 11", "sinergia em IA") == "6 de 11 · sinergia em IA"
    assert N._titulo_lado("4 de 6", "restabelecimento") == "4 de 6: restabelecimento"


def test_sem_por_que_importa_o_carrossel_fica_com_7():
    e = edicao(21)
    stories = [dataclasses.replace(s, why_it_matters=None) if s.id.startswith("spacex") else s for s in e.stories]
    p = N.raio_x(dataclasses.replace(e, stories=stories), {}, election=True, numero_serie=14)
    assert len(p.slides) == 7 and "rx_importa" not in [s.template for s in p.slides]
    assert not any(linha.startswith("Por que importa") for linha in p.caption.split("\n"))


def test_relogio_invalido_vira_a_atencao():
    p = raiox(21, limiares=LimiaresConfig(relogio_min_span_min=100_000))
    s5 = p.slides[4]
    assert s5.template == "rx_atencao"
    assert s5.ctx["titulo"] == "12 VEÍCULOS, 19 TEXTOS" and s5.ctx["destaque"] == "12 VEÍCULOS"
    assert s5.ctx["linhas"] == ["1º entre 1.159 assuntos do dia; 1.039 saíram em uma fonte só", "14 dos 19 textos em inglês"]
    assert "Gráfico: matriz com 1.159 pontos" in s5.alt
    assert "▸ A atenção: 12 veículos, 19 textos; 1º entre 1.159 assuntos do dia" in p.caption


def test_relogio_com_poucos_veiculos_ganha_as_linhas_da_atencao():
    s5 = noites()[20].slides[4]
    assert s5.ctx["titulo"] == "4 VEÍCULOS EM 17H00"
    assert s5.ctx["virada"] == "Os 2 primeiros a publicar destacaram contenção sanitária."
    assert s5.ctx["contexto"][0] == "7 textos de 4 veículos: o 29º assunto mais coberto entre os 1.071 do dia."


def test_sem_rotulo_de_lado():
    e = edicao(21)
    stories = []
    for s in e.stories:
        if s.id.startswith("spacex"):
            s = dataclasses.replace(s, coverage=dataclasses.replace(s.coverage, side_b="Afasta a governança"))
        stories.append(s)
    p = N.raio_x(dataclasses.replace(e, stories=stories), {}, election=True, numero_serie=14)
    assert p.formula == "sem_rotulo"
    assert p.slides[0].ctx["gancho"] == "11 VEÍCULOS, MESMO FATO: 6 CONTARAM DE UM JEITO, 2 DE OUTRO."
    assert p.slides[0].ctx["destaque"] == ""
    assert p.slides[3].ctx["titulo"] == "2 DE 11 · A MINORIA"
    assert "▸ Minoria (2): Financial Times, BBC News" in p.caption


def test_sem_numero_de_serie():
    p = raiox(21, numero=0)
    assert p.slides[0].ctx["selo"] == "RAIO-X · QUA 21 OUT · TECNOLOGIA"
    assert p.slides[0].alt.startswith("Raio-X da cobertura, 21 de outubro.")


def test_sem_ia_nao_ha_noite_por_aqui():
    e = edicao(10)
    assert e.mode != "ai"
    assert N.raio_x(e, {}, election=True, numero_serie=1) is None
    assert N.placar_do_dia(e, {}, election=True, numero_serie=1) is None


# ── Placar do dia ─────────────────────────────────────────────────────────


def test_placar_de_19_10_dia_de_consenso():
    p = noites()[19]
    assert (p.slot, p.serie, p.numero_serie, p.formula, p.title) == ("noite", "raiox", 6, "consenso", "Placar do dia")
    assert [s.template for s in p.slides] == ["placar_capa", "placar_lista", "rx_relogio", "placar_metodo"]
    capa = p.slides[0].ctx
    assert capa["selo"] == "RAIO-X Nº 006 · DIA DE CONSENSO"
    assert capa["gancho"] == "0 DE 14 ASSUNTOS DIVIDIRAM A COBERTURA HOJE"  # a spec simulou 0 de 16
    assert capa["subtitulo"] == "assuntos com 4 ou mais veículos comparados; dividir = pelo menos 2 veículos de cada lado."
    assert "grade de 14 quadrados" in p.slides[0].alt and "0 dividiram e 14 sem divisão" in p.slides[0].alt
    consenso = p.slides[1].ctx
    assert consenso["titulo"] == "8 VEÍCULOS, NENHUM LADO"
    assert consenso["assunto"].startswith("Protestos violentos na França")
    assert consenso["texto"].startswith("A cobertura convergiu")
    coberto = p.slides[2].ctx
    assert coberto["titulo"] == "13 VEÍCULOS, 29 TEXTOS"
    assert coberto["assunto"] == "O mesmo assunto foi o mais coberto do dia, entre 680."
    assert p.slides[3].ctx["cta"] == "Salve: o placar sai todo dia."
    linhas = p.caption.split("\n")
    assert linhas[0] == "Dia de consenso: 0 de 14 assuntos com 4 ou mais veículos dividiram a cobertura hoje."
    assert any(linha.startswith("▸ O mais coberto: o mesmo assunto, 13 veículos e 29 textos") for linha in linhas)
    assert "▸ Por editoria: Mundo 0 de 10 · Esporte 0 de 3 · Tecnologia 0 de 1." in linhas
    assert linhas[-2:] == ["Salve: o placar sai todo dia.", "#triangula #jornalismo #geopolitica"]


def test_placar_com_rachas_fora_do_periodo_eleitoral():
    p = N.placar_do_dia(edicao(19), {}, election=False, numero_serie=15)
    assert p.formula == "placar"
    assert p.slides[0].ctx["selo"] == "RAIO-X Nº 015 · PLACAR DO DIA"
    assert p.slides[0].ctx["gancho"] == "1 DE 21 ASSUNTOS DIVIDIRAM A COBERTURA HOJE"
    lista = p.slides[1].ctx
    assert lista["kicker"] == "1 de 21 assuntos" and len(lista["itens"]) == 1
    assert lista["itens"][0]["linha"] == "2 × 2 · cobrança da campanha × esclarecimentos operacionais"
    assert p.caption.startswith("Placar do dia: 1 de 21 assuntos")
    # o destaque e o gancho nunca levam nome de político, nem fora do período eleitoral
    for s in p.slides:
        for chave in ("destaque", "gancho", "titulo"):
            assert not E.cita_nome(s.ctx.get(chave) or "", ignora_caixa=True)


def test_placar_sai_so_com_base_10_ou_mais():
    e = edicao(26)
    assert E.placar(e, election=True).base == 2
    assert N.placar_do_dia(e, {}, election=True, numero_serie=19) is None
    assert N.placar_do_dia(e, {}, election=True, numero_serie=19, limiares=LimiaresConfig(placar_min_base=2)) is not None


def test_26_10_no_periodo_eleitoral_nada_eleitoral_entra():
    e = edicao(26)  # a edição do 2º turno: quase tudo é política
    assert N.raio_x(e, {}, election=True, numero_serie=19) is None
    assert N.placar_do_dia(e, {}, election=True, numero_serie=19) is None  # vai para o Mercado em linha
    for cov, story in E.placar(e, election=True).itens:
        assert not E.is_political_coverage(cov, story)
    p = N.placar_do_dia(e, {}, election=True, numero_serie=19, limiares=LimiaresConfig(placar_min_base=2))
    assert redacao.lint(p, election=True) == []


# ── Regras comuns (§4.1) em todas as peças de 14 a 25/10 ──────────────────


def test_lint_e_limites_em_todas_as_noites():
    for day, p in noites().items():
        e = edicao(day)
        assert redacao.lint(p, election=True, veiculos=veiculos(e)) == [], day
        primeira = p.caption.split("\n", 1)[0]
        assert len(primeira) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX, day
        assert all(len(s.alt) <= redacao.ALT_MAX and s.alt for s in p.slides), day
        assert len(p.slides) in ((7, 8) if p.title.startswith("Raio-X") else (4,)), day
        assert not re.search(r"\d+ ?%", " ".join(s.ctx.get("titulo", "") for s in p.slides if s.template.endswith("placar")))


def test_destaque_gancho_e_rotulo_sem_politico_nem_partido_tambem_fora_da_eleicao():
    for day in DIAS_IA:
        p = N.raio_x(edicao(day), {}, election=False, numero_serie=1)
        if p is None:
            continue
        for s in p.slides:
            for chave in ("destaque", "gancho"):
                valor = s.ctx.get(chave) or ""
                assert not E.cita_nome(valor, ignora_caixa=True) and not E.cita_partido(valor), (day, valor)
            assert not E.ADJ_CASA.search(s.ctx.get("destaque") or "")


def test_templates_renderizam_com_e_sem_handle():
    cfg = load_config(ROOT)
    env = gerar.environment()
    for handle, assinatura in (("", ">triangula<"), ("triangula", ">@triangula<")):
        brand = {**gerar.brand_context(cfg), "handle": handle}
        for p in (noites()[21], noites()[19], raiox(21, limiares=LimiaresConfig(relogio_min_span_min=100_000))):
            for i, s in enumerate(p.slides, 1):
                html = env.get_template(f"{s.template}.html.j2").render(
                    brand=brand, s=gerar.conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
                )
                assert assinatura in html, (s.template, handle)
                corpo = html.split("</style>", 1)[1]
                assert 'class="t-' in corpo and " v2" in corpo.split(">", 2)[1]
                assert "Lado A" not in corpo and "Noto Sans" not in corpo
                if s.ctx.get("grafico"):
                    assert "<svg class='graf" in html


def test_election_mode_do_gerador_cobre_os_dias_da_previa():
    cfg = load_config(ROOT)
    assert all(gerar.election_mode(cfg, date(2026, 10, d)) for d in (19, 20, 21, 23, 24, 26))
    assert not gerar.election_mode(cfg, date(2026, 10, 28))
