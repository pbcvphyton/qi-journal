"""Fidelidade com o SDK real: ``anthropic.Anthropic`` sobre um transporte HTTP falso.

Garante que a chamada montada em :mod:`qijournal.edit.llm` é aceita pelo SDK
``anthropic`` 1.x (sem rede) e vira a requisição HTTP esperada pela API:
``POST /v1/messages`` com o cabeçalho beta do fallback, ``fallbacks: "default"``,
``output_config`` (effort + JSON Schema) e ``stream: true``; e que o stream SSE
de resposta é consumido até virar uma edição.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from qijournal.config import load_config
from qijournal.edit.llm import build_llm_edition
from tests.fixtures.editor.factory import NOW, sample_bundle
from tests.fixtures.editor.llm_fakes import CANONICAL_PICKS, canonical_selection, canonical_writing


def sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events).encode()


def message_stream(payload: dict[str, Any], *, message_id: str, input_tokens: int, output_tokens: int) -> bytes:
    """Stream SSE válido de uma mensagem com um único bloco de texto (o JSON)."""
    text = json.dumps(payload, ensure_ascii=False)
    half = len(text) // 2
    return sse(
        [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-5-5",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": input_tokens, "output_tokens": 1},
                    },
                },
            ),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[:half]}},
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[half:]}},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
    )


class FakeAnthropicAPI:
    """Handler do MockTransport: guarda as requisições e responde como a API."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        schema = body["output_config"]["format"]["schema"]
        user_text = body["messages"][0]["content"]
        if "lead" in schema["properties"]:  # chamada 1 — pauta
            stream = message_stream(
                canonical_selection(user_text), message_id="msg_pauta", input_tokens=7000, output_tokens=900
            )
        else:  # chamada 2 — redação
            keys = schema["properties"]["stories"]["items"]["properties"]["key"]["enum"]
            stream = message_stream(
                canonical_writing(keys), message_id="msg_redacao", input_tokens=9000, output_tokens=6000
            )
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream", "request-id": "req_teste"}, content=stream
        )


@pytest.fixture
def api() -> FakeAnthropicAPI:
    return FakeAnthropicAPI()


@pytest.fixture
def client(api: FakeAnthropicAPI) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(api)),
    )


def test_real_sdk_builds_the_expected_http_requests(api, client):
    config = load_config(env={})
    edition = build_llm_edition(sample_bundle(), config, now=NOW, client=client, enrich_fn=lambda arts: {})

    assert len(api.requests) == 2
    for request, body in zip(api.requests, api.bodies, strict=True):
        assert request.method == "POST"
        assert request.url.path == "/v1/messages"
        assert request.url.host == "api.anthropic.com"
        assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
        assert request.headers["x-api-key"] == "test"
        assert body["model"] == "claude-opus-5-5"
        assert body["stream"] is True
        assert body["fallbacks"] == "default"
        assert body["output_config"]["effort"] == config.llm.effort
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
        assert body["messages"][0]["role"] == "user"
        for forbidden in ("thinking", "temperature", "top_p", "top_k", "betas"):
            assert forbidden not in body
        assert isinstance(body["system"], str) and body["system"]
    assert [b["max_tokens"] for b in api.bodies] == [config.llm.max_tokens_select, config.llm.max_tokens_write]

    assert edition.mode == "ai" and edition.model == "claude-opus-5-5"
    assert len(edition.stories) == len(CANONICAL_PICKS)
    assert edition.story(edition.lead).headline == "Manchete redigida 1"
    assert edition.stats.llm_input_tokens == 7000 + 9000
    assert edition.stats.llm_output_tokens == 900 + 6000
