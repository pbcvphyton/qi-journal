"""Testes da edição por IA com um cliente falso (qijournal.edit.llm).

O cliente falso e as respostas canônicas ficam em ``tests/fixtures/editor/llm_fakes.py``.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import sys
import types
from types import SimpleNamespace as NS
from typing import Any

import anthropic
import httpx2
import pytest

from qijournal.config import load_config
from qijournal.edit import llm
from qijournal.edit.assemble import plain_text
from qijournal.edit.llm import (
    SELECT_SYSTEM,
    WRITE_SYSTEM,
    LLMUnavailable,
    build_llm_edition,
    select_schema,
    write_schema,
)
from tests.fixtures.editor.factory import NOW, sample_articles, sample_bundle
from tests.fixtures.editor.llm_fakes import (
    CANDIDATE_RE,
    CANONICAL_PICKS,
    PRIMARIES,
    FakeClient,
    api_error,
    canonical_selection,
    message,
    no_enrich,
    select_responder,
    short_ids,
    write_responder,
)


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


def run(config, client, **kwargs):
    kwargs.setdefault("enrich_fn", no_enrich)
    return build_llm_edition(sample_bundle(), config, now=NOW, client=client, **kwargs)


# ── caminho feliz ──────────────────────────────────────────────────────────


def test_requests_follow_the_api_contract(config):
    client = FakeClient(select_responder(), write_responder())
    run(config, client)
    assert len(client.calls) == 2
    expected = [
        (config.llm.max_tokens_select, SELECT_SYSTEM, select_schema(config.section_ids)),
        (config.llm.max_tokens_write, WRITE_SYSTEM, write_schema([f"s{i}" for i in range(1, 15)])),
    ]
    for kwargs, (max_tokens, system, schema) in zip(client.calls, expected, strict=True):
        # só estes parâmetros: nada de thinking, temperature, top_p, top_k, budget_tokens, prefill
        assert set(kwargs) == {"model", "max_tokens", "betas", "fallbacks", "output_config", "system", "messages"}
        assert kwargs["model"] == "claude-opus-5-5"
        assert kwargs["max_tokens"] == max_tokens
        assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
        assert kwargs["fallbacks"] == "default"
        assert kwargs["output_config"] == {
            "effort": config.llm.effort,
            "format": {"type": "json_schema", "schema": schema},
        }
        assert kwargs["system"] == system
        assert len(kwargs["messages"]) == 1
        assert kwargs["messages"][0]["role"] == "user"
        assert isinstance(kwargs["messages"][0]["content"], str)


def test_edition_maps_short_ids_to_articles(config):
    enriched: list[list[str]] = []

    def enrich(articles):
        enriched.append([a.id for a in articles])
        return {}

    edition = run(config, FakeClient(select_responder(), write_responder()), enrich_fn=enrich)
    assert edition.mode == "ai" and edition.model == "claude-opus-5-5"
    assert len(edition.stories) == len(CANONICAL_PICKS)
    for story, (arts, section, importance, _) in zip(edition.stories, CANONICAL_PICKS, strict=True):
        assert sorted(story.article_ids) == sorted(arts)
        assert (story.section, story.importance) == (section, importance)
    # principal = pt, maior peso, com imagem (mesmo que o modelo liste outro primeiro)
    assert [s.article_ids[0] for s in edition.stories] == PRIMARIES
    assert enriched == [PRIMARIES]  # enriquecimento só dos principais

    lead = edition.story(edition.lead)
    assert lead.headline == "Manchete redigida 1"
    assert lead.dek == "Linha fina da matéria 1."
    assert lead.body == ["Primeiro parágrafo da matéria 1 com **número central**.", "Segundo parágrafo 1."]
    assert lead.why_it_matters == "Importa para o leitor 1."
    assert lead.image == "https://img.example.com/copom.jpg"
    assert lead.published == "2026-09-29T05:07:00+00:00"
    assert [s.name for s in lead.sources] == ["Valor Econômico", "Folha de S.Paulo", "Estadão", "g1"]
    assert lead.sources[0].url == "https://valor.example.com/noticia/copom-valor"

    assert edition.editorial.startswith("O dia combina **juros**")
    assert edition.briefing[0] == "Copom mantém a **Selic** em 15%."
    assert edition.stats.llm_input_tokens == 1000 + 5000
    assert edition.stats.llm_output_tokens == 200 + 3000
    assert edition.stats.articles_considered == len(sample_articles())
    assert edition.stats.sources_failed[0]["source_id"] == "bbc"
    assert {s.id for s in edition.sections} == set(config.section_ids)


def test_selection_prompt_contents(config):
    client = FakeClient(select_responder(), write_responder())
    run(config, client)
    prompt = client.calls[0]["messages"][0]["content"]
    lines = {m.group(1): m for m in CANDIDATE_RE.finditer(prompt)}
    assert len(lines) == len(sample_articles())
    assert list(lines) == [f"a{i}" for i in range(1, len(lines) + 1)]
    first = lines["a1"].group(0)
    assert first.startswith(
        "[a1] Valor Econômico | pt | há 3h | seção-sugerida: brasil | "
        "Copom mantém Selic em 15% ao ano pela quinta reunião seguida — O Comitê de Política Monetária"
    )
    for match in lines.values():
        summary = match.group(6).partition(" — ")[2]
        assert len(summary) <= 281
    ages = {plain_text(m.group(6).split(" — ")[0]): m.group(4) for m in lines.values()}
    assert ages["European Central Bank holds rates steady for third meeting"] == "sem data"
    assert ages["Vale conclui venda de participação em unidade de metais básicos"] == "há 29h"
    assert ages["Dólar sobe"] == "há 1h"
    assert ages["Banco Central anuncia nova regra para o Pix"] == "há 4h"  # título limpo de HTML/markdown
    for section in config.sections:
        assert f"- {section.id} — {section.title}: {section.description}" in prompt
    assert f"cerca de {config.edition.target_stories} matérias" in prompt
    assert "Terça-feira, 29 de setembro de 2026" in prompt and "05:07" in prompt


def test_writing_prompt_contents(config):
    page = NS(image=None, description="Descrição curta.", text="Texto completo da página do Valor sobre o Copom.")
    client = FakeClient(select_responder(), write_responder())
    run(config, client, enrich_fn=lambda arts: {"copom-valor": page})
    prompt = client.calls[1]["messages"][0]["content"]
    assert "- Dólar: R$ 5,22 (+0,19% na variação diária)" in prompt
    assert "- Ibovespa: 182.991 pts (+1,20% na variação diária)" in prompt
    assert "- Selic: 15,00% (referência: 2026-09)" in prompt
    assert '<materia chave="s1" secao="brasil (Brasil · Economia)" importancia="5">' in prompt
    assert "Ângulo: Copom mantém a Selic em 15%" in prompt
    assert '<fonte veiculo="Valor Econômico" idioma="pt" publicado="29/09/2026 02:07">' in prompt
    assert "Texto: Texto completo da página do Valor sobre o Copom." in prompt  # PageInfo.text tem prioridade
    assert "Texto: O Banco Central manteve a Selic em 15% ao ano nesta terça." in prompt  # resumo da Folha
    estadao = (
        '<fonte veiculo="Estadão" idioma="pt" publicado="29/09/2026 02:55">\n'
        "Título: Copom mantém Selic em 15% ao ano pela quinta vez\n"
        "Texto: (sem texto)"
    )
    assert estadao in prompt
    assert prompt.count("<materia ") == len(CANONICAL_PICKS)


def test_max_candidates_limits_the_list(config):
    small = dataclasses.replace(config, edition=dataclasses.replace(config.edition, max_candidates=10))

    def respond(kwargs):  # uma matéria por candidato, como ids curtos
        prompt = kwargs["messages"][0]["content"]
        stories = [
            {"article_ids": [m.group(1)], "section": m.group(5), "importance": 3, "angle": "fato"}
            for m in CANDIDATE_RE.finditer(prompt)
        ]
        return message({"stories": stories, "lead": 0})

    client = FakeClient(respond, write_responder())
    edition = run(small, client)
    prompt = client.calls[0]["messages"][0]["content"]
    assert [m.group(1) for m in CANDIDATE_RE.finditer(prompt)] == [f"a{i}" for i in range(1, 11)]
    assert len(edition.stories) == 10
    listed = set(short_ids(prompt))
    assert {aid for s in edition.stories for aid in s.article_ids} == listed
    assert edition.stats.articles_considered == 10
    # clusters inteiros entram juntos, na ordem do ranqueamento (o Copom primeiro)
    assert {"copom-valor", "copom-folha", "copom-g1", "copom-estadao"} <= listed


# ── validação da pauta e pós-processamento da redação ──────────────────────


def test_selection_is_validated_and_corrected(config, caplog):
    def mutate(data, ids):
        stories = data["stories"]
        stories[0]["article_ids"].append("a999")  # id inexistente
        stories[1]["article_ids"].append(ids["copom-g1"])  # artigo já usado na 1ª matéria
        stories.insert(2, {"article_ids": ["a998"], "section": "brasil", "importance": 3, "angle": "vazia"})
        stories[3]["section"] = "esportes"  # Fed: seção inválida → sugestão do classificador
        stories[4]["importance"] = 9  # tarifas: fora da faixa → 5
        stories[5]["importance"] = 2.0  # inteiro vindo como float
        stories[6]["importance"] = "alta"  # inválido → 3
        data["lead"] = 1  # índice na lista original (STF)

    caplog.set_level(logging.WARNING, logger="qijournal.edit.llm")
    edition = run(config, FakeClient(select_responder(mutate), write_responder()))
    assert len(edition.stories) == len(CANONICAL_PICKS)  # a matéria vazia some
    copom, stf, fed, tarifa, fiscal, ibov = edition.stories[:6]
    assert sorted(copom.article_ids) == sorted(["copom-valor", "copom-folha", "copom-g1", "copom-estadao"])
    assert sorted(stf.article_ids) == ["stf-conjur", "stf-jota"]
    assert fed.section == "mercados"
    assert tarifa.importance == 5
    assert fiscal.importance == 2
    assert ibov.importance == 3
    assert edition.lead == stf.id
    assert "2 id(s) inexistente(s), 1 artigo(s) repetido(s), 1 seção(ões) inválida(s)" in caplog.text


def test_writing_is_cleaned_and_missing_stories_are_filled(config, caplog):
    def mutate(data):
        by_key = {s["key"]: s for s in data["stories"]}
        s1 = by_key["s1"]
        s1["headline"] = "**Copom** mantém Selic em 15% ao ano"
        s1["dek"] = "# Decisão *unânime* do <b>BC</b>"
        s1["body"] = [
            "Parágrafo com **negrito** e *itálico*.\n\n- Segundo bloco [com link](https://x.com).",
            "",
            "   ",
            "**Copom** mantém Selic em 15% ao ano",  # repete o título → descartado
            "Terceiro **parágrafo",  # negrito quebrado → removido
            "Quarto.",
            "Quinto (excede o máximo de 4).",
        ]
        s1["why_it_matters"] = "**Juros** altos por mais tempo"
        data["stories"].remove(by_key["s3"])  # faltando → texto heurístico
        by_key["s5"]["body"] = []  # inválida → texto heurístico
        by_key["s6"]["headline"] = "  "  # inválida → texto heurístico
        data["stories"].append(dict(by_key["s2"], key="s99"))  # chave desconhecida
        data["stories"].append(dict(by_key["s2"], headline="Duplicada"))  # chave repetida
        data["editorial"] = "Dia de **juros e tarifas."
        data["briefing"] = ["", "  **Selic** estável  ", "x" * 400, "Três", "Quatro", "Cinco", "Seis", "Sete"]

    caplog.set_level(logging.WARNING, logger="qijournal.edit.llm")
    edition = run(config, FakeClient(select_responder(), write_responder(mutate)))
    s1, s2, s3, _, s5, s6 = edition.stories[:6]
    assert s1.headline == "Copom mantém Selic em 15% ao ano"
    assert s1.dek == "Decisão unânime do BC"
    assert s1.body == [
        "Parágrafo com **negrito** e itálico.",
        "Segundo bloco com link.",
        "Terceiro parágrafo",
        "Quarto.",
    ]
    assert s1.why_it_matters == "Juros altos por mais tempo"
    assert s2.headline == "Manchete redigida 2"
    # s3 (Fed) não veio → texto heurístico, com o título original
    assert s3.headline == "Federal Reserve cuts interest rates by a quarter point"
    assert s3.why_it_matters is None and s3.body
    assert s5.headline == "Governo bloqueia R$ 12 bilhões do Orçamento para cumprir arcabouço fiscal"
    assert s6.headline == "Ibovespa fecha em alta de 1,2% puxado por bancos e Petrobras"
    assert edition.editorial == "Dia de juros e tarifas."
    assert edition.briefing[0] == "**Selic** estável"
    assert len(edition.briefing) == 6
    assert len(edition.briefing[1]) <= 201 and edition.briefing[1].endswith("…")
    assert "não redigiu 3 matéria(s) (s3, s5, s6)" in caplog.text
    assert "2 entrada(s) com chave inválida ou repetida" in caplog.text


def test_short_briefing_is_replaced_by_headlines(config):
    def mutate(data):
        data["briefing"] = ["Só um item.", "Dois."]

    edition = run(config, FakeClient(select_responder(), write_responder(mutate)))
    featured = [edition.lead, *edition.secondary, *edition.highlights]
    assert edition.briefing == [edition.story(sid).headline for sid in featured[:5]]


def test_page_info_image_and_enrich_failure(config, caplog):
    page = NS(image="https://img.example.com/stf.jpg", description=None, text=None)
    edition = run(config, FakeClient(select_responder(), write_responder()), enrich_fn=lambda arts: {"stf-jota": page})
    stf = next(s for s in edition.stories if "stf-jota" in s.article_ids)
    assert stf.image == "https://img.example.com/stf.jpg"

    def broken(articles):
        raise RuntimeError("rede fora")

    caplog.set_level(logging.WARNING, logger="qijournal.edit.llm")
    edition = run(config, FakeClient(select_responder(), write_responder()), enrich_fn=broken)
    assert edition.mode == "ai"
    assert "Enriquecimento das páginas falhou" in caplog.text


def test_default_enrich_uses_collect_module_with_configured_limit(config, monkeypatch):
    received: dict[str, Any] = {}

    def fake_enrich(articles, *, limit):
        received.update(ids=[a.id for a in articles], limit=limit)
        return {}

    module = types.ModuleType("qijournal.collect.enrich")
    module.enrich = fake_enrich
    monkeypatch.setitem(sys.modules, "qijournal.collect.enrich", module)
    build_llm_edition(sample_bundle(), config, now=NOW, client=FakeClient(select_responder(), write_responder()))
    assert received == {"ids": PRIMARIES, "limit": config.edition.enrich_limit}


def test_mid_stream_fallback_text_blocks_are_joined(config, caplog):
    def respond(kwargs):
        text = json.dumps(canonical_selection(kwargs["messages"][0]["content"]), ensure_ascii=False)
        cut = len(text) // 2
        blocks = [
            NS(type="text", text=text[:cut]),
            NS(type="fallback", **{"from": NS(model="claude-opus-5-5")}, to=NS(model="claude-opus-4-8")),
            NS(type="text", text=text[cut:]),
        ]
        return message(blocks=blocks, model="claude-opus-4-8")

    caplog.set_level(logging.INFO, logger="qijournal.edit.llm")
    edition = run(config, FakeClient(respond, write_responder()))
    assert len(edition.stories) == len(CANONICAL_PICKS)
    assert edition.model == "claude-opus-5-5"
    assert "modelo de fallback claude-opus-4-8" in caplog.text


# ── falhas → LLMUnavailable ────────────────────────────────────────────────


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "failure, match",
    [
        (
            message(blocks=[], stop_reason="refusal", stop_details=NS(category="cyber", explanation="...")),
            "recusa: .*cyber",
        ),
        (message(blocks=[], stop_reason="refusal"), "recusa: .*não informada"),
        (message(raw='{"stories": [', stop_reason="max_tokens"), "max_tokens"),
        (message(raw="isto não é JSON"), "JSON inválido"),
        (message(blocks=[NS(type="thinking", thinking="")]), "sem bloco de texto"),
        (message([1, 2, 3]), "sem a lista 'stories'"),
        (api_error(anthropic.AuthenticationError, 401), "autenticação recusada"),
        (api_error(anthropic.PermissionDeniedError, 403), r"\(403\)"),
        (api_error(anthropic.NotFoundError, 404), "não encontrado"),
        (api_error(anthropic.RateLimitError, 429), r"\(429\)"),
        (api_error(anthropic.InternalServerError, 500), r"erro da API \(500\)"),
        (api_error(anthropic.BadRequestError, 400), r"erro da API \(400\)"),
        (anthropic.APITimeoutError(request=REQUEST), "tempo esgotado"),
        (anthropic.APIConnectionError(request=REQUEST), "falha de conexão"),
    ],
)
def test_selection_failures_raise_llm_unavailable(config, failure, match):
    client = FakeClient(failure)
    with pytest.raises(LLMUnavailable, match=match):
        run(config, client)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "failure, match",
    [
        (message(blocks=[], stop_reason="refusal", stop_details=NS(category="bio")), "recusa: .*redação"),
        (message(raw="{", stop_reason="max_tokens"), "redação: resposta cortada"),
        (message(raw="[]"), "não é um objeto JSON"),
        (message({"editorial": "", "briefing": [], "stories": []}), "nenhuma matéria válida"),
        (api_error(anthropic.RateLimitError, 429), r"redação: limite de uso"),
    ],
)
def test_writing_failures_raise_llm_unavailable(config, failure, match):
    client = FakeClient(select_responder(), failure)
    with pytest.raises(LLMUnavailable, match=match):
        run(config, client)
    assert len(client.calls) == 2


def test_error_messages_never_leak_the_api_key(config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-segredo-123")
    with pytest.raises(LLMUnavailable) as info:
        run(config, FakeClient(api_error(anthropic.AuthenticationError, 401)))
    assert "sk-ant" not in str(info.value)


def test_too_few_valid_stories(config):
    few = CANONICAL_PICKS[:7]

    def respond(kwargs):
        return message(canonical_selection(kwargs["messages"][0]["content"], picks=few))

    with pytest.raises(LLMUnavailable, match="só 7 matéria"):
        run(config, FakeClient(respond))


def test_too_few_candidates_skip_the_api(config):
    client = FakeClient()
    bundle = sample_bundle(articles=sample_articles()[:5])
    with pytest.raises(LLMUnavailable, match="candidato"):
        build_llm_edition(bundle, config, now=NOW, client=client, enrich_fn=no_enrich)
    assert client.calls == []


def test_oversized_selection_is_capped_keeping_the_lead(config):
    extra = [([aid], "brasil", 1, "extra") for aid in ("xss-estadao", "ipo-bj", "aovivo-g1", "curto-g1")]
    small = dataclasses.replace(config, edition=dataclasses.replace(config.edition, target_stories=8))  # teto 16

    def respond(kwargs):
        data = canonical_selection(kwargs["messages"][0]["content"], picks=CANONICAL_PICKS + extra)
        data["lead"] = 17  # "curto-g1", além do teto
        return message(data)

    edition = run(small, FakeClient(respond, write_responder()))
    assert len(edition.stories) == 16
    assert edition.story(edition.lead).article_ids == ["curto-g1"]


# ── cliente real e chave ───────────────────────────────────────────────────


def test_without_api_key_and_client_raises(config, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="ANTHROPIC_API_KEY"):
        build_llm_edition(sample_bundle(), config, now=NOW, enrich_fn=no_enrich)


def test_client_is_built_from_config_when_key_exists(config, monkeypatch):
    created: list[dict] = []

    def factory(**kwargs):
        created.append(kwargs)
        return FakeClient(select_responder(), write_responder())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(llm.anthropic, "Anthropic", factory)
    edition = build_llm_edition(sample_bundle(), config, now=NOW, enrich_fn=no_enrich)
    assert edition.mode == "ai"
    assert created == [{"timeout": config.llm.timeout_seconds, "max_retries": 3}]


# ── schemas no modo estrito ────────────────────────────────────────────────

ALLOWED_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "description"}
FORBIDDEN_SCHEMA_KEYS = {
    "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "pattern", "format", "default", "$ref", "anyOf", "oneOf", "allOf",
}  # fmt: skip


def _walk(schema: dict, path: str, seen: list[str]) -> None:
    keys = set(schema)
    assert not keys & FORBIDDEN_SCHEMA_KEYS, f"{path}: palavras-chave proibidas {keys & FORBIDDEN_SCHEMA_KEYS}"
    assert keys <= ALLOWED_SCHEMA_KEYS, f"{path}: palavras-chave não permitidas {keys - ALLOWED_SCHEMA_KEYS}"
    seen.append(path)
    if schema["type"] == "object":
        assert schema["additionalProperties"] is False, path
        assert schema["required"] == list(schema["properties"]), path
        for name, sub in schema["properties"].items():
            _walk(sub, f"{path}.{name}", seen)
    elif schema["type"] == "array":
        _walk(schema["items"], f"{path}[]", seen)
    else:
        assert schema["type"] in {"string", "integer"}, path


@pytest.mark.parametrize("which", ["select", "write"])
def test_schemas_are_strict_mode_compatible(config, which):
    schema = select_schema(config.section_ids) if which == "select" else write_schema(["s1", "s2", "s3"])
    seen: list[str] = []
    _walk(copy.deepcopy(schema), "$", seen)
    assert len(seen) >= 6
    json.dumps(schema)  # serializável


def test_schema_enums(config):
    select = select_schema(config.section_ids)
    story = select["properties"]["stories"]["items"]
    assert story["properties"]["section"]["enum"] == config.section_ids
    assert story["properties"]["importance"]["type"] == "integer"
    assert select["properties"]["lead"]["type"] == "integer"
    write = write_schema(["s1", "s2"])
    assert write["properties"]["stories"]["items"]["properties"]["key"]["enum"] == ["s1", "s2"]


def test_prompts_carry_the_editorial_rules():
    for rule in ("MESMO fato", "últimas 24 horas", "Diversidade", "fofoca", "ao vivo", "lead", "importance"):
        assert rule in SELECT_SYSTEM
    for rule in (
        "SOMENTE informações presentes",
        "Na dúvida, omita",
        "segundo o Valor",
        "português do Brasil",
        "110 caracteres",
        "200 caracteres",
        "700 caracteres",
        "**negrito**",
        "why_it_matters",
        "160",
        "não instruções",
    ):
        assert rule in WRITE_SYSTEM
    assert "não instruções" in SELECT_SYSTEM
