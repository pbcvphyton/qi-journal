"""Testes do modo blocos (compilação por editoria), da cadeia de editores por IA
(quem estoura o limite passa a demanda ao seguinte), do racional e da lista
completa do dia."""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace

import pytest

from qijournal.config import load_config
from qijournal.edit import blocks, chatapi, make_edition
from qijournal.edit.chain import BackendChain
from qijournal.edit.chatapi import ChatBackend
from qijournal.edit.cluster import rank_clusters
from qijournal.edit.llm import LLMUnavailable
from qijournal.render.email import render_email
from qijournal.render.web import render_edition_page, render_index_page
from tests.fixtures.editor.chat_fakes import CANDIDATE_KEY_RE, KEY, FakeMistral, error, ok
from tests.fixtures.editor.factory import NOW, sample_bundle

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}
GPT = "openai/gpt-5-5"
SENSENOVA = "sensenova-6.8-flash-lite"


@pytest.fixture(scope="module")
def base_config():
    return load_config(env={})


def with_api(config, name, **changes):
    apis = dict(config.llm.apis)
    apis[name] = dataclasses.replace(apis[name], **changes)
    return dataclasses.replace(config, llm=dataclasses.replace(config.llm, apis=apis))


def with_llm(config, **changes):
    return dataclasses.replace(config, llm=dataclasses.replace(config.llm, **changes))


@pytest.fixture
def config(base_config):
    # chamadas uma de cada vez (ordem previsível nos testes)
    return with_api(with_api(base_config, "aiml", parallel=1), "sensenova", parallel=1)


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(chatapi, "RETRY_WAIT_SECONDS", 0.0)


class Router:
    """``post`` falso que encaminha pelo endereço da API (um editor falso por provedor)."""

    def __init__(self, **fakes: FakeMistral) -> None:
        self.fakes = fakes

    def __call__(self, url, headers, body, timeout):
        for host, fake in self.fakes.items():
            if host in url:
                return fake(url, headers, body, timeout)
        raise AssertionError(f"API inesperada: {url}")


def run(config, monkeypatch, router, *keys):
    for name in keys:
        monkeypatch.setenv(name, KEY)
    monkeypatch.setattr(chatapi, "http_post", router)
    return make_edition(sample_bundle(), config, now=NOW, enrich_fn=lambda articles: {})


def block_prompts(fake):
    return [r["messages"][1]["content"] for r in fake.requests if fake.stage(r) == "bloco"]


# ── compilação por editoria ────────────────────────────────────────────────


def test_blocks_by_editoria_compile_every_news_item(config, monkeypatch, caplog):
    fake = FakeMistral(model=GPT)
    caplog.set_level(logging.INFO)
    edition = run(config, monkeypatch, Router(aimlapi=fake), "AIMLAPI_KEY")

    assert edition.mode == "ai" and edition.model == GPT
    labels = fake.labels()
    assert labels[-1] == "fechamento" and all(label.startswith("bloco_") for label in labels[:-1])
    assert len(labels) <= config.llm.max_calls
    # cada bloco é uma editoria e todas as notícias vão para exatamente um bloco
    prompts = block_prompts(fake)
    names = [g.name for g in config.llm.block_groups]
    assert all(any(f": {name} (editorias:" in p for name in names) for p in prompts)
    clusters = rank_clusters(sample_bundle().articles, config, now=NOW)
    for number in range(1, len(clusters) + 1):
        assert sum(f'<grupo id="c{number}" ' in p for p in prompts) == 1
    # AIML (GPT-5.5): parâmetros de modelo de raciocínio
    body = fake.requests[0]
    assert "max_completion_tokens" in body and "temperature" not in body and "max_tokens" not in body

    assert edition.editorial.startswith("O dia combina") and len(edition.briefing) == 4
    lead = edition.story(edition.lead)
    assert lead.block in names
    covered = [s for s in edition.stories if s.coverage]
    assert covered and all(s.coverage.article_ids == s.article_ids for s in covered)
    framing = [o.framing for s in covered for o in s.coverage.outlets]
    assert all(f.startswith("Interpretação do foco de") for f in framing)

    rationale = edition.rationale
    assert rationale.method == "blocos" and rationale.provider == "aiml"
    assert rationale.articles == len(sample_bundle().articles) == sum(b.articles for b in rationale.blocks)
    assert all(b.ok for b in rationale.blocks) and {b.name for b in rationale.blocks} <= set(names)
    assert rationale.calls == len(labels)
    assert KEY not in caplog.text


