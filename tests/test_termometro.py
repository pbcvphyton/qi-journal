"""Termômetro de Viés Editorial na edição: textos, pedido à IA, validação, cálculo,
histórico, publicação e páginas (a referência e a agregação têm os testes da
especificação em ``tests/termometro/``)."""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

import qijournal.edit as edit
from qijournal import pipeline
from qijournal.collect.enrich import PageInfo
from qijournal.config import load_config
from qijournal.edit.llm import LLMUnavailable, _Usage
from qijournal.models import Article, Bundle, Edition, Section, SourceRef, Story
from qijournal.render.analitico import render_analitico_page
from qijournal.render.termometro import termometro_dia_view
from qijournal.render.web import render_edition_page
from qijournal.termometro import agregacao, analise, historico
from qijournal.termometro.referencia import PARAMETROS, calcular
from tests.fixtures.pipeline.factory import make_edition as pipeline_edition
from tests.fixtures.render.helpers import parse, visible_text

CASO = json.loads((Path(__file__).parent / "termometro" / "caso_referencia.json").read_text(encoding="utf-8"))
DATA = "2026-09-29"
CORPO = (
    "O governo anunciou nesta segunda-feira um corte de 12% no orçamento do programa, segundo a Secretaria de "
    "Planejamento. A medida foi criticada por especialistas, que alertam para o impacto sobre famílias de baixa "
    "renda. O ministro alegou que o ajuste é necessário para equilibrar as contas públicas.\n"
    "Dados oficiais mostram que o programa atendeu 2,1 milhões de famílias em 2025, a um custo de R$ 4 bilhões. "
    '"Não há alternativa", disse o ministro em entrevista coletiva. A oposição prometeu recorrer ao Supremo '
    "contra a decisão, que considera ilegal, e convocou atos em várias capitais para o fim de semana.\n"
    "Economistas ouvidos pela reportagem divergem sobre o efeito do corte. Para uns, a redução abre espaço para "
    "investimentos; para outros, o custo social será maior do que a economia obtida no ano que vem.\n"
    "O Congresso deve votar o orçamento do próximo ano em dezembro, e líderes da base afirmam que o corte será "
    "mantido. Prefeitos de capitais pediram uma reunião com o governo para discutir a transição das famílias."
)


# ── fábrica ──────────────────────────────────────────────────────────────────


def artigo(i: int, *, fonte: str = "Folha de S.Paulo", lang: str = "pt", url: str | None = None) -> Article:
    return Article(
        id=f"t{i:03d}",
        url=url or f"https://exemplo{i}.com.br/politica/{i}.html",
        title=f"Governo corta orçamento do programa social número {i}",
        summary=f"Resumo do feed {i}.",
        source_id=f"f{i}",
        source_name=fonte,
        lang=lang,
        published="2026-09-29T09:00:00+00:00",
    )


def materia(story_id: str, section: str, article_ids: list[str]) -> Story:
    return Story(
        id=story_id,
        section=section,
        headline=f"Manchete {story_id}",
        dek="Linha fina.",
        body=["Parágrafo."],
        sources=[SourceRef(name="Folha", url="https://exemplo.com.br/x")],
        article_ids=article_ids,
    )


def cenario(n_materias: int = 3, por_materia: int = 3) -> tuple[Edition, Bundle]:
    """Edição com matérias de política, economia e esporte e os artigos de cada uma."""
    edition = pipeline_edition(DATA)
    secoes = ["politica", "brasil", "esporte"]
    artigos, materias = [], []
    for m in range(n_materias):
        ids = []
        for k in range(por_materia):
            a = artigo(m * 10 + k, fonte=f"Veículo {k}")
            artigos.append(a)
            ids.append(a.id)
        materias.append(materia(f"m{m}", secoes[m % len(secoes)], ids))
    edition.stories = materias
    edition.sections = [Section(id=s, title=s, color="#1C49A5", story_ids=[m.id for m in materias if m.section == s]) for s in secoes]
    bundle = Bundle(collected_at="2026-09-29T10:00:00+00:00", articles=artigos, quotes=[], weather=[], sources=[])
    return edition, bundle


def paginas(articles: list[Article]) -> dict[str, PageInfo]:
    return {a.id: PageInfo(description=f"Linha fina da página {a.id}.", text=CORPO, author="Repórter Teste") for a in articles}


