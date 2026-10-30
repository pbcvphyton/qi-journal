"""Base comum (conteudo), Terminal da manhã e a geração do dia inteiro (gerar), sem Chromium."""

from __future__ import annotations

import functools
import json
import shutil
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from markupsafe import Markup

from qijournal.config import InstagramSlot, load_config
from qijournal.instagram import conteudo, gerar, redacao
from qijournal.instagram import estatistica as E
from qijournal.instagram.fila import PUBLISHED, Fila, Post
from qijournal.instagram.formatos import manifesto as MF
from qijournal.instagram.gerar import LAUNCH_ID, build_pieces, clean_media, generate, slot_window
from qijournal.instagram.render import RenderResult
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "edicao-2026-10-20.json"
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-29.json").is_file(), reason="sem os JSONs reais em data/")
TZ = ZoneInfo("America/Sao_Paulo")
DIA = "2026-10-20"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def edition(raw):
    return Edition.from_dict(raw)


@pytest.fixture
def root(tmp_path):
    """Pasta publicada só com a edição de exemplo em data/ (como no passo do CI)."""
    (tmp_path / "data").mkdir()
    shutil.copy(FIXTURE, tmp_path / "data" / f"{DIA}.json")
    return tmp_path


@functools.lru_cache(maxsize=None)
def config():
    return load_config(ROOT)


# ── Utilidades ────────────────────────────────────────────────────────────


def test_variacao_com_sinal_e_virgula():
    assert conteudo.pct(0.1051) == "+0,11%"
    assert conteudo.pct(-0.171) == "−0,17%"
    assert conteudo.pct(0.001) == "0,00%"
    assert conteudo.pct(None) == ""


def test_corte_em_fim_de_frase():
    text = "Primeira frase curta. Segunda frase bem mais longa que não cabe no limite."
    assert conteudo.truncate_sentence(text, 40) == "Primeira frase curta."
    assert conteudo.truncate_sentence("palavra " * 20, 30).endswith("…")


def test_texto_dos_slides_aspas_enfase_curta_e_espacos_inseparaveis():
    assert conteudo.tidy("conferência **'Davos no deserto'** em 2027") == "conferência **‘Davos no deserto’** em 2027"
    assert conteudo.tidy('o lema "Meta não se meta"') == "o lema “Meta não se meta”"
    longa = "**França decreta toques de recolher para conter onda de protestos.** Governo francês"
    assert "**" not in conteudo.tidy(longa)
    assert conteudo.tidy("A **Ri Happy** pediu") == "A **Ri Happy** pediu"
    assert conteudo.tidy("1 minuto, R$ 42,5 bilhões") == "1\u00a0minuto, R$\u00a042,5\u00a0bilhões"
    assert conteudo.polish({"color": "#FF5A1F", "outlets": [{"name": 'O "X"'}]}) == {"color": "#FF5A1F", "outlets": [{"name": "O “X”"}]}


def test_polish_devolve_markup_sem_mexer():
    svg = Markup("<svg class='graf'><text>\"12 veículos\" 1 minuto</text></svg>")
    out = conteudo.polish({"grafico": svg, "lista": [svg], "titulo": '"12 veículos"'})
    assert out["grafico"] is svg and out["lista"][0] is svg and isinstance(out["grafico"], Markup)
    assert out["titulo"] == "“12\u00a0veículos”"


def test_cotacoes_com_a_hora_da_leitura(edition, raw):
    items = {q["id"]: q for q in conteudo.quote_items(edition, [q.id for q in edition.quotes])}
    assert (items["usd"]["ref"], items["usd"]["change"], items["usd"]["trend"]) == ("leitura das 06h00", "+0,11%", "up")
    assert (items["ibov"]["ref"], items["ibov"]["change"]) == ("fech. 19/10", "−0,17%")  # 17h18: fechamento
    assert (items["ipca12m"]["ref"], items["ipca12m"]["change"]) == ("set/26", "")
    # Leitura do começo do dia sem variação (05h00, 0,00%): sem variação e sem selo.
    raw = deepcopy(raw)
    usd = next(q for q in raw["quotes"] if q["id"] == "usd")
    usd["as_of"], usd["change_pct"] = "2026-10-20T08:00:00+00:00", 0.0
    q = conteudo.quote_items(Edition.from_dict(raw), ["usd"])[0]
    assert (q["ref"], q["change"], q["trend"]) == ("", "", "")