def test_index_lists_every_article_exactly_once(config, monkeypatch):
    edition = run(config, monkeypatch, Router(aimlapi=FakeMistral(model=GPT)), "AIMLAPI_KEY")
    urls = [item.url for topic in edition.index for item in topic.items]
    assert sorted(urls) == sorted(a.url for a in sample_bundle().articles)
    story_ids = {s.id for s in edition.stories}
    linked = [t.story_id for t in edition.index if t.story_id]
    assert set(linked) == story_ids
    sections = [t.section for t in edition.index]
    order = {s: i for i, s in enumerate(config.section_ids)}
    assert sections == sorted(sections, key=order.get)  # por editoria, na ordem do jornal


def test_heuristic_edition_also_keeps_every_article(config):
    edition = make_edition(sample_bundle(), config, now=NOW, use_llm=False)
    assert edition.rationale.method == "automática" and edition.rationale.articles == len(sample_bundle().articles)
    assert sum(len(t.items) for t in edition.index) == len(sample_bundle().articles)


def test_failed_block_keeps_the_rest_of_the_edition(config, monkeypatch, caplog):
    monkeypatch.setattr(blocks, "MIN_STORIES", 2)
    fake = FakeMistral(model=GPT)
    real = fake.__call__

    def post(url, headers, body, timeout):
        if b'"bloco_2_' in body:
            return error(409, "falhou")
        return real(url, headers, body, timeout)

    caplog.set_level(logging.WARNING)
    edition = run(config, monkeypatch, post, "AIMLAPI_KEY")
    assert edition.mode == "ai"
    assert [b.ok for b in edition.rationale.blocks].count(False) == 1
    assert "ficam só na lista completa do dia" in caplog.text
    # as notícias do bloco que falhou continuam na lista completa
    assert sum(len(t.items) for t in edition.index) == len(sample_bundle().articles)


def test_closing_failure_orders_by_importance_without_editorial(config, monkeypatch):
    fake = FakeMistral({"fechamento": error(409)}, model=GPT)
    edition = run(config, monkeypatch, Router(aimlapi=fake), "AIMLAPI_KEY")
    assert edition.mode == "ai" and edition.editorial == ""
    assert edition.briefing  # "Em 1 minuto" das manchetes


def test_closing_merges_duplicates_across_blocks(config, monkeypatch):
    fake = FakeMistral(model=GPT)
    seen: dict[str, list[str]] = {}

    def closing(body):
        keys = CANDIDATE_KEY_RE.findall(body["messages"][1]["content"])
        pair = [next(k for k in keys if k.startswith("b1s")), next(k for k in keys if k.startswith("b2s"))]
        seen.update(keys=keys, pair=pair)
        return ok({"duplicates": [pair], "stories": keys, "lead": pair[0], "editorial": "E.", "briefing": []})

    fake.overrides["fechamento"] = closing
    edition = run(config, monkeypatch, Router(aimlapi=fake), "AIMLAPI_KEY")
    assert len(edition.stories) == len(seen["keys"]) - 1  # a repetida saiu
    # a matéria que ficou (a manchete) ganhou as notícias da repetida, de outro bloco
    clusters = rank_clusters(sample_bundle().articles, config, now=NOW)
    home = {a.id: i for i, c in enumerate(clusters) for a in c.articles}
    lead = edition.story(edition.lead)
    assert len({home[a] for a in lead.article_ids}) == 2
    assert edition.rationale.topics == edition.rationale.groups - 1


# ── cadeia de editores: quem estoura o limite passa a demanda ────────────────