def saida_valida(paragrafos: dict[str, str], *, lado: str = "esquerda") -> dict[str, Any]:
    """Saída v1.1 com citações literais do texto."""
    s = copy.deepcopy(CASO["saida_modelo"])
    s["atores"], s["fontes"] = [], []
    corpo = paragrafos["P2"]
    trechos = [corpo[:40].rsplit(" ", 1)[0], corpo[60:110].rsplit(" ", 1)[0], paragrafos["P0"][:30].rsplit(" ", 1)[0]]
    s["evidencias"] = [
        {
            "criterio": criterio, "citacao": trecho, "paragrafo": pid, "eixo": "economico", "ator_favorecido": None,
            "ator_desfavorecido": None, "direcao": lado, "intensidade": 2, "certeza": 0.8, "explicacao": "teste",
            "ressalva": None,
        }
        for criterio, trecho, pid in zip(("ENQUADRAMENTO", "ADJETIVOS", "HIERARQUIA"), trechos, ("P2", "P2", "P0"), strict=True)
    ]
    return s


class Falsa:
    """IA falsa com a interface dos editores: só responde ao termômetro."""

    provider = "falsa"
    model = "modelo-falso"
    parallel = 1
    max_tokens_write = 32000
    max_tokens_select = 32000
    context_tokens = 128000

    def __init__(self, *, invalidas: set[str] = frozenset(), omitir: set[str] = frozenset()) -> None:
        self.usage = _Usage()
        self.calls: list[dict[str, Any]] = []
        self.exhausted = False
        self.invalidas, self.omitir = invalidas, omitir
        self.paragrafos: dict[str, dict[str, str]] = {}

    def call(self, *, label, system, user_text, schema, max_tokens, deadline=None):
        self.calls.append({"label": label, "user_text": user_text, "schema": schema, "system": system})
        if not label.startswith("termômetro"):
            raise LLMUnavailable(f"{label}: a IA falsa só faz o termômetro")
        analises = []
        for m in re.finditer(r'<texto id="(T\d+)">(.*?)</texto>', user_text, re.S):
            chave, corpo = m.groups()
            pars = dict(re.findall(r"^(P\d+): (.*)$", corpo, re.M))
            self.paragrafos[chave + str(len(self.calls))] = pars
            if chave in self.omitir:
                continue
            saida = saida_valida(pars)
            if chave in self.invalidas:
                saida["evidencias"][0]["intensidade"] = 7
            analises.append({"texto": chave, "saida": saida})
        self.usage.record(self.model, 100, 200)
        return {"analises": analises}


@pytest.fixture
def config():
    return load_config(env={})


# ── schema e validação ───────────────────────────────────────────────────────


def test_schema_da_api_sem_restricoes_que_as_apis_recusam(config):
    schema = analise.carregar_schema(config)
    api = json.dumps(analise.schema_do_lote(schema, ["T1", "T2"]))
    assert '"minimum"' not in api and '"maximum"' not in api and '"pattern"' not in api
    assert '"type": ["' not in api and '"anyOf"' in api
    assert '"enum": ["T1", "T2"]' in api


def test_validacao_aceita_o_caso_de_referencia_e_recusa_saidas_fora_do_schema(config):
    schema = analise.carregar_schema(config)
    assert analise.validar(CASO["saida_modelo"], schema) == []
    ruim = copy.deepcopy(CASO["saida_modelo"])
    ruim["evidencias"][0]["certeza"] = 1.5
    ruim["evidencias"][1]["paragrafo"] = "parágrafo 3"
    del ruim["qualidade"]["contraponto"]
    erros = analise.validar(ruim, schema)
    assert any("certeza" in e for e in erros) and any("paragrafo" in e for e in erros)
    assert any("contraponto: ausente" in e for e in erros)


def test_dicionario_da_configuracao_e_o_do_caso_de_referencia(config):
    assert analise.carregar_lexico(config) == CASO["lexico"]


# ── textos ───────────────────────────────────────────────────────────────────


def test_texto_segue_a_especificacao_titulo_linha_fina_e_corpo(config):
    a = artigo(1)
    story = materia("m", "politica", [a.id])
    info = PageInfo(description="Linha fina da página.", text=CORPO, author="Fulana")
    t = analise.montar_texto(a, story, info, editoria="Política", tz=__import__("zoneinfo").ZoneInfo("America/Sao_Paulo"), data_edicao=DATA)
    assert t.paragrafos["P0"] == a.title and t.paragrafos["P1"] == "Linha fina da página." and t.linha_fina
    assert t.paragrafos["P2"].startswith("O governo anunciou") and len(t.paragrafos) == 6
    assert t.meta == {"veiculo": "Folha de S.Paulo", "secao": "Política", "autor": "Fulana", "data": DATA}
    assert t.palavras >= PARAMETROS["min_palavras"] and not t.cortado