def test_terminal_no_sistema_novo(edition):
    peca = conteudo.terminal(edition, {})
    assert (peca.slot, peca.kind, peca.title, peca.serie) == ("manha", "image", "Terminal da manhã", "")
    ctx = peca.slides[0].ctx
    assert peca.slides[0].template == "terminal" and ctx["selo"] == "TERMINAL DA MANHÃ · TER 20 OUT"
    assert (ctx["titulo"], ctx["destaque"]) == ("Brent +1,95% na leitura das 06h45", "+1,95%")
    assert "weather" not in ctx  # sem clima
    linhas = peca.caption.split("\n")
    assert linhas[0] == "Terminal da manhã · 20/10: Brent +1,95% na leitura das 06h45"
    assert sum(1 for x in linhas if x.startswith("▸ ")) == 5
    assert linhas[-2] == conteudo.TERMINAL_CTA and linhas[-1].count("#") == 4
    assert redacao.lint(peca, election=True) == []


def test_janela_do_slot_por_dia_da_semana():
    slot = InstagramSlot(weekdays="07:00", weekends=None, expires="13:00")
    due, expires = slot_window(slot, date(2026, 10, 20), TZ)
    assert due.isoformat() == "2026-10-20T07:00:00-03:00" and expires.hour == 13
    assert slot_window(slot, date(2026, 10, 24), TZ) is None  # sábado
    assert gerar.horarios(config(), date(2026, 10, 24))["manha"] == "08:30"


# ── O dia inteiro ─────────────────────────────────────────────────────────


class FakeRenderer:
    """Escreve o HTML no lugar do JPEG; ``transbordar``: {nome do arquivo: vezes} que voltam cortados."""

    def __init__(self, transbordar: dict[str, int] | None = None):
        self.calls = []
        self.transbordar = dict(transbordar or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def render(self, html, out, *, width, height):
        assert "<html" in html and "@font-face" in html and "'Noto Sans'" not in html
        name = Path(out).name
        self.calls.append((name, width, height))
        Path(out).write_bytes(html.encode("utf-8")[:2000] + str(len(self.calls)).encode())
        if self.transbordar.get(name, 0) > 0:
            self.transbordar[name] -= 1
            return RenderResult(path=Path(out), clamped=[{"fit": "gancho", "size": 96, "min": 96, "altura_px": 40, "texto": "X"}])
        return RenderResult(path=Path(out))


def gera(edition, root, tmp_path, *, now=None, fila=None, renderer=None, launch=True):
    renderer = renderer or FakeRenderer()
    now = now or datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)  # 05h00 em Brasília
    posts = generate(edition, config(), root=root, media_dir=tmp_path / "midia", now=now,
                     renderer_factory=lambda: renderer, fila=fila, launch=launch)
    return posts, renderer


