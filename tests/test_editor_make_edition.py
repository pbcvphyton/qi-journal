"""Testes de ``make_edition``: IA quando possível, heurística como alternativa."""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace as NS

import anthropic
import pytest

from qijournal.config import load_config
from qijournal.edit import make_edition
from tests.fixtures.editor.factory import NOW, sample_bundle
from tests.fixtures.editor.llm_fakes import FakeClient, api_error, message, select_responder, write_responder


@pytest.fixture(scope="module")
def config():
    return load_config(env={})


class RecordingEnricher:
    """enrich_fn falso: registra os ids pedidos e devolve imagem para alguns."""

    def __init__(self, images: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.images = images or {}

    def __call__(self, articles):
        ids = [a.id for a in articles]
        self.calls.append(ids)
        return {aid: NS(image=self.images.get(aid), description=None, text=None) for aid in ids}


def test_uses_ai_when_client_is_available(config):
    client = FakeClient(select_responder(), write_responder())
    enricher = RecordingEnricher()
    edition = make_edition(sample_bundle(), config, now=NOW, client=client, enrich_fn=enricher)
    assert edition.mode == "ai"
    assert len(client.calls) == 2
    assert len(enricher.calls) == 1
    assert edition.stats.sources_total == 4 and edition.stats.llm_input_tokens == 6000


def test_falls_back_to_heuristic_on_llm_unavailable(config, caplog):
    caplog.set_level(logging.INFO, logger="qijournal.edit")
    client = FakeClient(api_error(anthropic.RateLimitError, 429))
    edition = make_edition(sample_bundle(), config, now=NOW, client=client)
    assert edition.mode == "heuristic" and edition.model is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "qijournal.edit"]
    assert len(warnings) == 1
    assert "Edição por IA indisponível" in warnings[0].getMessage()
    assert "429" in warnings[0].getMessage()


def test_falls_back_on_unexpected_errors_with_traceback(config, caplog):
    caplog.set_level(logging.INFO, logger="qijournal.edit")
    client = FakeClient(RuntimeError("bug inesperado"))
    edition = make_edition(sample_bundle(), config, now=NOW, client=client)
    assert edition.mode == "heuristic"
    errors = [r for r in caplog.records if r.name == "qijournal.edit" and r.levelno == logging.ERROR]
    assert len(errors) == 1 and errors[0].exc_info is not None
    assert isinstance(errors[0].exc_info[1], RuntimeError)


def test_refusal_falls_back(config):
    client = FakeClient(message(blocks=[], stop_reason="refusal", stop_details=NS(category="cyber")))
    assert make_edition(sample_bundle(), config, now=NOW, client=client).mode == "heuristic"


def test_no_llm_flag_and_disabled_config_never_call_the_api(config, caplog):
    caplog.set_level(logging.INFO, logger="qijournal.edit")
    client = FakeClient()
    assert make_edition(sample_bundle(), config, now=NOW, use_llm=False, client=client).mode == "heuristic"
    disabled = dataclasses.replace(config, llm=dataclasses.replace(config.llm, enabled=False))
    assert make_edition(sample_bundle(), disabled, now=NOW, client=client).mode == "heuristic"
    assert client.calls == []
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and r.name == "qijournal.edit"]
    assert any("--no-llm" in m for m in infos) and any("configuração" in m for m in infos)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_api_key_warns_and_uses_heuristic(config, caplog, monkeypatch, key):
    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    caplog.set_level(logging.INFO, logger="qijournal.edit")
    edition = make_edition(sample_bundle(), config, now=NOW)
    assert edition.mode == "heuristic"
    assert any(
        r.levelno == logging.WARNING and "ANTHROPIC_API_KEY não definida" in r.getMessage() for r in caplog.records
    )


def test_heuristic_receives_page_info_when_enrich_fn_is_given(config, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    enricher = RecordingEnricher(images={"stf-jota": "https://img.example.com/stf.jpg"})
    edition = make_edition(sample_bundle(), config, now=NOW, enrich_fn=enricher)
    assert edition.mode == "heuristic"
    assert len(enricher.calls) == 1 and "copom-valor" in enricher.calls[0]
    stf = next(s for s in edition.stories if "stf-jota" in s.article_ids)
    assert stf.image == "https://img.example.com/stf.jpg"


def test_enrichment_is_reused_after_ai_failure(config):
    # a pauta funciona (e enriquece), a redação falha → heurística reaproveita o que já foi baixado
    client = FakeClient(select_responder(), api_error(anthropic.InternalServerError, 500))
    enricher = RecordingEnricher(images={"stf-jota": "https://img.example.com/stf.jpg"})
    edition = make_edition(sample_bundle(), config, now=NOW, client=client, enrich_fn=enricher)
    assert edition.mode == "heuristic"
    assert len(enricher.calls) == 2
    first, second = enricher.calls
    assert not set(first) & set(second)  # nada é buscado duas vezes
    stf = next(s for s in edition.stories if "stf-jota" in s.article_ids)
    assert stf.image == "https://img.example.com/stf.jpg"  # veio do cache da 1ª chamada


def test_enrichment_failure_in_heuristic_path_is_tolerated(config, monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def broken(articles):
        raise OSError("sem rede")

    caplog.set_level(logging.WARNING, logger="qijournal.edit")
    edition = make_edition(sample_bundle(), config, now=NOW, enrich_fn=broken)
    assert edition.mode == "heuristic" and edition.stories
    assert "Enriquecimento das páginas falhou" in caplog.text