def test_linha_fina_que_repete_o_lead_fica_de_fora_e_coluna_vira_opiniao(config):
    a = artigo(2, url="https://www1.folha.uol.com.br/colunas/fulano/2026/09/texto.shtml")
    a.summary = CORPO.split(".")[0] + "."  # o resumo do feed também repete o lead
    info = PageInfo(description=CORPO.split(".")[0] + ".", text=CORPO + "…")
    t = analise.montar_texto(a, materia("m", "politica", [a.id]), info, editoria="Política", tz=__import__("zoneinfo").ZoneInfo("UTC"), data_edicao=DATA)
    assert not t.linha_fina and t.paragrafos["P1"].startswith("O governo anunciou")
    assert t.meta["secao"] == "Opinião" and t.cortado


@pytest.mark.parametrize(
    ("url", "opiniao"),
    [
        ("https://www1.folha.uol.com.br/colunas/fulano/2026/09/texto.shtml", True),
        ("https://www.estadao.com.br/opiniao/editorial-sobre-juros/", True),
        ("https://www.jota.info/opiniao-e-analise/artigos/texto", True),
        ("https://g1.globo.com/politica/noticia/2026/09/29/governo-defende-artigo-da-reforma.ghtml", False),
        ("https://www.cnnbrasil.com.br/politica/analise-de-dados-mostra-queda/", False),
    ],
)
def test_opiniao_pelos_diretorios_da_url_nao_pelo_slug(url, opiniao):
    a = artigo(3, url=url)
    t = analise.montar_texto(a, materia("m", "politica", [a.id]), PageInfo(text=CORPO), editoria="Política", tz=__import__("zoneinfo").ZoneInfo("UTC"), data_edicao=DATA)
    assert (t.meta["secao"] == "Opinião") is opiniao


def test_bloco_unico_e_dividido_entre_frases_sem_cortar_falas():
    bloco = " ".join(["Frase de teste com várias palavras para encher o parágrafo."] * 12)
    bloco += ' Ele disse: "Primeira frase da fala. Segunda frase da fala que continua bem longa." Fim do texto.'
    partes = analise._dividir_paragrafo(bloco * 2)
    assert len(partes) > 1 and " ".join(partes) == bloco * 2
    assert all(p.count('"') % 2 == 0 for p in partes)


def test_candidatos_em_rodizio_por_prioridade_e_idioma(config):
    edition, bundle = cenario(n_materias=3, por_materia=2)
    bundle.articles[1].lang = "en"
    ordem = [(a.id, s.id) for a, s in analise.candidatos(edition, bundle, config)]
    # política (m0) antes de economia (m1); esporte (m2) fora; o 2º artigo de m0 está em inglês
    assert ordem == [("t000", "m0"), ("t010", "m1"), ("t011", "m1")]


# ── análise da edição ────────────────────────────────────────────────────────


def test_analise_usa_o_calculo_de_referencia_sem_alteracao(config):
    edition, bundle = cenario()
    ia = Falsa()
    registro = analise.analisar_edicao(edition, bundle, config, backend=ia, enrich_fn=paginas)
    assert registro["motivo"] is None and registro["falhas"] == 0
    assert len(registro["analises"]) == 6  # política e economia, 3 veículos cada
    assert [c["label"] for c in ia.calls] == ["termômetro 1/2", "termômetro 2/2"]  # 4 textos por chamada
    assert ia.calls[0]["system"] == analise.SISTEMA and "Falas diretas:" in ia.calls[0]["user_text"]
    primeira = registro["analises"][0]
    t = analise.montar_texto(
        bundle.articles[0], edition.stories[0], paginas([bundle.articles[0]])[bundle.articles[0].id],
        editoria="Política", tz=__import__("zoneinfo").ZoneInfo(config.site.timezone), data_edicao=DATA,
    )
    esperado = calcular(saida_valida(t.paragrafos), t.paragrafos, analise.carregar_lexico(config), t.meta)
    assert (primeira["T"], primeira["rotulo"], primeira["confianca"]) == (esperado["T"], esperado["rotulo"], esperado["confianca"])
    assert primeira["qualidade_apuracao"]["Q"] == esperado["qualidade_apuracao"]["Q"]
    assert primeira["versao_calculo"] == "1.1.0" and primeira["analisado_em"] == DATA
    assert primeira["evidencias"] and all(e["citacao"] for e in primeira["evidencias"])
    assert registro["provedor"] == "falsa" and registro["tokens"] == {"entrada": 200, "saida": 400}