def test_generate_dia_inteiro_na_ordem_da_fila(edition, root, tmp_path):
    posts, renderer = gera(edition, root, tmp_path)
    assert [p.id for p in posts] == [
        LAUNCH_ID,
        f"{DIA}-manha",
        f"{DIA}-stories-1", f"{DIA}-stories-2", f"{DIA}-stories-3", f"{DIA}-stories-4",
        f"{DIA}-numero",
        f"{DIA}-noite",
    ]
    por_id = {p.id: p for p in posts}
    manha, numero, noite = por_id[f"{DIA}-manha"], por_id[f"{DIA}-numero"], por_id[f"{DIA}-noite"]
    assert [p.due[11:16] for p in posts] == ["00:00", "07:00", "07:10", "07:11", "07:12", "07:13", "12:00", "18:30"]
    assert (manha.expires[11:16], numero.expires[11:16], noite.expires[11:16]) == ("11:00", "16:00", "22:30")
    # a edição de exemplo não tem o index: o Em 1 minuto sai sem o fechamento (7 slides) e o Número é o juro real
    assert (manha.title, manha.serie, manha.numero_serie, len(manha.images)) == ("Em 1 minuto", "em1min", 1, 7)
    assert (numero.title, numero.serie, numero.numero_serie, numero.chave, len(numero.images)) == (
        "Número do dia", "numero", 1, "juroreal", 3)
    assert (noite.title, noite.serie, noite.numero_serie, len(noite.images)) == ("Raio-X da cobertura", "raiox", 1, 8)
    assert noite.story_ids == ["governo-declara-emergencia-zoossanitaria-em-sc-a"] and noite.entidades and noite.formula
    assert len(manha.alt_texts) == 7 and all(manha.alt_texts) and manha.story_ids[0] != noite.story_ids[0]
    # Manifesto no lugar do "Comece aqui": sai uma vez, permanente, com o id de sempre.
    lanc = posts[0]
    assert lanc.permanent and lanc.title == "Manifesto" and len(lanc.images) == 6
    assert lanc.caption.startswith(MF.LINHA1) and lanc.expires.startswith("2026-10-23")
    # Stories: uma imagem cada, sem legenda; o 1º traz a agenda do que foi gerado.
    stories = [p for p in posts if p.kind == "story"]
    assert all(len(p.images) == 1 and not p.caption for p in stories)
    imgs = tmp_path / "midia"
    assert all((imgs / img).is_file() for p in posts for img in p.images)
    assert all(img.startswith(f"{DIA}/") and len(img.rsplit("-", 1)[-1]) == len("0123456789.jpg") for p in posts for img in p.images)
    sizes = {name.split("-")[0]: (w, h) for name, w, h in renderer.calls}
    assert sizes == {".manha": (1080, 1350), ".stories": (1080, 1920), ".numero": (1080, 1350),
                     ".noite": (1080, 1350), ".lancamento": (1080, 1350)}


def test_os_tres_posts_de_feed_nao_estampam_a_mesma_materia(edition):
    pecas = build_pieces(edition, config(), {}, Fila(), [edition])
    assert [p.slot for p in pecas] == ["manha", "stories", "numero", "noite"]
    principais = [gerar.assunto_principal(p) for p in pecas if p.kind != "story"]
    assert len([x for x in principais if x]) == len({x for x in principais if x})
    numero = next(p for p in pecas if p.slot == "numero")
    assert edition.lead not in numero.story_ids  # o Número não repete a manchete
    stories = next(p for p in pecas if p.slot == "stories")
    agenda = [i["nome"] for i in stories.slides[0].ctx["agenda"]]
    assert agenda == ["Em 1 minuto", "Número do dia", "Raio-X"]


def test_generate_pula_o_que_ja_passou_do_prazo(edition, root, tmp_path):
    now = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)  # 14:00 em Brasília
    posts, renderer = gera(edition, root, tmp_path, now=now)
    assert [p.id for p in posts] == [LAUNCH_ID, f"{DIA}-numero", f"{DIA}-noite"]
    assert not any(n.startswith((".manha", ".stories")) for n, _, _ in renderer.calls)  # nem renderiza


def test_numeracao_das_series_pela_fila(edition, root, tmp_path):
    fila = Fila(series={"em1min": 30, "numero": 2, "raiox": 13})
    posts, _ = gera(edition, root, tmp_path, fila=fila, launch=False)
    num = {p.id: p.numero_serie for p in posts if p.kind != "story"}
    assert num == {f"{DIA}-manha": 31, f"{DIA}-numero": 3, f"{DIA}-noite": 14}
    capa = next(p for p in posts if p.id == f"{DIA}-manha")
    assert capa.caption and all(p.numero_serie == 0 for p in posts if p.kind == "story")
    # Refeito o dia (fila com os posts), cada um mantém o número, mesmo com a série já adiante.
    fila.replace_day(DIA, posts)
    fila.series = {"em1min": 40, "numero": 9, "raiox": 20}
    refeitos, _ = gera(edition, root, tmp_path, fila=fila, launch=False)
    assert {p.id: p.numero_serie for p in refeitos if p.kind != "story"} == num


