"""Edição do jornal: transforma o :class:`~qijournal.models.Bundle` coletado em uma
:class:`~qijournal.models.Edition`.

:func:`make_edition` tenta os editores por IA na ordem de ``llm.providers``
(Mistral, com a análise completa, e Claude) e, se nenhum estiver disponível ou
todos falharem, cai para a edição automática (heurística), que sempre funciona.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from qijournal.config import Config
from qijournal.edit.heuristic import build_heuristic_edition, choose_clusters
from qijournal.edit.llm import ClaudeBackend, EnrichFn, LLMUnavailable, _make_client, build_llm_edition
from qijournal.edit.mistral import API_KEY_ENV as MISTRAL_KEY_ENV
from qijournal.edit.mistral import MistralBackend
from qijournal.models import Article, Bundle, Edition

if TYPE_CHECKING:
    from qijournal.collect.enrich import PageInfo

__all__ = ["LLMUnavailable", "build_heuristic_edition", "build_llm_edition", "make_edition"]

log = logging.getLogger(__name__)


class _CachedEnricher:
    """Memoriza o enriquecimento por id de artigo.

    Se a edição por IA falhar depois de enriquecer as páginas, a edição
    heurística reaproveita o que já foi baixado em vez de buscar de novo.
    """

    def __init__(self, fn: EnrichFn) -> None:
        self._fn = fn
        self._cache: dict[str, PageInfo] = {}
        self._attempted: set[str] = set()

    def __call__(self, articles: list[Article]) -> dict[str, PageInfo]:
        pending = [a for a in articles if a.id not in self._attempted]
        if pending:
            self._attempted.update(a.id for a in pending)
            self._cache.update(self._fn(pending) or {})
        return {a.id: self._cache[a.id] for a in articles if a.id in self._cache}


def _llm_skip_reason(config: Config, use_llm: bool) -> tuple[str, int] | None:
    """Motivo (e nível de log) para nem tentar a IA, ou ``None`` se ela deve ser tentada."""
    if not use_llm:
        return "desligada nesta execução (--no-llm)", logging.INFO
    if not config.llm.enabled:
        return "desligada na configuração", logging.INFO
    return None


BackendFactory = Callable[[], Any]


def _backends(config: Config, client: Any) -> tuple[list[tuple[str, BackendFactory]], list[str]]:
    """Editores disponíveis, na ordem de ``llm.providers``, e os motivos dos que ficaram de fora.

    Um ``client`` (SDK da Anthropic ou objeto compatível, nos testes) dispensa a
    chave do Claude. Provedor desconhecido na configuração é ignorado com aviso.
    """
    available: list[tuple[str, BackendFactory]] = []
    missing: list[str] = []
    for provider in config.llm.providers:
        if provider == "mistral":
            if os.environ.get(MISTRAL_KEY_ENV, "").strip():
                available.append((provider, lambda: MistralBackend(config)))
            else:
                missing.append(f"{MISTRAL_KEY_ENV} não definida")
        elif provider == "claude":
            if client is not None or os.environ.get("ANTHROPIC_API_KEY", "").strip():
                available.append(
                    (provider, lambda: ClaudeBackend(client if client is not None else _make_client(config), config))
                )
            else:
                missing.append("ANTHROPIC_API_KEY não definida")
        else:
            log.warning("Editor por IA desconhecido em llm.providers: %r (use mistral ou claude)", provider)
    return available, missing


def _heuristic_page_info(
    bundle: Bundle, config: Config, *, now: datetime, enricher: _CachedEnricher
) -> dict[str, PageInfo] | None:
    """Enriquecimento dos artigos principais que a edição heurística vai usar."""
    try:
        return enricher([cluster.primary for cluster in choose_clusters(bundle, config, now=now)])
    except Exception:
        log.warning("Enriquecimento das páginas falhou; edição automática só com o texto dos feeds", exc_info=True)
        return None


def make_edition(
    bundle: Bundle,
    config: Config,
    *,
    now: datetime,
    use_llm: bool = True,
    client: Any = None,
    enrich_fn: EnrichFn | None = None,
) -> Edition:
    """Monta a edição do dia.

    Quando ``use_llm`` e a IA está habilitada, tenta :func:`build_llm_edition`
    com cada editor disponível, na ordem de ``llm.providers``: Mistral (com
    ``MISTRAL_API_KEY``) e Claude (com ``client`` ou ``ANTHROPIC_API_KEY``). Em
    :class:`LLMUnavailable` (ou qualquer erro inesperado, registrado com
    traceback) passa ao próximo e, por fim, a :func:`build_heuristic_edition`,
    à qual passa o enriquecimento das páginas quando ``enrich_fn`` é fornecido.
    As estatísticas da edição (fontes, artigos, tokens) vêm preenchidas pelos
    próprios editores.
    """
    enricher = _CachedEnricher(enrich_fn) if enrich_fn is not None else None

    skip = _llm_skip_reason(config, use_llm)
    if skip is None:
        backends, missing = _backends(config, client)
        # Prazo único: o editor seguinte só usa o tempo que sobrou (o job tem limite).
        deadline = time.monotonic() + max(0, config.llm.deadline_seconds)
        for position, (provider, factory) in enumerate(backends):
            following = "tentando o próximo editor" if position + 1 < len(backends) else "usando a edição automática"
            try:
                return build_llm_edition(
                    bundle, config, now=now, enrich_fn=enricher, backend=factory(), deadline=deadline
                )
            except LLMUnavailable as exc:
                log.warning("Edição por IA (%s) indisponível (%s); %s", provider, exc, following)
            except Exception:
                log.exception("Erro inesperado na edição por IA (%s); %s", provider, following)
        if not backends:
            log.warning("Edição por IA não utilizada: %s; usando a edição automática", "; ".join(missing))
    else:
        reason, level = skip
        log.log(level, "Edição por IA não utilizada: %s; usando a edição automática", reason)

    page_info = _heuristic_page_info(bundle, config, now=now, enricher=enricher) if enricher else None
    return build_heuristic_edition(bundle, config, now=now, page_info=page_info)