def test_saida_invalida_ou_ausente_vira_falha_sem_derrubar_as_outras(config):
    edition, bundle = cenario(n_materias=1, por_materia=3)
    ia = Falsa(invalidas={"T1"}, omitir={"T2"})
    registro = analise.analisar_edicao(edition, bundle, config, backend=ia, enrich_fn=paginas)
    assert len(registro["analises"]) == 1 and registro["falhas"] == 2


def test_sem_ia_nao_consulta_paginas_e_registra_o_motivo(config):
    edition, bundle = cenario()

    def proibido(articles):
        raise AssertionError("sem IA, nenhuma página é consultada")

    registro = analise.analisar_edicao(edition, bundle, config, backend=None, enrich_fn=proibido)
    assert registro["analises"] == [] and registro["motivo"] == "nenhuma IA disponível nesta execução"


def test_texto_curto_nao_vai_a_ia(config):
    edition, bundle = cenario(n_materias=1, por_materia=2)
    ia = Falsa()
    registro = analise.analisar_edicao(edition, bundle, config, backend=ia, enrich_fn=lambda arts: {})
    assert ia.calls == [] and registro["textos_curtos"] == 2 and "corpo suficiente" in registro["motivo"]


def test_analises_anteriores_sao_reaproveitadas_sem_nova_chamada(config):
    edition, bundle = cenario(n_materias=1, por_materia=2)
    primeira = analise.analisar_edicao(edition, bundle, config, backend=Falsa(), enrich_fn=paginas)
    anteriores = {a["id"]: a for a in primeira["analises"]}
    ia = Falsa()
    segunda = analise.analisar_edicao(edition, bundle, config, backend=ia, enrich_fn=paginas, anteriores=anteriores)
    assert ia.calls == [] and segunda["analises"] == primeira["analises"]


def test_teto_de_chamadas(config):
    edition, bundle = cenario(n_materias=2, por_materia=3)
    config.termometro = dataclasses.replace(config.termometro, textos_por_chamada=1, max_chamadas=2)
    ia = Falsa()
    registro = analise.analisar_edicao(edition, bundle, config, backend=ia, enrich_fn=paginas)
    assert len(ia.calls) == 2 and len(registro["analises"]) == 2 and registro["falhas"] == 4


def test_make_edition_reaproveita_a_cadeia_da_edicao(config, monkeypatch):
    edition, bundle = cenario()
    ia = Falsa()
    monkeypatch.setattr(edit, "_backends", lambda config, client: ([("falsa", lambda: ia)], []))
    monkeypatch.setattr(edit, "build_llm_edition", lambda *a, **k: (_ for _ in ()).throw(LLMUnavailable("sem edição")))
    monkeypatch.setattr(edit, "build_heuristic_edition", lambda *a, **k: copy.deepcopy(edition))
    sem = edit.make_edition(bundle, config, now=pipeline.parse_iso_datetime(bundle.collected_at), enrich_fn=paginas)
    assert sem.termometro is None and ia.calls == []  # sem contexto, o termômetro não roda
    com = edit.make_edition(
        bundle, config, now=pipeline.parse_iso_datetime(bundle.collected_at), enrich_fn=paginas,
        termometro=edit.ContextoTermometro(),
    )
    assert len(com.termometro["analises"]) == 6 and len(ia.calls) == 2


# ── histórico ────────────────────────────────────────────────────────────────


def _analise(i: str, dia: str, T: int | None = -20, **extra: Any) -> dict[str, Any]:
    base = {
        "id": i, "url": f"https://x.com/{i}", "titulo": f"Texto {i}", "veiculo": "Folha", "editoria": "Política",
        "secao": "politica", "materia": "m", "data_publicacao": dia, "analisado_em": dia, "versao_calculo": "1.1.0",
        "selo": None, "genero": "noticia", "triagem": {"relevancia_politica": "alta", "eixos": ["economico"]},
        "estado": "ok" if T is not None else "nao_se_aplica", "T": T,
        "rotulo": agregacao.rotulo_T(T) if T is not None else "Não se aplica", "confianca": "media",
        "resumo": "Resumo.", "qualidade_apuracao": {"Q": 60, "rotulo": "Regular"}, "evidencias": [],
    }
    return {**base, **extra}


