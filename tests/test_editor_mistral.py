"""Testes do editor Mistral e da análise completa (agrupamento por assunto e cobertura comparada)."""

from __future__ import annotations

import dataclasses
import json
import logging
import re

import pytest

from qijournal.config import load_config
from qijournal.edit import coverage as cov
from qijournal.edit import make_edition, mistral, topics
from qijournal.edit.cluster import rank_clusters
from qijournal.edit.llm import LLMUnavailable
from qijournal.edit.mistral import MistralBackend
from qijournal.models import Coverage, CoverageOutlet, Edition, Story
from tests.fixtures.editor.factory import NOW, make_article, sample_bundle
from tests.fixtures.editor.mistral_fakes import KEY, FakeMistral, error, ok

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}


@pytest.fixture(scope="module")
def base_config():
    return load_config(env={})


@pytest.fixture
def config(base_config):
    # sem intervalo entre requisições nos testes (em produção: plano gratuito)
    return dataclasses.replace(base_config, llm=dataclasses.replace(base_config.llm, mistral_min_interval_seconds=0))


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(mistral, "RETRY_WAIT_SECONDS", 0.0)


def backend(config, fake):
    return MistralBackend(config, api_key=KEY, post=fake)


def call(b, **kwargs):
    kwargs.setdefault("label", "pauta")
    return b.call(system="sys", user_text="texto", schema=SCHEMA, max_tokens=100, **kwargs)


# ── backend ────────────────────────────────────────────────────────────────


def test_request_follows_the_chat_completions_contract(config):
    fake = FakeMistral({"redacao": ok({"x": 1})})
    b = backend(config, fake)
    assert call(b, label="redação 2") == {"x": 1}
    assert fake.urls == ["https://api.mistral.ai/v1/chat/completions"]
    assert fake.headers[0]["Authorization"] == f"Bearer {KEY}"
    body = fake.requests[0]
    assert set(body) == {"model", "messages", "max_tokens", "temperature", "response_format"}
    assert body["model"] == "mistral-large-latest" and body["max_tokens"] == 100
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "texto"}]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "redacao_2", "schema": SCHEMA, "strict": True},
    }
    assert b.usage.input_tokens == 1000 and b.usage.served_models == ["mistral-large-2511"]


def test_missing_key_is_unavailable(config, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="MISTRAL_API_KEY não definida"):
        MistralBackend(config)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (error(401), "autenticação recusada (401)"),
        (error(403, "sem acesso"), "sem permissão"),
        (error(404), "não encontrado (404)"),
        (error(409, "conflito"), "erro da API (409): conflito"),
    ],
)
def test_fatal_errors_never_leak_the_key(config, response, message):
    b = backend(config, FakeMistral({"pauta": response}))
    with pytest.raises(LLMUnavailable, match=re.escape(message)) as excinfo:
        call(b)
    assert KEY not in str(excinfo.value) and KEY not in repr(b)


def test_transient_errors_are_retried(config, caplog):
    responses = [error(429, "limite", {"Retry-After": "0"}), error(503), ok({"x": 2})]
    fake = FakeMistral({"pauta": lambda body: responses})
    with caplog.at_level(logging.WARNING, logger="qijournal.edit.mistral"):
        assert call(backend(config, fake)) == {"x": 2}
    assert len(fake.requests) == 3 and "nova tentativa 2/3" in caplog.text


def test_transient_errors_give_up_after_the_retries(config):
    fake = FakeMistral({"pauta": lambda body: [error(500) for _ in range(10)]})
    with pytest.raises(LLMUnavailable, match=r"erro da API \(500\)"):
        call(backend(config, fake))
    assert len(fake.requests) == mistral.MAX_RETRIES + 1


def test_rejected_schema_falls_back_to_json_object(config):
    responses = [error(422, "response_format não suportado"), ok({"x": 3})]
    fake = FakeMistral({"pauta": lambda body: responses, "json_object": lambda body: responses})
    b = backend(config, fake)
    assert call(b) == {"x": 3}
    retry = fake.requests[1]
    assert retry["response_format"] == {"type": "json_object"}
    assert '"required": ["x"]' in retry["messages"][0]["content"]  # schema descrito no prompt


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('```json\n{"x": 4}\n```', {"x": 4}),
        ([{"type": "text", "text": '{"x": '}, {"type": "text", "text": "5}"}], {"x": 5}),
    ],
)
def test_content_formats(config, content, expected):
    body = json.loads(ok({})[2])
    body["choices"][0]["message"]["content"] = content
    fake = FakeMistral({"pauta": (200, {}, json.dumps(body).encode())})
    assert call(backend(config, fake)) == expected


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (ok({"x": 1}, finish="length"), "cortada ao atingir max_tokens"),
        ((200, {}, json.dumps({"choices": [{"message": {"content": "não é json"}}]}).encode()), "JSON inválido"),
        ((200, {}, json.dumps({"choices": [{"message": {"content": ""}}]}).encode()), "resposta sem texto"),
    ],
)
def test_unusable_answers(config, response, message):
    with pytest.raises(LLMUnavailable, match=message):
        call(backend(config, FakeMistral({"pauta": response})))


