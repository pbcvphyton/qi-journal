"""Gemini (Google AI Studio) na cadeia de editores, como no jornal TSA Tech."""

from __future__ import annotations

import json

import pytest

from qijournal.config import load_config
from qijournal.edit import _backends
from qijournal.edit.chatapi import ChatBackend
from qijournal.net import USER_AGENT


def test_gemini_abre_a_cadeia_com_flash_e_flash_lite_na_mesma_chave(monkeypatch: pytest.MonkeyPatch):
    config = load_config(env={})
    assert config.llm.providers[:2] == ["gemini", "gemini_lite"]
    flash, lite = config.llm.apis["gemini"], config.llm.apis["gemini_lite"]
    assert flash.key_env == lite.key_env == "GEMINI_API_KEY"
    assert flash.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert (flash.model, lite.model) == ("gemini-flash-latest", "gemini-flash-lite-latest")
    monkeypatch.setenv("GEMINI_API_KEY", "chave-de-teste")
    disponiveis, faltando = _backends(config, None)
    assert [nome for nome, _ in disponiveis[:2]] == ["gemini", "gemini_lite"]
    assert "GEMINI_API_KEY não definida" not in faltando


def test_modelo_trocado_pela_variavel_do_github():
    config = load_config(env={"QIJ_GEMINI_MODEL": "gemini-x", "QIJ_GEMINI_LITE_MODEL": "gemini-x-lite"})
    assert (config.llm.apis["gemini"].model, config.llm.apis["gemini_lite"].model) == ("gemini-x", "gemini-x-lite")


def test_chamada_vai_ao_endpoint_compativel_com_a_openai():
    config = load_config(env={})
    pedidos = []

    def post(url, headers, body, timeout):
        pedidos.append((url, headers, json.loads(body)))
        resposta = {"model": "gemini-flash-latest", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]}
        return 200, {}, json.dumps(resposta).encode()

    backend = ChatBackend("gemini", config, api_key="chave-de-teste", post=post)
    assert backend.call(label="teste", system="s", user_text="u", schema={"type": "object"}, max_tokens=100) == {"ok": True}
    url, headers, corpo = pedidos[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert headers["Authorization"] == "Bearer chave-de-teste" and headers["User-Agent"] == USER_AGENT
    assert corpo["model"] == "gemini-flash-latest" and corpo["response_format"]["type"] == "json_schema"
