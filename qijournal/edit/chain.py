"""Cadeia de editores por IA: quando um estoura o limite, o seguinte assume.

:func:`~qijournal.edit.make_edition` monta a cadeia com todos os editores que têm
chave, na ordem de ``llm.providers`` (ex.: AIML → SenseNova → Mistral → Kimi →
Claude). Cada chamada vai para o primeiro editor que ainda tem limite:

- se ele estourar o limite (teto de requisições da edição, 429 persistente, cota
  ou saldo, chave recusada, modelo inexistente: :attr:`LLMUnavailable.exhausted`),
  sai da cadeia e a mesma chamada é refeita no seguinte, e assim sucessivamente;
- outros erros (resposta cortada, JSON inválido, falha passageira que persistiu)
  refazem só aquela chamada no seguinte; o editor continua nas próximas.

Assim, toda a demanda da edição é compilada enquanto houver algum editor com
limite. O modo da edição (blocos, etapas ou duas chamadas) é o do primeiro
editor; o tamanho dos blocos usa o menor limite de entrada e de saída entre os
editores da cadeia, para que qualquer um deles consiga assumir qualquer bloco.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from qijournal.edit.llm import LLMUnavailable

log = logging.getLogger(__name__)

DEFAULT_CONTEXT_TOKENS = 128000


class _ChainUsage:
    """Consumo somado de todos os editores da cadeia (lido na hora)."""

    def __init__(self, backends: list[Any]) -> None:
        self._backends = backends

    @property
    def input_tokens(self) -> int:
        return sum(b.usage.input_tokens for b in self._backends)

    @property
    def output_tokens(self) -> int:
        return sum(b.usage.output_tokens for b in self._backends)

    @property
    def calls(self) -> int:
        return sum(getattr(b.usage, "calls", 0) for b in self._backends)

    @property
    def served_models(self) -> list[str]:
        models: list[str] = []
        for backend in self._backends:
            models += [m for m in backend.usage.served_models if m not in models]
        return models


class BackendChain:
    """Editores em sequência, com a mesma interface de um editor (``call``)."""

    def __init__(self, backends: list[Any]) -> None:
        if not backends:
            raise ValueError("cadeia de editores vazia")
        self.backends = list(backends)
        primary = self.backends[0]
        self.usage = _ChainUsage(self.backends)
        self.full_analysis = bool(getattr(primary, "full_analysis", False))
        self.block_mode = bool(getattr(primary, "block_mode", False))
        self.write_batch_size = getattr(primary, "write_batch_size", 0)
        self.parallel = max(1, getattr(primary, "parallel", 1))
        self.max_tokens_select = min(b.max_tokens_select for b in self.backends)
        self.max_tokens_write = min(b.max_tokens_write for b in self.backends)
        self.context_tokens = min(getattr(b, "context_tokens", DEFAULT_CONTEXT_TOKENS) for b in self.backends)
        self._lock = threading.Lock()
        self._answered: list[str] = []  # editores que responderam, na ordem

    @property
    def model(self) -> str:
        return self.backends[0].model

    @property
    def provider(self) -> str:
        """Editores que redigiram a edição: "aiml" ou "aiml → sensenova"."""
        return " → ".join(self._answered) or self.backends[0].provider

    @property
    def requests(self) -> int:
        return sum(getattr(b, "requests", 0) or 0 for b in self.backends)

    def __repr__(self) -> str:
        return f"BackendChain({[b.provider for b in self.backends]!r})"

    def _own_max_tokens(self, backend: Any, max_tokens: int) -> int:
        """Saída máxima da chamada no editor que vai atendê-la (cada um usa o seu limite)."""
        if max_tokens == self.max_tokens_write:
            return backend.max_tokens_write
        if max_tokens == self.max_tokens_select:
            return backend.max_tokens_select
        return min(max_tokens, backend.max_tokens_write)

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
        errors: list[str] = []
        available = [b for b in self.backends if not getattr(b, "exhausted", False)]
        for position, backend in enumerate(available):
            if getattr(backend, "exhausted", False):  # esgotado por outra chamada em paralelo
                continue
            if deadline is not None and time.monotonic() > deadline:
                errors.append("sem tempo no prazo da edição")
                break
            following = next((b.provider for b in available[position + 1 :] if not getattr(b, "exhausted", False)), None)
            try:
                result = backend.call(
                    label=label,
                    system=system,
                    user_text=user_text,
                    schema=schema,
                    max_tokens=self._own_max_tokens(backend, max_tokens),
                    deadline=deadline,
                )
            except LLMUnavailable as exc:
                errors.append(f"{backend.provider}: {exc}")
                if exc.exhausted:
                    backend.exhausted = True
                    log.warning(
                        "IA %s estourou o limite ou ficou indisponível (%s); %s",
                        backend.provider,
                        exc,
                        f"passando a demanda para {following}" if following else "não há outro editor",
                    )
                else:
                    log.warning(
                        "IA %s falhou em %s (%s); %s",
                        backend.provider,
                        label,
                        exc,
                        f"refazendo com {following}" if following else "não há outro editor",
                    )
                continue
            with self._lock:
                if backend.provider not in self._answered:
                    self._answered.append(backend.provider)
            return result
        if not available:
            errors.append("todos os editores estouraram o limite")
        raise LLMUnavailable(f"{label}: nenhum editor conseguiu ({'; '.join(errors)})")