def test_noite_que_transborda_vira_mercado_em_linha(edition, root, tmp_path):
    renderer = FakeRenderer({".noite-01.jpg": 1})  # a capa do Raio-X não coube nem no mínimo
    posts, _ = gera(edition, root, tmp_path, renderer=renderer, launch=False)
    noite = next(p for p in posts if p.id == f"{DIA}-noite")
    assert (noite.title, noite.kind, noite.serie, noite.numero_serie) == ("Mercado em linha", "image", "mercado", 0)
    assert len(noite.images) == 1 and noite.caption.startswith("Mercado em linha · 20/10: ")
    # as imagens do Raio-X reprovado não ficam na pasta
    usadas = {img for p in posts for img in p.images}
    assert {f"{DIA}/{f.name}" for f in (tmp_path / "midia" / DIA).glob("*.jpg")} == usadas
    # a agenda dos stories fala do que de fato vai sair
    assert [p.id for p in posts if p.kind == "story"] and "Mercado em linha" not in [p.title for p in posts if p.kind == "story"]


def test_manha_reprovada_vira_terminal(edition):
    def aprovar(peca):
        return ["slide 1: palavra proibida “x”"] if peca.title == "Em 1 minuto" else []

    pecas = build_pieces(edition, config(), {}, Fila(), [edition], aprovar=aprovar)
    por_slot = {p.slot: p for p in pecas}
    assert por_slot["manha"].title == "Terminal da manhã" and por_slot["manha"].kind == "image"
    agenda = [i["nome"] for i in por_slot["stories"].slides[0].ctx["agenda"]]
    assert agenda[0] == "Terminal da manhã"
    assert not any(s.template == "st_feed" for s in por_slot["stories"].slides)  # sem Em 1 minuto, sem chamada


@real
def test_numero_reprovado_vai_para_o_proximo_passo():
    hist = E.load_history(DATA, date(2026, 10, 21), 90)
    vistos: list[str] = []

    def aprovar(peca):
        if peca.title != "Número do dia":
            return []
        vistos.append(peca.chave)
        return ["legenda: 1ª linha com 120 caracteres (máx. 110)"] if len(vistos) == 1 else []

    pecas = build_pieces(hist[-1], config(), {}, Fila(), hist, aprovar=aprovar)
    numero = next(p for p in pecas if p.slot == "numero")
    assert vistos == ["acum:ibov", "juroreal"] and numero.chave == "juroreal"  # passo 5 reprovado → passo 6
    assert numero.slides[2].ctx["passo"] == 6
    # tudo reprovado: o dia fica sem Número (e o log diz por quê), o resto sai
    pecas = build_pieces(hist[-1], config(), {}, Fila(), hist, aprovar=lambda p: ["x"] if p.title == "Número do dia" else [])
    assert [p.slot for p in pecas] == ["manha", "stories", "noite"]


def test_numero_reprovado_sempre_tem_o_acumulado_como_ultimo_recurso():
    hist = E.load_history(DATA, date(2026, 10, 21), 90)
    if not hist:
        pytest.skip("sem os JSONs reais em data/")
    e = hist[-1]
    primeiro = E.numero_do_dia(e, hist, election=True)
    assert primeiro.passo < 7
    seguinte = E.numero_do_dia(e, hist, election=True, apos=primeiro.passo, rejeitadas={primeiro.chave})
    assert seguinte.chave != primeiro.chave and (seguinte.passo > primeiro.passo)
    # com todos os acumulados e o juro real rejeitados, não sobra nada
    todos = {f"acum:{q}" for q in E.ACUMULADO_IDS} | {"juroreal"}
    assert E.numero_do_dia(e, hist, election=True, apos=6, rejeitadas=todos) is None
    ultimo = E.numero_do_dia(e, hist, election=True, apos=6, rejeitadas=todos - {"acum:usd"})
    assert ultimo is not None and ultimo.chave == "acum:usd" and ultimo.passo == 7


