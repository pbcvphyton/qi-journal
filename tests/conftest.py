"""Configuração comum dos testes."""

from __future__ import annotations

import pytest

from qijournal.edit import llm


@pytest.fixture(autouse=True)
def _no_llm_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nova tentativa após erro passageiro da IA espera 15 s em produção; nos testes, não."""
    monkeypatch.setattr(llm, "RETRY_WAIT_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _no_real_ai_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chaves de IA do ambiente de quem roda os testes nunca chamam as APIs de verdade."""
    for name in ("MISTRAL_API_KEY", "AIMLAPI_KEY", "SENSENOVA_API_KEY", "MOONSHOT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