def test_mesclar_dia_guarda_so_analises_do_dia_e_nunca_apaga():
    existente = {"data": DATA, "analises": [_analise("a", DATA), _analise("b", DATA)]}
    novo = {"data": DATA, "analises": [_analise("b", DATA, T=10), _analise("c", "2026-09-28", analisado_em="2026-09-28")]}
    dia = historico.mesclar_dia(existente, novo)
    assert {a["id"]: a["T"] for a in dia["analises"]} == {"a": -20, "b": 10}  # c é de outro dia (reaproveitada)
    assert historico.mesclar_dia(existente, {"data": DATA, "analises": [], "motivo": "sem IA"}) is None
    assert historico.mesclar_dia(None, {"data": DATA, "analises": []}) is None


def test_historico_carrega_tudo_e_ignora_arquivos_ruins(tmp_path):
    pasta = historico.pasta(tmp_path)
    pasta.mkdir(parents=True)
    (pasta / "2026-09-28.json").write_text(json.dumps({"data": "2026-09-28", "analises": [_analise("a", "2026-09-28")]}))
    (pasta / "2026-09-29.json").write_text("{quebrado")
    (pasta / "leia-me.txt").write_text("x")
    registros = historico.carregar_registros(tmp_path)
    assert [r["data"] for r in registros] == ["2026-09-28"]
    assert [a["id"] for a in historico.todas_as_analises(registros)] == ["a"]


# ── publicação ───────────────────────────────────────────────────────────────


def _edicao_com_termometro(analises: list[dict[str, Any]], motivo: str | None = None) -> Edition:
    edition = pipeline_edition(DATA)
    edition.termometro = analise.registro_do_dia(DATA, analises, motivo=motivo, provedor="falsa")
    return edition


def _publicar(edition: Edition, config, out: Path) -> dict[str, Path]:
    return pipeline.publish(edition, config, out_dir=out, subject="s", email_html="<p>e</p>", email_text="e")


def test_publish_grava_o_dia_e_a_aba_analitica(tmp_path, config):
    paths = _publicar(_edicao_com_termometro([_analise("a", DATA), _analise("b", DATA, T=None)]), config, tmp_path)
    assert paths["termometro"] == tmp_path / "data" / "termometro" / f"{DATA}.json"
    assert paths["analitico"] == tmp_path / "edicoes" / "analitico.html"
    dia = json.loads(paths["termometro"].read_text(encoding="utf-8"))
    assert [a["id"] for a in dia["analises"]] == ["a", "b"]
    pagina = paths["analitico"].read_text(encoding="utf-8")
    assert "Analítico Político" in pagina and "Texto a" in pagina
    capa = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'id="termometro"' in capa and 'href="edicoes/analitico.html"' in capa
    arquivada = (tmp_path / "edicoes" / f"{DATA}.html").read_text(encoding="utf-8")
    assert 'href="./analitico.html"' in arquivada


def test_reexecucao_sem_analise_nao_apaga_o_dia(tmp_path, config):
    _publicar(_edicao_com_termometro([_analise("a", DATA)]), config, tmp_path)
    paths = _publicar(_edicao_com_termometro([], motivo="nenhuma IA disponível nesta execução"), config, tmp_path)
    assert "termometro" not in paths and "analitico" in paths
    dia = json.loads((tmp_path / "data" / "termometro" / f"{DATA}.json").read_text(encoding="utf-8"))
    assert [a["id"] for a in dia["analises"]] == ["a"]
    assert "Texto a" in (tmp_path / "edicoes" / "analitico.html").read_text(encoding="utf-8")


def test_marca_sem_termometro_nao_ganha_nada(tmp_path):
    config = load_config(env={"QIJ_BRAND": "pbcv"})
    paths = _publicar(_edicao_com_termometro([_analise("a", DATA)]), config, tmp_path)
    assert "termometro" not in paths and "analitico" not in paths
    assert not (tmp_path / "data" / "termometro").exists()
    capa = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "termometro" not in capa and "Analítico" not in capa


# ── páginas ──────────────────────────────────────────────────────────────────


