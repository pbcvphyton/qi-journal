"""Conteúdo e geração das publicações do Instagram (sem Chromium: renderizador falso)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qijournal.config import InstagramSlot, load_config
from qijournal.instagram import conteudo
from qijournal.instagram.fila import Fila, Post
from qijournal.instagram.gerar import clean_media, generate, slot_window
from qijournal.models import Edition

FIXTURE = Path(__file__).parent / "fixtures" / "edicao-2026-10-20.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def edition(raw):
    return Edition.from_dict(raw)


def test_variacao_com_sinal_e_virgula():
    assert conteudo.pct(0.1051) == "+0,11%"
    assert conteudo.pct(-0.171) == "−0,17%"
    assert conteudo.pct(0.001) == "0,00%"
    assert conteudo.pct(None) == ""


def test_corte_em_fim_de_frase():
    text = "Primeira frase curta. Segunda frase bem mais longa que não cabe no limite."
    assert conteudo.truncate_sentence(text, 40) == "Primeira frase curta."
    assert conteudo.truncate_sentence("palavra " * 20, 30).endswith("…")


def test_cotacoes_com_referencia_de_fechamento_e_mes(edition):
    items = {q["id"]: q for q in conteudo.quote_items(edition, conteudo.MERCADO_IDS)}
    assert items["usd"]["ref"] == "" and items["usd"]["trend"] == "up"
    assert items["ibov"]["ref"] == "fech. 19/10" and items["ibov"]["change"] == "−0,17%"
    assert items["ipca12m"]["ref"] == "set/26" and items["ipca12m"]["change"] == ""


def test_em_1_minuto_no_periodo_eleitoral_tira_pesquisa(edition):
    peca = conteudo.em_1_minuto(edition, {}, election=True)
    assert [s.template for s in peca.slides] == ["capa", "fato", "fato", "fato", "fato", "fato", "mercado", "fechamento"]
    texts = " ".join(s.ctx["text"] for s in peca.slides if s.template == "fato")
    assert "empate técnico" not in texts and "Flávio" not in texts
    assert peca.slides[1].ctx["text"].startswith("A Justiça do Rio")  # o item da manchete vem primeiro
    assert peca.caption.splitlines()[0].startswith("Justiça do Rio encerra")
    assert "**" not in peca.caption and len(peca.caption) <= conteudo.CAPTION_LIMIT
    assert peca.caption.count("#") <= 5
    assert all(s.alt for s in peca.slides)


def test_em_1_minuto_fora_do_periodo_mantem_o_briefing(edition):
    peca = conteudo.em_1_minuto(edition, {}, election=False)
    texts = " ".join(s.ctx["text"] for s in peca.slides if s.template == "fato")
    assert "empate técnico" in texts


def test_sem_ia_vira_terminal_e_sem_raiox(raw):
    raw = deepcopy(raw)
    raw["mode"] = "heuristic"
    edition = Edition.from_dict(raw)
    assert conteudo.em_1_minuto(edition, {}, election=False) is None
    assert conteudo.raio_x(edition, {}, election=False) is None
    peca = conteudo.terminal(edition, {})
    assert peca.kind == "image" and len(peca.slides) == 1
    assert peca.caption.startswith("Terminal da manhã · 20/10: Dólar R$ 5,05")
    stories = conteudo.stories_manha(edition, {}, election=False)
    assert [s.template for s in stories.slides] == ["story_bomdia", "story_mercado"]


def test_raio_x_escolhe_cobertura_dividida_e_neutra_no_periodo_eleitoral(edition):
    peca = conteudo.raio_x(edition, {}, election=True)
    assert peca.story_ids == ["governo-declara-emergencia-zoossanitaria-em-sc-a"]
    assert [s.template for s in peca.slides] == [
        "raiox_capa", "raiox_fato", "raiox_lado", "raiox_lado", "raiox_espectro", "raiox_conclusao", "raiox_metodo",
    ]
    lado_a, lado_b = peca.slides[2].ctx, peca.slides[3].ctx
    assert [o["name"] for o in lado_a["outlets"]] == ["Estadão", "CNN Brasil"]
    assert [o["name"] for o in lado_b["outlets"]] == ["Folha de S.Paulo", "g1"]
    assert peca.caption.startswith("Mesmo fato, 2 enquadramentos:")
    assert "#politica" not in peca.caption


def test_raio_x_sem_cobertura_dividida_nao_sai(raw):
    raw = deepcopy(raw)
    for story in raw["stories"]:
        story["coverage"] = None
    assert conteudo.raio_x(Edition.from_dict(raw), {}, election=False) is None


def test_janela_do_slot_por_dia_da_semana():
    tz = ZoneInfo("America/Sao_Paulo")
    slot = InstagramSlot(weekdays="07:00", weekends=None, expires="13:00")
    due, expires = slot_window(slot, date(2026, 10, 20), tz)
    assert due.isoformat() == "2026-10-20T07:00:00-03:00" and expires.hour == 13
    assert slot_window(slot, date(2026, 10, 24), tz) is None  # sábado


class FakeRenderer:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def render(self, html, out, *, width, height):
        assert "<html" in html and "@font-face" in html
        self.calls.append((Path(out).name, width, height))
        Path(out).write_bytes(html.encode("utf-8")[:2000])


def test_generate_renderiza_e_monta_a_fila(tmp_path, edition):
    config = load_config()
    renderer = FakeRenderer()
    now = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    posts = generate(edition, config, root=config.root, media_dir=tmp_path, now=now, renderer_factory=lambda: renderer)
    ids = [p.id for p in posts]
    assert ids == [
        "lancamento-comece-aqui",
        "2026-10-20-manha",
        "2026-10-20-stories-1", "2026-10-20-stories-2", "2026-10-20-stories-3", "2026-10-20-stories-4",
        "2026-10-20-raiox",
    ]
    lancamento, manha = posts[0], posts[1]
    assert lancamento.due == "2026-10-20T00:00:00-03:00" and len(lancamento.images) == 6
    assert lancamento.caption.startswith("Comece aqui")
    assert manha.kind == "carousel" and len(manha.images) == 8 and len(manha.alt_texts) == 8
    assert manha.due == "2026-10-20T07:00:00-03:00" and manha.expires == "2026-10-20T13:00:00-03:00"
    assert all((tmp_path / img).is_file() for p in posts for img in p.images)
    assert all(img.startswith("2026-10-20/") and len(img.rsplit("-", 1)[-1]) == len("0123456789.jpg") for p in posts for img in p.images)
    assert posts[2].due < posts[3].due  # stories na ordem
    sizes = {name.split("-")[0]: (w, h) for name, w, h in renderer.calls}
    assert sizes == {".manha": (1080, 1350), ".stories": (1080, 1920), ".raiox": (1080, 1350), ".lancamento": (1080, 1350)}


def test_generate_pula_o_que_ja_passou_do_prazo(tmp_path, edition):
    config = load_config()
    now = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)  # 14:00 em Brasília
    posts = generate(edition, config, root=config.root, media_dir=tmp_path, now=now, renderer_factory=FakeRenderer)
    assert [p.id for p in posts] == ["lancamento-comece-aqui", "2026-10-20-raiox"]


def test_limpeza_de_imagens_antigas_e_orfas(tmp_path):
    (tmp_path / "2026-10-01").mkdir()
    (tmp_path / "2026-10-01" / "manha-01-a.jpg").write_bytes(b"x")
    (tmp_path / "2026-10-20").mkdir()
    (tmp_path / "2026-10-20" / "manha-01-velha.jpg").write_bytes(b"x")
    (tmp_path / "2026-10-20" / "manha-01-nova.jpg").write_bytes(b"x")
    fila = Fila(posts=[Post(id="2026-10-20-manha", date="2026-10-20", kind="carousel", due="2026-10-20T07:00:00-03:00",
                            expires="2026-10-20T13:00:00-03:00", images=["2026-10-20/manha-01-nova.jpg"])])
    removed = clean_media(fila, tmp_path, date(2026, 10, 20), keep_days=7)
    assert sorted(p.name for p in removed) == ["2026-10-01", "manha-01-velha.jpg"]
    assert (tmp_path / "2026-10-20" / "manha-01-nova.jpg").is_file()


def test_comece_aqui_volta_a_fila_ate_sair():
    def lanc(expires, status="pending"):
        return Post(id="lancamento-comece-aqui", date="2026-10-20", kind="carousel", due="2026-10-20T00:00:00-03:00",
                    expires=expires, images=["a.jpg"], status=status)

    fila = Fila(posts=[lanc("2026-10-23T00:00:00-03:00", status="expired")])
    assert fila.upsert([lanc("2026-10-23T00:00:00-03:00")]) == []  # mesmo prazo: não volta
    assert fila.upsert([lanc("2026-10-24T00:00:00-03:00")]) == ["lancamento-comece-aqui"]
    fila.posts[0].status = "published"
    assert fila.upsert([lanc("2026-10-25T00:00:00-03:00")]) == []  # já saiu: nunca mais


def test_texto_dos_slides_aspas_enfase_curta_e_espacos_inseparaveis():
    assert conteudo.tidy("conferência **'Davos no deserto'** em 2027") == "conferência **‘Davos no deserto’** em 2027"
    assert conteudo.tidy('o lema "Meta não se meta"') == "o lema “Meta não se meta”"
    longa = "**França decreta toques de recolher para conter onda de protestos.** Governo francês"
    assert "**" not in conteudo.tidy(longa)
    assert conteudo.tidy("A **Ri Happy** pediu") == "A **Ri Happy** pediu"
    assert conteudo.tidy("1 minuto, R$ 42,5 bilhões") == "1 minuto, R$ 42,5 bilhões"
    assert conteudo.polish({"color": "#FF5A1F", "outlets": [{"name": 'O "X"'}]}) == {"color": "#FF5A1F", "outlets": [{"name": "O “X”"}]}


def test_numeros_arredondados_do_comece_aqui(edition):
    assert conteudo.more_than(1581, 100) == "mais de 1.500"
    assert conteudo.more_than(40, 10) == "40"
    peca = conteudo.comece_aqui(edition, {})
    assert peca.slides[0].ctx["headline"] == "Todo dia, mais de 1.500 notícias. 1 minuto para você."
    assert "1.581" not in peca.caption and "mais de 40 veículos" in peca.caption


def test_raio_x_leva_nomes_e_manchete_em_todos_os_slides(edition):
    peca = conteudo.raio_x(edition, {}, election=True)
    for slide in peca.slides:
        assert slide.ctx["a"] == ["Estadão", "CNN Brasil"] and slide.ctx["b"] == ["Folha de S.Paulo", "g1"]
        assert slide.ctx["headline"].startswith("Governo declara emergência")
