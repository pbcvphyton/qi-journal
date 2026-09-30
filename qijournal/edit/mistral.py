"""Editor Mistral: chamadas à API de chat da Mistral com saída estruturada.

:class:`MistralBackend` tem a mesma interface do ``ClaudeBackend`` (ver
:mod:`qijournal.edit.llm`) e liga a análise completa: todas as notícias do dia
agrupadas por assunto, redação em lotes e cobertura comparada.

Cada chamada é um ``POST {mistral_base_url}/chat/completions`` com o JSON Schema
da etapa em ``response_format`` (``json_schema`` estrito). Se a API recusar o
schema (400/422), a mesma chamada é refeita no modo ``json_object``, com o schema
descrito no prompt. Erros passageiros (429, 5xx, queda de conexão, tempo
esgotado) ganham novas tentativas enquanto couberem no prazo da edição.

A chave vem de ``MISTRAL_API_KEY`` e nunca aparece em logs nem em mensagens de erro.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from qijournal import text
from qijournal.config import Config
from qijournal.edit.llm import LLMUnavailable, _Usage

log = logging.getLogger(__name__)

API_KEY_ENV = "MISTRAL_API_KEY"
TEMPERATURE = 0.2  # redação sóbria e agrupamento estável
MAX_RETRIES = 3  # novas tentativas após erro passageiro
RETRY_WAIT_SECONDS = 10.0  # espera base (multiplicada pela tentativa) quando a API não diz quanto esperar
RETRY_AFTER_MAX = 60.0
ERROR_DETAIL_CHARS = 200

_SCHEMA_NAME_RE = re.compile(r"[^a-z0-9_-]+")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)

# (status, cabeçalhos, corpo) da resposta HTTP
HttpResponse = tuple[int, dict[str, str], bytes]
PostFn = Callable[[str, dict[str, str], bytes, float], HttpResponse]


class _TransientError(Exception):
    """Erro que vale nova tentativa (a mensagem já é segura para o log)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> HttpResponse:
    """POST com a biblioteca padrão; devolve a resposta mesmo com status de erro.

    Falhas de rede e tempo esgotado viram :class:`_TransientError`.
    """
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() or b""
    except (TimeoutError, socket.timeout) as exc:
        raise _TransientError("tempo esgotado aguardando a API") from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        reason = getattr(exc, "reason", exc)  # ex.: IncompleteRead, conexão encerrada no meio da resposta
        raise _TransientError(f"falha de conexão com a API ({type(reason).__name__})") from exc


def _schema_name(label: str) -> str:
    """Nome do schema aceito pela API (``[a-z0-9_-]``): "redação 2" → "redacao_2"."""
    return _SCHEMA_NAME_RE.sub("_", text.normalize(label)).strip("_") or "resposta"


def _error_detail(body: bytes) -> str:
    """Mensagem curta do corpo de erro da API (JSON ``message``/``detail`` ou texto)."""
    raw = body.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        detail = data.get("message") or data.get("detail") or data.get("error") or raw
        raw = json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail
    return text.truncate(" ".join(raw.split()), ERROR_DETAIL_CHARS)


def _retry_after(headers: dict[str, str]) -> float | None:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return min(max(float(value), 0.0), RETRY_AFTER_MAX)
            except ValueError:
                return None
    return None


