"""API da Mistral falsa para os testes do editor com análise completa.

:class:`FakeMistral` faz o papel de ``post(url, headers, body, timeout)`` do
``MistralBackend``: registra cada requisição e responde conforme a etapa (nome do
JSON Schema em ``response_format``), lendo o prompt como faria o modelo.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tests.fixtures.editor.llm_fakes import canonical_selection, canonical_writing

BLOCK_RE = re.compile(r'<assunto chave="(\w+)">(.*?)</assunto>', re.S)
OUTLET_RE = re.compile(r"^\[(v\d+)\] (.+?) \| idioma:", re.M)
KEY = "chave-de-teste-123"


def ok(data: Any, *, finish: str = "stop", usage: tuple[int, int] = (1000, 200), model: str = "mistral-large-2511"):
    body = {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(data)}, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    }
    return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


def error(status: int, message: str = "falhou", headers: dict[str, str] | None = None):
    return status, headers or {}, json.dumps({"object": "error", "message": message}).encode()


def canonical_coverage(user_text: str) -> dict[str, Any]:
    """Um lado para o 1º veículo, outro para o 2º, neutro para os demais; t* sem debate."""
    topics = []
    for key, block in BLOCK_RE.findall(user_text):
        outlets = OUTLET_RE.findall(block)
        debate = key.startswith("s")
        stances = ["a", "b"] + ["neutro"] * max(0, len(outlets) - 2)
        topics.append(
            {
                "key": key,
                "topic": f"Assunto {key}",
                "has_debate": debate,
                "side_a": "Destaca o alívio" if debate else "",
                "side_b": "Destaca o risco" if debate else "",
                "outlets": [
                    {"outlet": vkey, "stance": stances[i] if debate else "neutro", "framing": f"{name} destacou {key}"}
                    for i, (vkey, name) in enumerate(outlets)
                ],
                "conclusion": f"Conclusão de {key}.",
            }
        )
    return {"topics": topics}


class FakeMistral:
    """``post`` falso; ``overrides[etapa]`` troca a resposta de uma etapa (resposta ou função)."""

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.urls: list[str] = []
        self.overrides = dict(overrides or {})

    @staticmethod
    def stage(body: dict[str, Any]) -> str:
        """"agrupamento_1_2" → "agrupamento", "redacao_2" → "redacao"; modo json_object → "json_object"."""
        name = ((body.get("response_format") or {}).get("json_schema") or {}).get("name")
        return name.split("_")[0] if name else "json_object"

    def labels(self) -> list[str]:
        return [(r.get("response_format", {}).get("json_schema") or {}).get("name", "json_object") for r in self.requests]

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.urls.append(url)
        self.headers.append(headers)
        data = json.loads(body.decode("utf-8"))
        self.requests.append(data)
        stage = self.stage(data)
        override = self.overrides.get(stage)
        if override is not None:
            result = override(data) if callable(override) else override
            if isinstance(result, list):  # sequência: uma resposta por chamada
                return result.pop(0)
            return result
        user = data["messages"][1]["content"]
        schema = (data.get("response_format", {}).get("json_schema") or {}).get("schema", {})
        if stage == "agrupamento":
            return ok({"groups": []})
        if stage == "pauta":
            return ok(canonical_selection(user))
        if stage == "redacao":
            keys = schema["properties"]["stories"]["items"]["properties"]["key"]["enum"]
            return ok(canonical_writing(keys), usage=(5000, 3000))
        if stage == "cobertura":
            return ok(canonical_coverage(user), usage=(2000, 800))
        raise AssertionError(f"etapa inesperada: {stage}")