def test_sem_ia_terminal_stories_numero_e_mercado(raw, root, tmp_path):
    raw = deepcopy(raw)
    raw["mode"] = "heuristic"
    posts, _ = gera(Edition.from_dict(raw), root, tmp_path)
    feed = [p for p in posts if p.kind != "story"]
    assert [p.title for p in feed] == ["Terminal da manhã", "Número do dia", "Mercado em linha"]  # sem manifesto
    assert [p.id for p in feed] == [f"{DIA}-manha", f"{DIA}-numero", f"{DIA}-noite"]
    assert 2 <= sum(1 for p in posts if p.kind == "story") <= 3


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


def test_manifesto_volta_a_fila_ate_sair():
    def lanc(expires, status="pending"):
        return Post(id=LAUNCH_ID, date="2026-10-20", kind="carousel", due="2026-10-20T00:00:00-03:00",
                    expires=expires, images=["a.jpg"], status=status, permanent=True)

    fila = Fila(posts=[lanc("2026-10-23T00:00:00-03:00", status="expired")])
    assert fila.upsert([lanc("2026-10-23T00:00:00-03:00")]) == []  # mesmo prazo: não volta
    assert fila.upsert([lanc("2026-10-24T00:00:00-03:00")]) == [LAUNCH_ID]
    fila.posts[0].status = "published"
    assert fila.upsert([lanc("2026-10-25T00:00:00-03:00")]) == []  # já saiu: nunca mais


# ── Dias reais, com a fila avançando ──────────────────────────────────────


@real
def test_dias_reais_com_a_fila_avancando():
    """14 a 29/10 como se tudo tivesse saído: ordem, numeração, sem repetir matéria e sem eleição."""
    fila = Fila()
    contagem: dict[str, int] = {}
    for d in range(14, 30):
        day = date(2026, 10, d)
        hist = E.load_history(DATA, day, 90)
        e = hist[-1]
        pecas = build_pieces(e, config(), {}, fila, hist)
        slots = [p.slot for p in pecas]
        assert slots[0] == "manha" and slots[-2:] == ["numero", "noite"], (d, slots)
        feed = [p for p in pecas if p.kind != "story"]
        principais = [x for x in (gerar.assunto_principal(p) for p in feed) if x]
        assert len(principais) == len(set(principais)), d
        eleicao = gerar.election_mode(config(), day)
        for p in feed:
            assert redacao.lint(p, election=eleicao, nomes=config().instagram.eleicao.nomes, veiculos=gerar.veiculos(e)) == []
            if eleicao and gerar.assunto_principal(p):
                st = next(s for s in e.stories if s.id == gerar.assunto_principal(p))
                assert st.section != "politica" and not E.eleicao_escrita(st.headline), (d, p.title, st.headline)
            if p.serie and p.serie != "mercado":
                contagem[p.serie] = contagem.get(p.serie, 0) + 1
                assert p.numero_serie == contagem[p.serie], (d, p.title)
            else:
                assert p.numero_serie == 0
        posts = []
        for p in pecas:  # publicação simulada
            pid = gerar.post_id(e.date, p.slot)
            posts.append(Post(id=pid, date=e.date, kind=p.kind, due=f"{e.date}T07:00:00-03:00", expires=f"{e.date}T23:00:00-03:00",
                              images=[], serie=p.serie, numero_serie=p.numero_serie, story_ids=p.story_ids, chave=p.chave,
                              entidades=p.entidades, status=PUBLISHED))
        fila.replace_day(e.date, posts)
        for p in posts:
            fila.registrar_publicacao(p)
    assert fila.series == contagem
    assert contagem["numero"] == 16  # o 12:00 nunca fica vazio
    assert fila.chaves_numero(date(2026, 10, 30)) and len(fila.chaves_numero(date(2026, 10, 30))) == 7