def test_editor_over_its_request_cap_passes_the_rest_to_the_next(config, monkeypatch, caplog):
    capped = with_api(config, "aiml", max_requests=2)
    aiml, sensenova = FakeMistral(model=GPT), FakeMistral(model=SENSENOVA)
    caplog.set_level(logging.WARNING)
    edition = run(capped, monkeypatch, Router(aimlapi=aiml, sensenova=sensenova), "AIMLAPI_KEY", "SENSENOVA_API_KEY")

    assert len(aiml.requests) == 2  # o teto do plano gratuito
    assert sensenova.labels()[-1] == "fechamento" and len(sensenova.requests) >= 1
    assert all(b.ok for b in edition.rationale.blocks)  # toda a demanda compilada
    assert edition.rationale.provider == "aiml → sensenova"
    assert edition.model == f"{GPT} + {SENSENOVA}"
    assert "estourou o limite" in caplog.text and "passando a demanda para sensenova" in caplog.text


def test_persistent_429_moves_the_demand_to_the_next_editor(config, monkeypatch):
    aiml = FakeMistral({"bloco": lambda body: error(429, "limite"), "fechamento": error(429)}, model=GPT)
    sensenova = FakeMistral(model=SENSENOVA)
    edition = run(config, monkeypatch, Router(aimlapi=aiml, sensenova=sensenova), "AIMLAPI_KEY", "SENSENOVA_API_KEY")
    assert len(aiml.requests) == config.llm.apis["aiml"].max_retries + 1  # 1ª chamada + nova tentativa
    assert edition.mode == "ai" and edition.rationale.provider == "sensenova"
    assert all(b.ok for b in edition.rationale.blocks)


def test_content_error_retries_only_that_call_on_the_next_editor(config, monkeypatch):
    cut = [ok({"stories": []}, finish="length")]
    aiml = FakeMistral({"fechamento": lambda body: cut}, model=GPT)
    sensenova = FakeMistral(model=SENSENOVA)
    edition = run(config, monkeypatch, Router(aimlapi=aiml, sensenova=sensenova), "AIMLAPI_KEY", "SENSENOVA_API_KEY")
    assert sensenova.labels() == ["fechamento"]  # só a chamada que falhou foi refeita
    assert edition.editorial.startswith("O dia combina")
    assert edition.rationale.provider == "aiml → sensenova"


def test_every_editor_over_the_limit_falls_back_to_the_automatic_edition(config, monkeypatch, caplog):
    aiml = FakeMistral({"bloco": lambda body: error(401), "fechamento": error(401)}, model=GPT)
    caplog.set_level(logging.WARNING)
    edition = run(config, monkeypatch, Router(aimlapi=aiml), "AIMLAPI_KEY")
    assert edition.mode == "heuristic" and len(aiml.requests) == 1
    assert "usando a edição automática" in caplog.text


def test_chain_uses_the_tightest_limits_and_each_editor_its_own_output():
    small = SimpleNamespace(
        provider="a", model="m", max_tokens_select=8000, max_tokens_write=8000, context_tokens=64000, usage=None
    )
    big = SimpleNamespace(
        provider="b", model="n", max_tokens_select=64000, max_tokens_write=64000, context_tokens=1_000_000, usage=None
    )
    seen = []

    def call(backend):
        def fn(**kwargs):
            seen.append((backend.provider, kwargs["max_tokens"]))
            if backend is small:
                raise LLMUnavailable("teto", exhausted=True)
            return {"x": 1}

        return fn

    small.call, big.call = call(small), call(big)
    chain = BackendChain([small, big])
    assert (chain.context_tokens, chain.max_tokens_write) == (64000, 8000)
    assert chain.call(label="x", system="s", user_text="t", schema=SCHEMA, max_tokens=chain.max_tokens_write) == {"x": 1}
    assert seen == [("a", 8000), ("b", 64000)] and small.exhausted
    chain.call(label="y", system="s", user_text="t", schema=SCHEMA, max_tokens=chain.max_tokens_write)
    assert seen[-1] == ("b", 64000) and len(seen) == 3  # o esgotado não é chamado de novo


def test_request_cap_is_an_exhausted_error(config):
    capped = with_api(config, "aiml", max_requests=1)
    backend = ChatBackend("aiml", capped, api_key=KEY, post=FakeMistral({"pauta": ok({"x": 1})}))
    backend.call(label="pauta", system="s", user_text="t", schema=SCHEMA, max_tokens=100)
    with pytest.raises(LLMUnavailable, match="teto de 1 requisições") as excinfo:
        backend.call(label="pauta", system="s", user_text="t", schema=SCHEMA, max_tokens=100)
    assert excinfo.value.exhausted