def test_minimum_interval_between_requests(config, monkeypatch):
    slow = dataclasses.replace(config, llm=dataclasses.replace(config.llm, mistral_min_interval_seconds=30))
    clock = [1000.0]
    sleeps: list[float] = []
    monkeypatch.setattr(mistral.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(mistral.time, "sleep", sleep)
    b = backend(slow, FakeMistral({"pauta": ok({"x": 1})}))
    call(b)
    clock[0] += 10
    call(b)
    assert sleeps == [20.0]  # a 2ª requisição espera completar os 30 s


# ── edição completa ────────────────────────────────────────────────────────


def test_make_edition_uses_mistral_first_with_full_analysis(config, monkeypatch, caplog):
    monkeypatch.setenv("MISTRAL_API_KEY", KEY)
    fake = FakeMistral()
    monkeypatch.setattr(mistral, "http_post", fake)
    caplog.set_level(logging.INFO)
    edition = make_edition(sample_bundle(), config, now=NOW, enrich_fn=lambda articles: {})
    assert edition.mode == "ai" and edition.model == "mistral-large-2511"
    assert fake.labels() == ["agrupamento_1_1", "pauta", "redacao_1", "redacao_2", "cobertura_1_1"]
    covered = [s for s in edition.stories if s.coverage]
    assert len(covered) == 5  # as matérias com 2+ veículos
    lead = edition.story(edition.lead)
    assert lead.coverage.side_a == "Destaca o alívio" and lead.coverage.side_b == "Destaca o risco"
    assert [o.stance for o in lead.coverage.outlets] == ["a", "b", "neutro", "neutro"]
    assert lead.coverage.section == lead.section
    assert edition.editorial.startswith("O dia combina")  # do 1º lote da redação
    assert edition.stats.llm_input_tokens == 1000 + 1000 + 5000 * 2 + 2000
    assert KEY not in caplog.text


def test_mistral_failure_falls_back_to_the_next_editor(config, monkeypatch, caplog):
    monkeypatch.setenv("MISTRAL_API_KEY", KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(mistral, "http_post", FakeMistral({"pauta": error(401)}))
    caplog.set_level(logging.WARNING, logger="qijournal.edit")
    edition = make_edition(sample_bundle(), config, now=NOW)
    assert edition.mode == "heuristic"
    assert "Edição por IA (mistral) indisponível" in caplog.text and "usando a edição automática" in caplog.text


def test_failed_writing_batch_uses_automatic_text(config, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", KEY)
    fake = FakeMistral()
    real = fake.__call__

    def post(url, headers, body, timeout):
        if (json.loads(body)["response_format"].get("json_schema") or {}).get("name") == "redacao_2":
            return error(409, "falhou")
        return real(url, headers, body, timeout)

    monkeypatch.setattr(mistral, "http_post", post)
    edition = make_edition(sample_bundle(), config, now=NOW, enrich_fn=lambda articles: {})
    assert edition.mode == "ai"
    written = [s for s in edition.stories if s.headline.startswith("Manchete redigida")]
    assert len(written) == 12 and len(edition.stories) == 14


def test_coverage_failure_keeps_the_edition(config, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", KEY)
    monkeypatch.setattr(mistral, "http_post", FakeMistral({"cobertura": error(409)}))
    edition = make_edition(sample_bundle(), config, now=NOW, enrich_fn=lambda articles: {})
    assert edition.mode == "ai" and not any(s.coverage for s in edition.stories) and edition.compared == []


# ── agrupamento por assunto ────────────────────────────────────────────────


def _clusters(config):
    return rank_clusters(sample_bundle().articles, config, now=NOW)


def test_group_topics_merges_the_clusters_named_by_the_model(config):
    clusters = _clusters(config)
    first, second = clusters[0], clusters[1]
    fake = FakeMistral({"agrupamento": ok({"groups": [{"ids": ["c1", "c2", "c999"], "topic": "x"}]})})
    merged = topics.group_topics(clusters, config, backend(config, fake), now=NOW)
    assert len(merged) == len(clusters) - 1
    union = next(c for c in merged if first.primary.id in {a.id for a in c.articles})
    assert {a.id for a in union.articles} == {a.id for a in first.articles + second.articles}
    prompt = fake.requests[0]["messages"][1]["content"]
    for cluster in clusters:  # todas as notícias vão ao modelo
        for article in cluster.articles:
            assert article.source_name in prompt


def test_group_topics_respects_the_size_cap(config, monkeypatch):
    monkeypatch.setattr(topics, "MAX_TOPIC_ARTICLES", 1)
    clusters = _clusters(config)
    fake = FakeMistral({"agrupamento": ok({"groups": [{"ids": ["c1", "c2"], "topic": "x"}]})})
    assert topics.group_topics(clusters, config, backend(config, fake), now=NOW) is clusters


def test_group_topics_in_batches_with_a_cross_batch_pass(config):
    small = dataclasses.replace(config, llm=dataclasses.replace(config.llm, topics_batch_chars=2000))
    clusters = _clusters(small)
    fake = FakeMistral()
    assert topics.group_topics(clusters, small, backend(small, fake), now=NOW) is clusters
    labels = fake.labels()
    assert len(labels) > 2 and labels[-1] == "agrupamento_entre_lotes"


def test_failed_grouping_keeps_the_automatic_clusters(config, caplog):
    clusters = _clusters(config)
    with caplog.at_level(logging.WARNING, logger="qijournal.edit.topics"):
        result = topics.group_topics(clusters, config, backend(config, FakeMistral({"agrupamento": error(409)})), now=NOW)
    assert result is clusters and "agrupamento automático" in caplog.text


# ── cobertura comparada ────────────────────────────────────────────────────


def _articles(*sources, prefix="x"):
    return [
        make_article(f"{prefix}-{i}", f"Título {i} {prefix}", "Resumo do fato.", source_id=src)
        for i, src in enumerate(sources)
    ]


def test_select_topics_takes_stories_then_topics_with_more_outlets(config):
    picks = [_articles("valor", "folha", prefix="p"), _articles("valor", prefix="solo")]
    three = rank_clusters(_articles("g1", "estadao", "jota", prefix="t3"), config, now=NOW)
    two = rank_clusters(_articles("conjur", "poder360", prefix="t2"), config, now=NOW)
    same_outlet = rank_clusters(_articles("valor", "valor", prefix="rep"), config, now=NOW)
    clusters = [*two, *three, *same_outlet]
    selected = cov.select_topics(picks, ["ângulo", ""], clusters, config)
    assert [t.key for t in selected][:1] == ["s1"] and selected[0].story_index == 0
    others = [t for t in selected if t.key.startswith("t")]
    assert [len(t.outlets) for t in others] == sorted((len(t.outlets) for t in others), reverse=True)
    assert all(len(t.outlets) >= 2 for t in selected)


def test_parse_topic_validates_the_answer():
    topic = cov._Topic(
        key="t1",
        hint="Pista",
        outlets=cov.outlets_for(_articles("valor", "folha", "g1")),
    )
    item = {
        "topic": "**Assunto**",
        "has_debate": True,
        "side_a": "Lado A",
        "side_b": "Lado B",
        "outlets": [{"outlet": "v1", "stance": "a", "framing": "<b>x</b>"}, {"outlet": "v2", "stance": "talvez"}],
        "conclusion": "",
    }
    result = cov.parse_topic(item, topic)
    assert result.topic == "Assunto"
    assert [(o.name, o.stance) for o in result.outlets] == [
        ("Valor Econômico", "a"),
        ("Folha de S.Paulo", "neutro"),  # posição inválida
        ("g1", "neutro"),  # veículo sem resposta
    ]
    assert result.conclusion.startswith("1 de 3 destacaram que lado A")
    assert "<" not in result.outlets[0].framing

    no_debate = cov.parse_topic({**item, "has_debate": False}, topic)
    assert not no_debate.has_debate and {o.stance for o in no_debate.outlets} == {"neutro"}
    assert no_debate.conclusion == "Os 3 veículos relataram o assunto de forma semelhante, sem leituras divergentes."


# ── modelos ────────────────────────────────────────────────────────────────


def test_coverage_round_trip_and_old_editions():
    coverage = Coverage(
        topic="Assunto",
        conclusion="Conclusão.",
        outlets=[CoverageOutlet(name="Valor", stance="a", framing="f", url="https://v.example/1")],
        side_a="A",
        side_b="B",
        section="brasil",
    )
    story = Story(
        id="s", section="brasil", headline="H", dek="D", body=["B"], sources=[], article_ids=[], coverage=coverage
    )
    assert Story.from_dict(json.loads(json.dumps(story.to_dict()))) == story
    data = json.loads(json.dumps(dataclasses.asdict(coverage)))
    assert Coverage.from_dict(data) == coverage
    old = {
        "date": "2026-09-29",
        "date_label": "x",
        "generated_at": "2026-09-29T08:07:00+00:00",
        "mode": "heuristic",
        "model": None,
        "lead": "s",
        "stories": [{k: v for k, v in story.to_dict().items() if k != "coverage"}],
    }
    edition = Edition.from_dict(old)
    assert edition.compared == [] and edition.stories[0].coverage is None
    edition.compared = [coverage]
    assert Edition.from_dict(json.loads(json.dumps(edition.to_dict()))).compared == [coverage]
