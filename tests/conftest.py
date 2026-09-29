"""Configuração comum dos testes."""

from __future__ import annotations

import pytest

from qijournal.edit import llm


@pytest.fixture(autouse=True)
def _no_llm_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nova tentativa após erro passageiro da IA espera 15 s em produção; nos testes, não."""
    monkeypatch.setattr(llm, "RETRY_WAIT_SECONDS", 0.0)
