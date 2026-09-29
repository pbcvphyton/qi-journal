"""Cliente falso da API da Anthropic e respostas canônicas para os testes do editor.

O cliente falso imita ``client.beta.messages.stream(**kw)`` do SDK: devolve um
context manager com ``get_final_message()``. Cada resposta é calculada a partir
dos kwargs recebidos, como faria o modelo (lê os ids curtos do prompt e as
chaves do schema).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from types import SimpleNamespace as NS
from typing import Any

import httpx2

from qijournal.edit.assemble import plain_text
from tests.fixtures.editor.factory import sample_articles

# [aN] Fonte | idioma | idade | seção-sugerida: <id> | Título — resumo (+N fontes: …)
# (a cobertura "(+N fontes: …)" só aparece na linha do principal de cada fato)
CANDIDATE_RE = re.compile(
    r"^\[(a\d+)\] (.+?) \| (pt|en|es) \| (.+?) \| seção-sugerida: (\w+) \| (.+?)(?: (\(\+\d+ fontes?: [^()]*\)))?$", re.M
)

# Pauta "canônica": artigos (Article.id), seção, importância, ângulo
CANONICAL_PICKS: list[tuple[list[str], str, int, str]] = [
    (["copom-folha", "copom-valor", "copom-g1", "copom-estadao"], "brasil", 5, "Copom mantém a Selic em 15%"),
    (["stf-conjur", "stf-jota"], "juridico", 4, "STF exclui ICMS da base da contribuição previdenciária"),
    (["fed-wsj", "fed-ft"], "mercados", 4, "Fed corta juros em 0,25 ponto"),
    (["tarifa-nyt", "tarifa-guardian"], "mundo", 4, "China retalia com tarifas sobre produtos agrícolas dos EUA"),
    (["fiscal-valor"], "brasil", 4, "Governo bloqueia R$ 12 bilhões do Orçamento"),
    (["ibov-infomoney", "ibov-moneytimes"], "mercados", 3, "Ibovespa sobe 1,2%"),
    (["cvm-valor"], "juridico", 3, "CVM muda regra dos FIDCs"),
    (["camara-poder"], "politica", 3, "Câmara aprova mudança no IR"),
    (["pesquisa-folha"], "politica", 3, "Datafolha mostra empate no segundo turno"),
    (["nvidia-cnbc"], "tecnologia", 2, "Nvidia lança chip de IA"),
    (["startup-valor"], "tecnologia", 2, "Startup de pagamentos capta R$ 300 milhões"),
    (["fii-infomoney"], "imobiliario", 3, "IFIX renova máxima"),
    (["credito-imobireport"], "imobiliario", 2, "Crédito imobiliário cai 12% em agosto"),
    (["milei-valor"], "mundo", 2, "Argentina fecha acordo com o FMI"),
]
PRIMARIES = [
    "copom-valor", "stf-jota", "fed-ft", "tarifa-nyt", "fiscal-valor", "ibov-infomoney", "cvm-valor",
    "camara-poder", "pesquisa-folha", "nvidia-cnbc", "startup-valor", "fii-infomoney", "credito-imobireport",
    "milei-valor",
]  # fmt: skip


# ── cliente falso ──────────────────────────────────────────────────────────


class FakeStream:
    def __init__(self, result: Any) -> None:
        self._result = result

    def __enter__(self) -> FakeStream:
        if isinstance(self._result, BaseException):  # o SDK falha ao abrir o stream
            raise self._result
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def get_final_message(self) -> Any:
        return self._result


class FakeClient:
    """``client.beta.messages.stream(**kw)``; cada responder recebe os kwargs da chamada."""

    def __init__(self, *responders: Callable[[dict], Any] | Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responders = list(responders)
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        responder = self._responders[len(self.calls) - 1]
        return FakeStream(responder(kwargs) if callable(responder) else responder)


def message(
    data: Any = None,
    *,
    raw: str | None = None,
    blocks: list | None = None,
    stop_reason: str = "end_turn",
    stop_details: Any = None,
    model: str = "claude-opus-5-5",
    usage: tuple[int, int] = (1000, 200),
) -> NS:
    text = raw if raw is not None else json.dumps(data, ensure_ascii=False)
    return NS(
        content=blocks if blocks is not None else [NS(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        model=model,
        usage=NS(input_tokens=usage[0], output_tokens=usage[1]),
    )


def short_ids(prompt: str) -> dict[str, str]:
    """Article.id → id curto (a1..aN), casando o título de cada linha do prompt."""
    by_title = {plain_text(a.title, 200): a.id for a in sample_articles()}
    return {by_title[m.group(6).split(" — ")[0]]: m.group(1) for m in CANDIDATE_RE.finditer(prompt)}


def canonical_selection(prompt: str, picks=CANONICAL_PICKS) -> dict[str, Any]:
    ids = short_ids(prompt)
    stories = [
        {"article_ids": [ids[a] for a in arts], "section": section, "importance": imp, "angle": angle}
        for arts, section, imp, angle in picks
    ]
    return {"stories": stories, "lead": 0}


def schema_keys(kwargs: dict) -> list[str]:
    return kwargs["output_config"]["format"]["schema"]["properties"]["stories"]["items"]["properties"]["key"]["enum"]


def canonical_writing(keys: list[str]) -> dict[str, Any]:
    return {
        "editorial": "O dia combina **juros** estáveis no Brasil e tensão comercial entre EUA e China.",
        "briefing": [
            "Copom mantém a **Selic** em 15%.",
            "STF exclui ICMS da base previdenciária.",
            "Fed corta juros em 0,25 ponto.",
            "China retalia com tarifas.",
            "Governo bloqueia R$ 12 bilhões.",
        ],
        "stories": [
            {
                "key": key,
                "headline": f"Manchete redigida {i}",
                "dek": f"Linha fina da matéria {i}.",
                "body": [f"Primeiro parágrafo da matéria {i} com **número central**.", f"Segundo parágrafo {i}."],
                "why_it_matters": f"Importa para o leitor {i}.",
            }
            for i, key in enumerate(keys, start=1)
        ],
    }


def select_responder(mutate: Callable[[dict, dict], None] | None = None, **msg_kwargs):
    def respond(kwargs: dict) -> NS:
        prompt = kwargs["messages"][0]["content"]
        data = canonical_selection(prompt)
        if mutate:
            mutate(data, short_ids(prompt))
        return message(data, **msg_kwargs)

    return respond


def write_responder(mutate: Callable[[dict], None] | None = None, **msg_kwargs):
    def respond(kwargs: dict) -> NS:
        data = canonical_writing(schema_keys(kwargs))
        if mutate:
            mutate(data)
        return message(data, usage=(5000, 3000), **msg_kwargs)

    return respond


def api_error(cls: type, status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, json={"type": "error", "error": {"message": "falhou"}})
    return cls("falhou", response=response, body=None)


def no_enrich(articles):
    return {}