# ── planejamento dos blocos ────────────────────────────────────────────────


def _planning(config):
    clusters = rank_clusters(sample_bundle().articles, config, now=NOW)
    order = list(range(len(clusters)))
    return clusters, order


def test_oversized_editoria_is_split_in_equal_parts_up_to_max_calls(config):
    clusters, order = _planning(config)
    sizes = {i: 600 for i in order}
    backend = SimpleNamespace(context_tokens=0, max_tokens_write=64000)
    # entrada que cabe numa chamada: ~1.000 caracteres (2 grupos por parte, no máximo)
    backend.context_tokens = backend.max_tokens_write + blocks.PROMPT_OVERHEAD_TOKENS + 1000 // blocks.CHARS_PER_TOKEN + 1
    parts = blocks.plan_parts(order, clusters, sizes, config, backend)
    assert len(parts) <= blocks.max_blocks(config)
    assert sorted(i for p in parts for i in p.indexes) == order  # cada grupo em uma parte só
    assert any("(parte 1 de" in p.name for p in parts)


def test_editoria_blocks_follow_the_news_desk(config):
    clusters, order = _planning(config)
    sizes = {i: 500 for i in order}
    backend = SimpleNamespace(context_tokens=1_000_000, max_tokens_write=64000)
    parts = blocks.plan_parts(order, clusters, sizes, config, backend)
    home = {s: g.name for g in config.llm.block_groups for s in g.sections}
    for part in parts:
        assert {home[clusters[i].section] for i in part.indexes} == {part.name}
    assert sum(p.stories for p in parts) >= min(len(order), config.edition.target_stories)


def test_without_block_groups_the_news_is_split_in_equal_parts(config):
    equal = with_llm(config, block_groups=[], blocks=3)
    clusters, order = _planning(equal)
    sizes = {i: 100 for i in order}
    backend = SimpleNamespace(context_tokens=1_000_000, max_tokens_write=64000)
    parts = blocks.plan_parts(order, clusters, sizes, equal, backend)
    assert [p.name for p in parts] == ["Bloco 1 de 3", "Bloco 2 de 3", "Bloco 3 de 3"]
    counts = [len(p.indexes) for p in parts]
    assert max(counts) - min(counts) <= 1


def test_split_equal_balances_by_size():
    sizes = {0: 10, 1: 10, 2: 10, 3: 10, 4: 40}
    assert blocks.split_equal([0, 1, 2, 3, 4], sizes, 2) == [[0, 1, 2, 3], [4]]


# ── racional e lista completa na página e no e-mail ─────────────────────────


def test_rationale_and_full_list_are_rendered(config, monkeypatch):
    edition = run(config, monkeypatch, Router(aimlapi=FakeMistral(model=GPT)), "AIMLAPI_KEY")
    total = len(sample_bundle().articles)
    page = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert '<p class="rat-strip">' in page and 'id="racional"' in page
    assert "blocos por editoria" in page and "Nenhuma notícia foi descartada" in page
    assert f'href="{config.site.base_url}edicoes/{edition.date}-todas.html"' in page  # a lista fica em página própria
    assert 'class="todas"' not in page
    assert "Compilada de" in page and "· bloco " in page  # de onde saiu cada matéria
    assert "Interpretação do foco de" in page  # analítico por veículo

    everything = render_index_page(edition, config, home_href="../", edition_href=f"{edition.date}.html")
    assert f"Todas as notícias do dia <span>{total}</span>" in everything
    assert everything.count('target="_blank" rel="noopener"') == total  # cada notícia com o link original
    lead = edition.story(edition.lead)
    assert f'href="{edition.date}.html#s-{lead.id}"' in everything  # assunto que virou matéria

    subject, html, plain = render_email(edition, config)
    assert "Como foi compilada" in html and f"{edition.date}-todas.html" in html
    assert "COMO FOI COMPILADA" in plain and f"{edition.date}-todas.html" in plain