def _content_text(content: Any) -> str:
    """Texto da mensagem: string ou lista de trechos ``{"type": "text", "text": …}``."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for chunk in content:
            if isinstance(chunk, dict) and chunk.get("type") == "text" and isinstance(chunk.get("text"), str):
                parts.append(chunk["text"])
            elif isinstance(chunk, str):
                parts.append(chunk)
        return "".join(parts)
    return ""


def parse_json_content(raw: str, label: str) -> Any:
    """JSON da resposta; tolera cercas de código (```json … ```)."""
    candidate = raw.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        raise LLMUnavailable(f"{label}: JSON inválido na resposta do modelo") from None


class MistralBackend:
    """Editor Mistral (análise completa: todas as notícias + cobertura comparada)."""

    provider = "mistral"
    full_analysis = True

    def __init__(self, config: Config, *, api_key: str | None = None, post: PostFn | None = None) -> None:
        key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")).strip()
        if not key:
            raise LLMUnavailable(f"{API_KEY_ENV} não definida")
        self._key = key
        self._post = post or http_post
        self.config = config
        self.usage = _Usage()
        llm = config.llm
        self.max_tokens_select = llm.mistral_max_tokens
        self.max_tokens_write = llm.mistral_max_tokens
        self.write_batch_size = max(0, llm.write_batch_size)
        self.parallel = max(1, llm.mistral_parallel)
        self._interval = max(0.0, float(llm.mistral_min_interval_seconds))
        self._next_slot = 0.0  # time.monotonic() a partir do qual a próxima requisição pode sair
        self._slot_lock = threading.Lock()
        self._url = llm.mistral_base_url.rstrip("/") + "/chat/completions"
        self._json_schema_ok = True  # vira False se a API recusar response_format json_schema

    @property
    def model(self) -> str:
        return self.config.llm.mistral_model

    def __repr__(self) -> str:  # nunca mostra a chave
        return f"MistralBackend(model={self.model!r})"

    # ── requisição ─────────────────────────────────────────────────────────

    def _body(self, system: str, user_text: str, schema: dict[str, Any], max_tokens: int, label: str) -> bytes:
        if self._json_schema_ok:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": _schema_name(label), "schema": schema, "strict": True},
            }
        else:
            response_format = {"type": "json_object"}
            system = (
                f"{system}\n\nResponda com um único objeto JSON válido que siga exatamente este JSON Schema:\n"
                f"{json.dumps(schema, ensure_ascii=False)}"
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
            "max_tokens": max_tokens,
            "temperature": TEMPERATURE,
            "response_format": response_format,
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _wait_slot(self, label: str, deadline: float | None) -> None:
        """Respeita o intervalo mínimo entre requisições (limite por minuto do plano gratuito)."""
        with self._slot_lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            if deadline is not None and start > deadline:
                raise LLMUnavailable(f"{label}: sem tempo no prazo da edição")
            self._next_slot = start + self._interval
        if start > now:
            time.sleep(start - now)

    def _send(self, body: bytes, label: str) -> dict[str, Any]:
        """Uma tentativa: resposta decodificada, :class:`_TransientError` ou :class:`LLMUnavailable`."""
        status, headers, raw = self._post(self._url, self._headers(), body, float(self.config.llm.timeout_seconds))
        if status == 200:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise _TransientError("resposta HTTP 200 sem JSON válido") from None
            if not isinstance(data, dict):
                raise _TransientError("resposta HTTP 200 inesperada")
            return data
        detail = _error_detail(raw)
        if status == 401:
            raise LLMUnavailable(f"{label}: autenticação recusada (401) — verifique {API_KEY_ENV}")
        if status == 403:
            raise LLMUnavailable(f"{label}: chave sem permissão para {self.model} (403): {detail}")
        if status == 404:
            raise LLMUnavailable(f"{label}: modelo {self.model} ou endereço da API não encontrado (404)")
        if status in (400, 422) and self._json_schema_ok:  # schema recusado: uma vez no modo json_object
            raise _SchemaRejected(f"{status}: {detail}")
        if status == 429 or status >= 500:
            raise _TransientError(f"erro da API ({status}): {detail}", _retry_after(headers))
        raise LLMUnavailable(f"{label}: erro da API ({status}): {detail}")

    # ── interface do editor ────────────────────────────────────────────────

    def call(
        self,
        *,
        label: str,
        system: str,
        user_text: str,
        schema: dict[str, Any],
        max_tokens: int,
        deadline: float | None = None,
    ) -> Any:
        """Uma chamada com saída estruturada; devolve o JSON já decodificado."""
        log.info("IA (%s): enviando %d caracteres ao modelo %s", label, len(user_text), self.model)
        attempt = 0
        while True:
            body = self._body(system, user_text, schema, max_tokens, label)
            self._wait_slot(label, deadline)
            try:
                data = self._send(body, label)
                break
            except _SchemaRejected as exc:
                log.warning("IA (%s): a API recusou o JSON Schema (%s); usando o modo json_object", label, exc)
                self._json_schema_ok = False
                continue
            except _TransientError as exc:
                wait = exc.retry_after if exc.retry_after is not None else RETRY_WAIT_SECONDS * (attempt + 1)
                has_time = deadline is None or time.monotonic() + wait < deadline
                if attempt < MAX_RETRIES and has_time:
                    attempt += 1
                    log.warning(
                        "IA (%s): %s; nova tentativa %d/%d em %.0f s", label, exc, attempt, MAX_RETRIES, wait
                    )
                    time.sleep(wait)
                    continue
                raise LLMUnavailable(f"{label}: {exc}") from None
        return self._parse(data, label, max_tokens)

    def _parse(self, data: dict[str, Any], label: str, max_tokens: int) -> Any:
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        used_in = int(usage.get("prompt_tokens") or 0)
        used_out = int(usage.get("completion_tokens") or 0)
        served = data.get("model") if isinstance(data.get("model"), str) else self.model
        self.usage.record(served, used_in, used_out)
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        finish = choice.get("finish_reason")
        log.info("IA (%s): finish=%s, %d tokens de entrada, %d de saída", label, finish, used_in, used_out)
        if finish == "length":
            raise LLMUnavailable(f"{label}: resposta cortada ao atingir max_tokens ({max_tokens})")
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        raw = _content_text(message.get("content"))
        if not raw.strip():
            raise LLMUnavailable(f"{label}: resposta sem texto (finish_reason={finish})")
        return parse_json_content(raw, label)


class _SchemaRejected(Exception):
    """A API não aceitou ``response_format`` com ``json_schema`` (400/422)."""
