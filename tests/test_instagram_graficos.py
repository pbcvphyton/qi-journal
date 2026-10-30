"""Gráficos do Instagram (graficos.py), macros do sistema visual 2 e relatório de transbordo do render."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from markupsafe import Markup

from qijournal.instagram import graficos as G
from qijournal.instagram.render import RenderResult, Transbordo

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "qijournal" / "instagram" / "templates"
UTC = timezone.utc
NS = "{http://www.w3.org/2000/svg}"


def conta(svg: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}[\s>/]", svg))


def textos(svg: str) -> list[str]:
    """Texto de cada <text>, juntando os <tspan>."""
    raiz = ET.fromstring(svg)
    return ["".join(t.itertext()) for t in raiz.iter(f"{NS}text")]


def caixas_texto(svg: str) -> list[tuple[float, float, float, float, str]]:
    """Caixa aproximada de cada <text> simples (sem tspan), pela tabela de larguras."""
    out = []
    for t in ET.fromstring(svg).iter(f"{NS}text"):
        if len(t):
            continue
        estilo = t.get("style", "")
        tam = float(re.search(r"font-size:([\d.]+)px", estilo).group(1))
        fonte = "mono" if "Plex Mono" in estilo else ("hook" if "62%" in estilo else "label")
        w = G.largura_texto(t.text or "", fonte, tam)
        x, y = float(t.get("x")), float(t.get("y"))
        anc = t.get("text-anchor", "start")
        x0 = x if anc == "start" else (x - w if anc == "end" else x - w / 2)
        out.append((x0, y - tam * 0.75, x0 + w, y + tam * 0.2, t.text or ""))
    return out


def sem_sobreposicao(svg: str) -> None:
    cx = caixas_texto(svg)
    for i, a in enumerate(cx):
        for b in cx[i + 1 :]:
            separados = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
            assert separados, f"rótulos sobrepostos: {a[4]!r} e {b[4]!r}"


def valido(svg: str) -> ET.Element:
    """SVG bem formado, com viewBox, só tokens de cor e sem aspas duplas (atravessa o polish)."""
    assert isinstance(svg, Markup)
    raiz = ET.fromstring(svg)
    assert raiz.tag == f"{NS}svg" and raiz.get("viewBox", "").startswith("0 0 ")
    assert '"' not in svg
    cores = set(re.findall(r"(?:fill|stroke)='([^']+)'", svg))
    assert all(c.startswith("var(--") or c == "none" for c in cores), cores
    return raiz


# ── Dados reais de 21/10 (SpaceX e Tesla), copiados do JSON ───────────────

BATIDAS = [
    ("The New York Times", "2026-10-20T16:17:54+00:00", "a"),
    ("TechCrunch", "2026-10-20T20:40:41+00:00", "neutro"),
    ("Valor Econômico", "2026-10-20T21:17:24+00:00", "a"),
    ("Folha de S.Paulo", "2026-10-20T21:31:00+00:00", "neutro"),
    ("MarketWatch", "2026-10-21T00:14:00+00:00", "neutro"),
    ("g1", "2026-10-21T00:44:56+00:00", "a"),
    ("Financial Times", "2026-10-21T01:49:39+00:00", "b"),
    ("Al Jazeera", "2026-10-21T03:51:39+00:00", "a"),
    ("BBC News", "2026-10-21T06:36:39+00:00", "b"),
    ("CNBC", "2026-10-21T08:28:13+00:00", "a"),
    ("InfoMoney", "2026-10-21T08:45:50+00:00", "a"),
    ("The Wall Street Journal", "2026-10-21T09:01:41+00:00", None),
]
ROT = {"a": "sinergia em IA", "b": "complexidade de governança"}
IBOV = [  # fechamentos de 28/09 a 20/10 (14 leituras)
    ("2026-09-28", 182991.12), ("2026-09-29", 183827.6), ("2026-10-01", 184924.62), ("2026-10-02", 192114.55),
    ("2026-10-05", 207949.73), ("2026-10-06", 204961.38), ("2026-10-07", 204990.47), ("2026-10-08", 206220.23),
    ("2026-10-09", 209066.9), ("2026-10-14", 206505.98), ("2026-10-15", 204324.27), ("2026-10-16", 205430.33),
    ("2026-10-19", 205079.25), ("2026-10-20", 208295.98),
]
BTC = [  # variação diária do bitcoin, 29/09 a 21/10 (23 leituras)
    -0.08, -1.1, 0.1, 2.36, -2.04, 0.47, -0.06, 1.59, -3.07, -2.52, -0.09, 0.21, 0.2, 0.62, -0.07, -1.98, -2.36,
    2.23, -0.4, 0.57, 1.04, -1.6, 2.44,
]


def batidas():
    return [(n, datetime.fromisoformat(t)) for n, t, _ in BATIDAS]


def stance():
    return {n: s for n, _, s in BATIDAS if s}


# ── Medida de texto ──────────────────────────────────────────────────────


def test_largura_texto_usa_a_tabela_da_fonte():
    assert G.largura_texto("0000", "mono", 20) == pytest.approx(48)
    assert G.largura_texto("É", "label", 100) == G.largura_texto("E", "label", 100)
    assert G.largura_texto("M", "hook", 100) < G.largura_texto("M", "label", 100)


def test_tamanho_gancho_cabe_nas_linhas_pedidas():
    assert G.tamanho_gancho("Mesmo fato, várias versões.") == 150
    gancho = "6 de 11 destacaram sinergia em IA; 2, complexidade de governança."
    tam = G.tamanho_gancho(gancho)
    assert 112 <= tam < 150
    assert len(G.linhas_texto(gancho.upper(), "hook", tam, G.LARGURA * 0.985, -0.01)) <= 4
    # o G2 de 21/10 (87 caracteres) não cabe em 4 linhas nem no mínimo: devolve o mínimo
    g2 = "11 veículos, mesmo fato. 6 destacaram sinergia em IA. Só 2, complexidade de governança."
    assert G.tamanho_gancho(g2, tam_min=96) == 96


# ── barra_anb ────────────────────────────────────────────────────────────


def test_barra_anb_capa_do_raiox():
    svg, desc = G.barra_anb(6, 3, 2, {"a": "maioria", "n": "sem lado", "b": "minoria"})
    valido(svg)
    assert conta(svg, "rect") == 3
    assert {"6", "3", "2", "maioria", "sem lado", "minoria", "11 veículos analisados"} <= set(textos(svg))
    assert desc == "Gráfico: barra com 6 veículos na maioria, 3 sem lado e 2 na minoria."
    assert not any("%" in t for t in textos(svg))  # sem percentual


def test_barra_anb_mini_selo_sem_texto():
    svg, desc = G.barra_anb(6, 3, 2, mini=True)
    raiz = valido(svg)
    assert raiz.get("viewBox") == "0 0 240 16"
    assert conta(svg, "rect") == 3 and conta(svg, "text") == 0
    assert desc == "Gráfico: barra com 6 veículos de um lado, 3 sem lado e 2 do outro."


def test_barra_anb_casos_de_borda():
    vazio, desc = G.barra_anb(0, 0, 0)
    assert conta(vazio, "rect") == 1 and "vazia" in desc
    assert conta(G.barra_anb(0, 0, 0, mini=True)[0], "rect") == 1
    um, desc = G.barra_anb(5, 0, 0, {"a": "maioria"})
    assert conta(um, "rect") == 1 and desc == "Gráfico: barra com 5 veículos na maioria."
    # segmento mínimo de 64 px: a contagem de 1 dígito cabe dentro
    estreito, _ = G.barra_anb(40, 0, 1, {"a": "maioria", "b": "minoria"})
    assert {"40", "1", "minoria"} <= set(textos(estreito))
    larguras = [float(w) for w in re.findall(r"width='([\d.]+)'", estreito)[1:]]
    assert min(larguras) >= 64
    # quando nem assim cabe, a contagem vai para o rótulo
    fino, _ = G.barra_anb(5000, 0, 10, {"a": "maioria", "b": "minoria"})
    assert "minoria · 10" in textos(fino) and "10" not in textos(fino)


# ── relogio ──────────────────────────────────────────────────────────────


def test_relogio_spacex_21_10():
    svg, desc = G.relogio(batidas(), stance(), date(2026, 10, 21), rotulos=ROT)
    valido(svg)
    assert desc == (
        "Gráfico: linha do tempo com 12 pontos, de 20/10 13h17 a 21/10 06h01; 6 do lado sinergia em IA, "
        "2 do lado complexidade de governança, 3 neutros, 1 fora da análise."
    )
    assert conta(svg, "circle") == 12
    assert svg.count("fill='var(--bg)' stroke='var(--ink2)'") == 1  # The Wall Street Journal, vazado
    ts = textos(svg)
    assert "00h 21/10" in ts and "16h43 do primeiro ao último horário" in ts
    linhas = [t for t in ts if "h" in t[:12] and any(n in t for n, _, _ in BATIDAS)]
    assert linhas[0].startswith("20/10 13h17The New York Times")
    assert linhas[-1].startswith("06h01The Wall Street Journal") and linhas[-1].endswith("fora da análise")
    assert any(t.startswith("12 veículos · horário informado") for t in ts)
    # marcas de 3 em 3 h
    assert {"15h", "18h", "21h", "03h", "06h"} <= set(ts)


def test_relogio_muitas_linhas_e_virada_de_dois_dias():
    base = datetime(2026, 10, 19, 22, 0, tzinfo=G.TZ)
    bs = [(f"Veículo {i}", base + timedelta(hours=2 * i)) for i in range(20)]
    svg, desc = G.relogio(bs, None, date(2026, 10, 21), max_linhas=12)
    valido(svg)
    assert desc.startswith("Gráfico: linha do tempo com 20 pontos, de 19/10 22h00 a 21/10 12h00")
    assert conta(svg, "circle") == 20  # 11 linhas + 9 pontos pequenos na linha "+9"
    ts = textos(svg)
    assert any(t.startswith("+9 veículos entre") for t in ts)
    assert {"00h 20/10", "00h 21/10"} <= set(ts)
    assert "fora da análise" not in svg  # sem cobertura comparada, nenhum rótulo de lado
    altura = float(ET.fromstring(svg).get("height"))
    assert altura < 44 + 52 + 12 * 60 + 60


def test_relogio_bordas():
    svg, desc = G.relogio([], None)
    assert conta(svg, "circle") == 0 and "sem horários" in desc
    um, desc = G.relogio([("g1", datetime(2026, 10, 21, 9, 0, tzinfo=UTC))], None, date(2026, 10, 21))
    assert conta(um, "circle") == 1 and desc == "Gráfico: linha do tempo com 1 ponto, em 21/10 06h00."
    # datetime ingênuo é Brasília; objeto com .source/.at também serve
    class Batida:
        def __init__(self, source, at):
            self.source, self.at = source, at
    svg, desc = G.relogio([Batida("A", datetime(2026, 10, 21, 8, 0)), Batida("B", datetime(2026, 10, 21, 9, 30))])
    assert "de 21/10 08h00 a 21/10 09h30" in desc
    # nome longo em gráfico estreito: cortado com reticências, nunca para fora
    estreito, _ = G.relogio(batidas()[:3], stance(), date(2026, 10, 21), rotulos=ROT, largura=420)
    assert any("…" in t for t in textos(estreito))


# ── espectro ─────────────────────────────────────────────────────────────


def test_espectro_raiox():
    maj = ["The New York Times", "Valor Econômico", "g1", "Al Jazeera", "CNBC", "InfoMoney"]
    neu = ["TechCrunch", "Folha de S.Paulo", "MarketWatch"]
    mino = ["Financial Times", "BBC News"]
    svg, desc = G.espectro(maj, neu, mino, rotulos=(ROT["a"], ROT["b"]), fora=["The Wall Street Journal"])
    valido(svg)
    assert conta(svg, "circle") == 6 + 3 + 2 + 1
    ts = textos(svg)
    assert {"6", "3", "2", "sem lado", "11 veículos analisados"} <= set(ts)
    assert set(maj + neu + mino) <= set(ts)
    assert "fora da análise: The Wall Street Journal" in ts
    assert "complexidade de governança" in " ".join(ts) and not any("…" in t for t in ts)
    assert desc.startswith("Gráfico: espectro com 11 veículos analisados; à esquerda, 6 do lado sinergia em IA (")
    assert "no meio, 3 sem lado" in desc and "à direita, 2 do lado complexidade de governança" in desc
    assert not any("%" in t for t in textos(svg))  # sem percentual


def test_espectro_sem_neutros_e_com_muitos_nomes():
    nomes = [f"Veículo com nome comprido {i}" for i in range(12)]
    svg, desc = G.espectro(nomes, [], ["BBC News"], lado_maioria="b")
    valido(svg)
    ts = textos(svg)
    assert "+6" in ts and "sem lado" not in ts
    assert svg.count("fill='var(--g-b)'") == 12 and svg.count("fill='var(--g-a)'") == 1
    assert desc.startswith("Gráfico: espectro com 13 veículos analisados; à esquerda, 12 na maioria (")
    vazio, _ = G.espectro(["g1", "CNN Brasil"], [], [])
    assert "stroke-dasharray" in vazio and "0" in textos(vazio)


# ── waffle ───────────────────────────────────────────────────────────────


def test_waffle_placar_4_de_26():
    svg, desc = G.waffle(26, 4, 0)
    valido(svg)
    assert svg.count("fill='var(--g-hi)'") == 4
    assert svg.count("fill='var(--g-n)'") == 22
    assert conta(svg, "rect") == 26 + 2  # + contorno + a amostra do contorno no rótulo
    assert {"4", "22", "dividiram", "sem divisão", "este Raio-X"} <= set(textos(svg))
    assert "base: 26 assuntos com 4 ou mais veículos comparados" in textos(svg)
    assert desc == (
        "Gráfico: grade de 26 quadrados, um por assunto com 4 ou mais veículos comparados; "
        "4 dividiram e 22 sem divisão; o contorno marca este Raio-X."
    )
    # 7 por linha, quadrados de 64 px
    xs = sorted({float(x) for x in re.findall(r"<rect x='([\d.]+)' y='[\d.]+' width='64'", svg)})
    assert len(xs) == 7


def test_waffle_dia_de_consenso_e_bordas():
    svg, desc = G.waffle(16, 0)
    assert svg.count("fill='var(--g-hi)'") == 0 and "0 dividiram e 16 sem divisão" in desc
    assert "contorno" not in desc
    vazio, desc = G.waffle(0, 0)
    assert conta(vazio, "rect") == 0 and "grade vazia" in desc
    grande, _ = G.waffle(300, [0, 5, 299], unidade="assunto da semana")
    valido(grande)
    assert grande.count("fill='var(--g-hi)'") == 3
    ys = {float(y) for y in re.findall(r"<rect x='[\d.]+' y='([\d.]+)'", grande)}
    assert len([y for y in ys if y < 1000]) <= 11  # até 10 linhas (+ a amostra)
    um, desc = G.waffle(1, 1)
    assert "grade de 1 quadrado" in desc


# ── matriz_pontos ────────────────────────────────────────────────────────

# 21/10: 1.159 assuntos, 1.039 com 1 fonte, 60 com 3+, 22 com 5+, 1 com 10+ (o de 12 fontes)
CONTAGENS = [1] * 1039 + [2] * 60 + [3] * 38 + [5] * 21 + [12]


def test_matriz_pontos_concentracao_21_10():
    svg, desc = G.matriz_pontos(CONTAGENS, len(CONTAGENS) - 1, rotulo_destaque="SpaceX e Tesla: 12 veículos")
    valido(svg)
    assert conta(svg, "circle") == 1159 + 3  # + as amostras das 3 faixas
    assert svg.count("fill='var(--g-hi)'") == 1
    ts = textos(svg)
    assert "1.159 assuntos · 60 com 3+ · 22 com 5+ · 1 com 10+" in ts
    assert {"22", "98", "1.039", "SpaceX e Tesla: 12 veículos"} <= set(ts)
    assert desc == (
        "Gráfico: matriz com 1.159 pontos, um por assunto; 1.039 com 1 fonte, 98 com 2 a 4 fontes e "
        "22 com 5 ou mais fontes; em destaque, SpaceX e Tesla: 12 veículos."
    )
    # 34 colunas e o ponto ampliado inteiro dentro do desenho
    raiz = ET.fromstring(svg)
    cxs = sorted({c.get("cx") for c in raiz.iter(f"{NS}circle") if c.get("fill") != "var(--g-hi)"})
    assert len(cxs) == 34 + 1  # + a coluna das amostras
    d = next(c for c in raiz.iter(f"{NS}circle") if c.get("fill") == "var(--g-hi)")
    assert float(d.get("cx")) - float(d.get("r")) >= 0


def test_matriz_pontos_1200_sem_rotulos_e_bordas():
    svg, desc = G.matriz_pontos([1] * 1200, None, rotulos=False, altura_max=500)
    valido(svg)
    assert conta(svg, "circle") == 1200
    assert float(ET.fromstring(svg).get("height")) <= 500 + 60
    assert "em destaque" not in desc
    vazio, desc = G.matriz_pontos([], None)
    assert conta(vazio, "circle") == 0 and "matriz vazia" in desc
    um, desc = G.matriz_pontos([3], 0)
    assert conta(um, "circle") == 2 and "em destaque, 3 fontes" in desc


# ── linha ────────────────────────────────────────────────────────────────


def ibov():
    return [(date.fromisoformat(d), v) for d, v in IBOV]


def test_linha_ibovespa_desde_28_09():
    faltantes = [date(2026, 9, 30), date(2026, 10, 10), date(2026, 10, 13)]
    svg, desc = G.linha(ibov(), faltantes, altura=560)
    valido(svg)
    assert conta(svg, "circle") == 14
    assert svg.count("fill='var(--g-hi)'") == 1
    assert "stroke-dasharray='3 9'" in svg  # pregões sem leitura
    ts = textos(svg)
    assert {"182.991", "208.296", "20/10", "máx. 209.067 · 09/10"} <= set(ts)
    assert "28/09 · mín." in ts
    assert ts[-1] == "14 leituras, de 28/09 a 20/10 · 3 pregões sem leitura (tracejado)"
    assert desc == (
        "Gráfico: linha com 14 leituras, de 182.991 (28/09) a 208.296 (20/10); máxima de 209.067 em 09/10; "
        "mínima de 182.991 em 28/09; 3 pregões sem leitura, tracejado."
    )
    sem_sobreposicao(svg)


def test_linha_compacta_e_bordas():
    svg, _ = G.linha(ibov(), [], largura=440, altura=300, prefixo="US$ ", casas=2, compacto=True)
    valido(svg)
    assert ET.fromstring(svg).get("viewBox") == "0 0 440 300"
    sem_sobreposicao(svg)
    um, desc = G.linha([(date(2026, 10, 21), 5.01)], casas=2, prefixo="R$ ")
    assert conta(um, "circle") == 1 and desc == "Gráfico: uma leitura, R$ 5,01 em 21/10."
    vazio, desc = G.linha([])
    assert conta(vazio, "circle") == 0 and desc == "Gráfico: linha sem leituras."
    plano, _ = G.linha([(date(2026, 10, d), 100.0) for d in range(1, 8)])
    valido(plano)
    # datetimes com fuso entram pelo dia de Brasília
    dts = [(datetime(2026, 10, 20, 2, 0, tzinfo=UTC), 1.0), (datetime(2026, 10, 21, 2, 0, tzinfo=UTC), 2.0)]
    assert "de 1 (19/10) a 2 (20/10)" in G.linha(dts)[1]


# ── barras_variacao ──────────────────────────────────────────────────────


def btc():
    inicio = date(2026, 9, 29)
    return [(inicio + timedelta(days=i), c) for i, c in enumerate(BTC)]


def test_barras_variacao_bitcoin():
    svg, desc = G.barras_variacao(btc(), 1.5375)
    valido(svg)
    assert conta(svg, "rect") == 23
    assert svg.count("fill='var(--g-hi)'") == 1
    assert svg.count("stroke-dasharray='8 8'") == 4  # ±2 e ±3 vezes a escala
    ts = textos(svg)
    assert {"hoje: +2,44%", "1,6 vez a variação típica", "29/09", "21/10", "+3×", "−2×"} <= set(ts)
    assert desc == (
        "Gráfico: barras com 23 variações diárias, de 29/09 a 21/10; hoje, +2,44%, 1,6 vez a variação típica "
        "de 1,54%; linhas tracejadas em 2 e 3 vezes essa variação."
    )


def test_barras_variacao_sem_escala_e_limite_de_30():
    longa = [(date(2026, 9, 1) + timedelta(days=i), (-1) ** i * 0.5) for i in range(40)]
    longa[-1] = (longa[-1][0], -2.36)
    svg, desc = G.barras_variacao(longa, 0.9)
    assert conta(svg, "rect") == 30 and "hoje, −2,36%, 2,6 vezes" in desc
    sem, desc = G.barras_variacao(btc()[:5])
    assert "stroke-dasharray" not in sem and "típica" not in desc
    assert conta(G.barras_variacao([])[0], "rect") == 0
    um, desc = G.barras_variacao([(date(2026, 10, 21), 0.0)])
    assert conta(um, "rect") == 1 and "em 21/10; hoje, 0,00%" in desc


# ── checklist ────────────────────────────────────────────────────────────


def test_checklist_numero_triangulado():
    nomes = ["Valor Econômico", "g1", "Folha de S.Paulo", "Agência Brasil", "Estadão", "Money Times"]
    horas = {n: datetime(2026, 10, 21, 20, i, tzinfo=UTC) for i, n in enumerate(nomes)}
    horas["Money Times"] = datetime(2026, 10, 22, 8, 59, tzinfo=UTC)
    ok = set(nomes) - {"g1"}
    svg, desc = G.checklist(nomes, ok, horas, ref_date=date(2026, 10, 22))
    valido(svg)
    assert conta(svg, "path") == 5  # ✓ desenhado, não glifo
    ts = textos(svg)
    assert "5 de 6 veículos com o número no título" in ts
    assert "21/10 17h00" in ts and "05h59" in ts
    assert desc.startswith("Gráfico: lista de 6 veículos; 5 de 6 com o número no título (Valor Econômico, 21/10 17h00;")
    assert desc.endswith("; sem o número: g1; títulos não reproduzidos.")


def test_checklist_muitos_e_bordas():
    nomes = [f"Veículo {i}" for i in range(15)]
    svg, _ = G.checklist(nomes, nomes[::2], [None] * 15, max_linhas=12)
    assert any(t.startswith("+4 veículos (") for t in textos(svg))
    assert conta(svg, "path") == 6  # os confirmados entre os 11 mostrados
    vazio, desc = G.checklist([], [])
    assert "lista vazia" in desc
    longo, _ = G.checklist(["The Wall Street Journal"], [], largura=300)
    assert "…" in textos(longo)[0]


def test_svg_atravessa_o_polish_dos_slides():
    from qijournal.instagram.conteudo import polish

    for svg, _ in (
        G.relogio(batidas(), stance(), date(2026, 10, 21), rotulos=ROT),
        G.linha(ibov(), [date(2026, 9, 30)], prefixo="R$ "),
        G.waffle(26, 4, 0),
    ):
        depois = polish({"grafico": str(svg)})["grafico"]
        assert "“" not in depois and "”" not in depois
        assert conta(depois, "text") == conta(svg, "text") and conta(depois, "circle") == conta(svg, "circle")
        ET.fromstring(depois)


# ── Macros e tokens do sistema 2 ─────────────────────────────────────────


@pytest.fixture
def env():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, trim_blocks=True,
                       lstrip_blocks=True, undefined=StrictUndefined)


def macro(env, nome: str, *args, ctx: dict | None = None, **kwargs) -> str:
    contexto = {"brand": {"name": "Triangula"}, "page": 3, "pages": 8, "kind": "carousel"} | (ctx or {})
    m = env.get_template("_macros.html.j2").make_module(contexto)
    return str(getattr(m, nome)(*args, **kwargs))


def test_assinatura_sem_handle_e_com_handle(env):
    sem = macro(env, "assinatura", "análise por IA de 11 veículos")
    assert ">triangula<" in sem and "@" not in sem
    assert "análise por IA de 11 veículos" in sem and "var(--tri)" in sem
    vazio = macro(env, "assinatura", ctx={"brand": {"handle": ""}})
    assert ">triangula<" in vazio and "note" not in vazio
    com = macro(env, "assinatura", "fonte: Yahoo Finance", ctx={"brand": {"handle": "triangula"}})
    assert ">@triangula<" in com
    assert ">@outro<" in macro(env, "assinatura", handle="@outro")


def test_serie_band_selo_e_contador(env):
    capa = macro(env, "serie_band", "Raio-X Nº 014 · TER 21 OUT", None, "raiox")
    assert "Raio-X Nº 014 · TER 21 OUT" in capa and "cnt" not in capa and "#F6F4EF" in capa
    interno = macro(env, "serie_band", "Número do dia Nº 003", True, "numero")
    assert ">03/08<" in interno and "#FF5A1F" in interno
    assert ">05/08<" in macro(env, "serie_band", "Em 1 minuto Nº 031", "05/08", "#123456")


def test_selo_cobertura_realce_e_numero(env):
    selo = macro(env, "selo_cobertura", "12 veículos · dividida 6 × 2", 6, 3, 2)
    assert selo.count("<i ") == 3 and "flex:6 1 0" in selo and "var(--g-b)" in selo
    so_texto = macro(env, "selo_cobertura", "8 veículos · sem divisão")
    assert "anb" not in so_texto and "8 veículos · sem divisão" in so_texto
    realce = macro(env, "realce", "Só 2, complexidade de governança.", "Complexidade de governança")
    assert realce == 'Só 2, <em class="hl">complexidade de governança</em>.'
    assert macro(env, "realce", "<b>", "x") == "&lt;b&gt;"
    numero = macro(env, "num_hero", "R$ 1 bi", "cerca de")
    assert 'class="qual"' in numero and "font-size:320px" in numero and 'data-min="200"' in numero
    gancho = macro(env, "hook", "Mesmo fato", "fato", 150, 96)
    assert 'data-fit="gancho"' in gancho and '<em class="hl">fato</em>' in gancho


def test_macros_antigos_continuam(env):
    m = env.get_template("_macros.html.j2").make_module({"brand": {"logo": ""}, "page": 1, "pages": 1, "kind": "carousel"})
    for nome in ("logo", "header", "footer", "spectrum", "spectrum_css", "rx_band", "datebox", "names_line", "rich"):
        assert hasattr(m, nome), nome


def _tema(css: str, classe: str) -> dict[str, str]:
    bloco = re.search(rf"\.{classe}\{{([^}}]*)\}}", css).group(1)
    return dict(re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6})", bloco))


def _contraste(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)

    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_temas_passam_de_4_5_em_todo_texto():
    css = (TEMPLATES / "_base.html.j2").read_text(encoding="utf-8")
    laranja = _tema(css, "t-orange")
    assert laranja["bg"] == "#FF5A1F" and laranja["ink"] == laranja["ink2"] == laranja["ink3"] == "#0F1730"
    for classe in ("t-dark", "t-paper", "t-orange"):
        t = _tema(css, classe)
        for token in ("ink", "ink2", "ink3", "accent"):
            assert _contraste(t[token], t["bg"]) >= 4.5, (classe, token)
        for token in ("g-a", "g-b", "g-n", "g-hi"):  # marcas de gráfico: 3:1
            assert _contraste(t[token], t["bg"]) >= 3.0, (classe, token)
        for lado in ("a", "b", "n"):  # contagem dentro do segmento
            assert _contraste(t[f"g-{lado}-ink"], t[f"g-{lado}"]) >= 4.5, (classe, lado)
    assert "font-stretch:62%" in css and "--label:'Archivo'" in css
    assert re.search(r"\.hook\{[^}]*line-height:\.92[^}]*letter-spacing:-\.01em[^}]*text-transform:uppercase", css)
    assert re.search(r"\.num-hero\{[^}]*tabular-nums", css)


# ── render: transbordo para o lint ───────────────────────────────────────


def test_render_result_transbordos_tipados():
    r = RenderResult(Path("x.jpg"), [{"fit": "gancho", "size": 96, "min": 96, "altura_px": 40, "largura_px": 0,
                                      "texto": "11 VEÍCULOS"}, {"fit": "antigo", "size": 20}])
    assert not r.ok
    t = r.transbordos
    assert t[0] == Transbordo("gancho", 96.0, 96.0, 40, 0, "11 VEÍCULOS")
    assert "40 px na altura" in str(t[0])
    assert t[1].fit == "antigo" and t[1].min == 0
    assert RenderResult(Path("y.jpg")).ok


def _renderer():
    pytest.importorskip("playwright.sync_api")
    from qijournal.instagram.render import Renderer

    r = Renderer()
    try:
        r.__enter__()
    except Exception as exc:  # sem Chromium nesta máquina
        r._tmp.cleanup()
        pytest.skip(f"Chromium indisponível: {exc}")
    return r


def test_render_informa_quem_transbordou_no_minimo(tmp_path):
    from jinja2 import Environment, FileSystemLoader

    from qijournal.instagram.render import fonts_css

    (tmp_path / "t.html.j2").write_text(
        '{% extends "_base.html.j2" %}{% import "_macros.html.j2" as m with context %}'
        "{% block theme %}t-paper v2{% endblock %}"
        "{% block body %}{{ m.serie_band('Raio-X Nº 014') }}<div class='frame'>"
        "{{ m.hook(s.gancho, none, 150, 140, 300, 'gancho') }}"
        "{{ m.hook('Cabe', none, 150, 96, 400, 'cabe') }}</div>{{ m.assinatura('nota') }}{% endblock %}",
        encoding="utf-8",
    )
    env = Environment(loader=FileSystemLoader([str(tmp_path), str(TEMPLATES)]), autoescape=True)
    brand = {"fonts_css": Markup(fonts_css(ROOT)), "handle": None}
    html = env.get_template("t.html.j2").render(brand=brand, kind="carousel", s={"gancho": "palavra " * 40})
    r = _renderer()
    try:
        res = r.render(html, tmp_path / "x.jpg", width=1080, height=1350)
    finally:
        r.__exit__(None, None, None)
    assert res.path.exists()
    nomes = [t.fit for t in res.transbordos]
    assert "gancho" in nomes and "cabe" not in nomes
    t = next(t for t in res.transbordos if t.fit == "gancho")
    assert t.size == 140 and t.min == 140 and t.altura_px > 0 and t.texto.startswith("palavra")
