"""API de chat (formato OpenAI) falsa para os testes dos editores com análise completa.

:class:`FakeMistral` faz o papel de ``post(url, headers, body, timeout)`` do
``ChatBackend``: registra cada requisição e responde conforme a etapa (nome do
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


GROUP_RE = re.compile(r'<grupo id="(c\d+)" secao="(\w+)">\n(.*?)</grupo>', re.S)
GROUP_LINE_RE = re.compile(r"^- (.+?) \((\w+), ", re.M)
ASK_RE = re.compile(r"Redija até (\d+) matérias(?: e compare até (\d+) outros assuntos)?")
CANDIDATE_KEY_RE = re.compile(r"^\[(b\d+s\d+)\]", re.M)
TOPIC_KEY_RE = re.compile(r"^\[(b\d+t\d+)\]", re.M)
TARGET_RE = re.compile(r"Escolha até (\d+) matérias")


def block_coverage(outlets: list[str]) -> dict[str, Any]:
    """Análise canônica: 1º veículo no lado A, 2º no B, os demais neutros."""
    stances = ["a", "b"] + ["neutro"] * max(0, len(outlets) - 2)
    return {
        "has_debate": len(outlets) >= 2,
        "side_a": "Destaca o alívio" if len(outlets) >= 2 else "",
        "side_b": "Destaca o risco" if len(outlets) >= 2 else "",
        "outlets": [
            {"outlet": name, "stance": stances[i], "framing": f"Interpretação do foco de {name}: prioriza o dado oficial."}
            for i, name in enumerate(outlets)
        ],
        "conclusion": "A cobertura pendeu para o alívio.",
    }


def canonical_block(user_text: str) -> dict[str, Any]:
    """Um bloco: cada grupo vira matéria, até o pedido; os seguintes com 2+ veículos, assuntos."""
    groups = GROUP_RE.findall(user_text)
    ask = ASK_RE.search(user_text)
    max_stories = int(ask.group(1)) if ask else len(groups)
    max_topics = int(ask.group(2) or 0) if ask else 0
    stories, topics = [], []
    for gid, section, body in groups:
        outlets = list(dict.fromkeys(name for name, _ in GROUP_LINE_RE.findall(body)))
        if len(stories) < max_stories:
            stories.append(
                {
                    "groups": [gid],
                    "section": section,
                    "importance": 4 if not stories else 3,
                    "headline": f"Manchete redigida {gid}",
                    "dek": f"Linha fina da matéria {gid}.",
                    "body": [f"Primeiro parágrafo {gid} com **número central**.", f"Segundo parágrafo {gid}."],
                    "why_it_matters": f"Importa para o leitor ({gid}).",
                    "coverage": block_coverage(outlets),
                }
            )
        elif len(outlets) >= 2 and len(topics) < max_topics:
            topics.append({"groups": [gid], "topic": f"Assunto {gid}", "coverage": block_coverage(outlets)})
    return {"stories": stories, "topics": topics}


def canonical_closing(user_text: str) -> dict[str, Any]:
    keys = CANDIDATE_KEY_RE.findall(user_text)
    target = TARGET_RE.search(user_text)
    chosen = keys[: int(target.group(1))] if target else keys
    return {
        "duplicates": [],
        "stories": chosen,
        "lead": chosen[0] if chosen else "",
        "editorial": "O dia combina **juros** estáveis e a compilação por editoria.",
        "briefing": ["Fato um do dia.", "Fato dois do dia.", "Fato três do dia.", "Fato quatro do dia."],
    }


class FakeMistral:
    """``post`` falso; ``overrides[etapa]`` troca a resposta de uma etapa (resposta ou função)."""

    def __init__(self, overrides: dict[str, Any] | None = None, *, model: str = "mistral-large-2511") -> None:
        self.model = model  # modelo informado nas respostas dos blocos e do fechamento
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
        if stage == "bloco":
            return ok(canonical_block(user), usage=(8000, 4000), model=self.model)
        if stage == "fechamento":
            return ok(canonical_closing(user), usage=(3000, 600), model=self.model)
        raise AssertionError(f"etapa inesperada: {stage}")