def test_bloco_do_dia_e_seguro_e_bem_formado(config):
    maliciosa = _analise("x", DATA, T=-40, veiculo="<script>alert(1)</script>", titulo="**Título** <b>forte</b>")
    edition = _edicao_com_termometro([maliciosa, _analise("y", DATA, T=12), _analise("z", DATA, T=None)])
    html = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    estrutura = parse(html)
    assert estrutura.errors == [] and estrutura.stack == []
    assert all(n == 1 for n in estrutura.ids.values())
    assert "<script>alert" not in html and "**" not in html and "*" not in visible_text(html)
    assert html.count("<script") == 2  # só os scripts da página
    assert "Termômetro do dia" in html and "3 textos analisados" in html


def test_bloco_do_dia_sem_analise_mostra_o_motivo(config):
    edition = _edicao_com_termometro([], motivo="nenhuma IA disponível nesta execução")
    view = termometro_dia_view(edition.termometro)
    assert view.vazio and view.motivo == "nenhuma IA disponível nesta execução"
    html = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert "Sem análise nesta edição: nenhuma IA disponível nesta execução." in html


def test_media_do_dia_vem_da_agregacao_e_separa_opiniao():
    analises = [_analise("a", DATA, T=-30), _analise("b", DATA, T=-10), _analise("c", DATA, T=50, selo="Opinião")]
    view = termometro_dia_view(analise.registro_do_dia(DATA, analises))
    assert view.gauge.valor == "−20" and view.gauge.rotulo == "Leve à esquerda"  # (−30 −10)/2; opinião fora
    assert any("1 texto de opinião (T +50, à parte)" == m for m in view.meta)


@pytest.mark.parametrize("dias", [0, 1, 40])
def test_pagina_analitica_bem_formada_com_qualquer_historico(config, dias):
    registros = []
    for d in range(dias):
        dia = f"2026-{8 + d // 30:02d}-{1 + d % 30:02d}"
        registros.append({"data": dia, "analises": [_analise(f"{d}-{k}", dia, T=-15 + k * 3, veiculo=f"V{k % 4}") for k in range(12)]})
    html = render_analitico_page(registros, config, home_href="../", archive_href="./", edition_date=DATA, edicoes=[DATA])
    estrutura = parse(html)
    assert estrutura.errors == [] and estrutura.stack == []
    assert all(n == 1 for n in estrutura.ids.values())
    assert "*" not in visible_text(html) and html.count("<script") == 2
    assert ("Nenhuma análise no histórico ainda." in html) == (dias == 0)
    if dias == 40:
        assert "Ranking · Veículos" in html and 'class="rk-i"' in html and 'class="ts-plot"' in html


# ── ponta a ponta ────────────────────────────────────────────────────────────


def test_pipeline_ponta_a_ponta_com_reaproveitamento(tmp_path, monkeypatch):
    from tests.fixtures.pipeline.factory import make_config

    config = make_config()
    ia = Falsa()
    monkeypatch.setattr(edit, "_backends", lambda config, client: ([("falsa", lambda: ia)], []))
    bundle = Path(__file__).parent / "fixtures" / "bundle.json"

    def enriquecer(articles):
        return {a.id: PageInfo(text=CORPO) for a in articles}

    result = pipeline.run(config, out_dir=tmp_path, bundle_path=bundle, send_email=False, enrich_fn=enriquecer, env={})
    analisadas = result.edition.termometro["analises"]
    assert analisadas and {"termometro", "analitico"} <= set(result.outputs)
    chamadas = len([c for c in ia.calls if c["label"].startswith("termômetro")])
    assert chamadas == -(-len(analisadas) // config.termometro.textos_por_chamada)
    # refazer o dia: o que já foi analisado é reaproveitado (não volta à IA) e continua no histórico
    primeiros = {a["id"] for a in analisadas}
    ia.calls.clear()
    again = pipeline.run(config, out_dir=tmp_path, bundle_path=bundle, send_email=False, enrich_fn=enriquecer, env={})
    enviados = sum(c["user_text"].count('<texto id="') for c in ia.calls if c["label"].startswith("termômetro"))
    segunda = {a["id"] for a in again.edition.termometro["analises"]}
    assert primeiros <= segunda and enviados == len(segunda - primeiros)
    dia = json.loads(result.outputs["termometro"].read_text(encoding="utf-8"))
    assert {a["id"] for a in dia["analises"]} == segunda
