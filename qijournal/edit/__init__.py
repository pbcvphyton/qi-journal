"""Edição do jornal: transforma o :class:`~qijournal.models.Bundle` coletado em uma
:class:`~qijournal.models.Edition`.

:func:`make_edition` tenta a edição por IA (Claude) e, se ela estiver desligada
ou falhar, cai para a edição automática (heurística), que sempre funciona.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

from qijournal.config import Config
from qijournal.edit.heuristic import build_heuristic_edition, choose_clusters
from qijournal.edit.llm import EnrichFn, LLMUnavailable, build_llm_edition
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


def _llm_skip_reason(config: Config, use_llm: bool, client: Any) -> tuple[str, int] | None:
    """Motivo (e nível de log) para nem tentar a IA, ou ``None`` se ela deve ser tentada.

    Desligar a IA de propósito é informativo; faltar a chave com a IA ligada
    merece aviso (provavelmente o segredo não foi configurado).
    """
    if not use_llm:
        return "desligada nesta execução (--no-llm)", logging.INFO
    if not config.llm.enabled:
        return "desligada na configuração", logging.INFO
    if client is None and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "ANTHROPIC_API_KEY não definida", logging.WARNING
    return None


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

    Tenta :func:`build_llm_edition` quando ``use_llm``, a IA está habilitada na
    configuração e há ``client`` ou ``ANTHROPIC_API_KEY``. Em
    :class:`LLMUnavailable` (ou qualquer erro inesperado, registrado com
    traceback) cai para :func:`build_heuristic_edition`, à qual passa o
    enriquecimento das páginas quando ``enrich_fn`` é fornecido. As
    estatísticas da edição (fontes, artigos, tokens) vêm preenchidas pelos
    próprios editores.
    """
    enricher = _CachedEnricher(enrich_fn) if enrich_fn is not None else None

    skip = _llm_skip_reason(config, use_llm, client)
    if skip is None:
        try:
            return build_llm_edition(bundle, config, now=now, client=client, enrich_fn=enricher)
        except LLMUnavailable as exc:
            log.warning("Edição por IA indisponível (%s); usando a edição automática", exc)
        except Exception:
            log.exception("Erro inesperado na edição por IA; usando a edição automática")
    else:
        reason, level = skip
        log.log(level, "Edição por IA não utilizada: %s; usando a edição automática", reason)

    page_info = _heuristic_page_info(bundle, config, now=now, enricher=enricher) if enricher else None
    return build_heuristic_edition(bundle, config, now=now, page_info=page_info)
