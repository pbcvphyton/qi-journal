"""Renovação automática do token da "Instagram API with Instagram Login".

O token de longa duração vale 60 dias e pode ser renovado depois de 24 h de
idade. O publicador renova a cada :data:`REFRESH_EVERY` e grava na fila só as
datas (nunca o token). O token novo vai para um arquivo temporário do runner,
que o workflow guarda de volta no segredo ``IG_ACCESS_TOKEN``; só depois disso
(:func:`confirm_refresh`) as datas dele passam a valer na fila. Assim o aviso
de vencimento acompanha o token que está de fato no segredo: se a gravação
falhar sempre, o aviso chega antes de ele vencer.

Tokens da API "with Facebook Login" (token de usuário do sistema do
Business Manager) não expiram e não passam por aqui.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .fila import Fila
from .graph import GraphError, InstagramClient

log = logging.getLogger(__name__)

REFRESH_EVERY = timedelta(days=7)
WARN_BEFORE = timedelta(days=14)
FRESH_TOKEN_LIFETIME = timedelta(days=60)  # token recém-gerado no painel da Meta
INSTAGRAM_LOGIN_HOST = "graph.instagram.com"


@dataclass
class TokenStatus:
    refreshed: bool = False
    new_token: str | None = None  # só quando a renovação devolveu um token diferente
    expires_at: datetime | None = None
    warning: str | None = None


def _parse(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


def maybe_refresh(client: InstagramClient, fila: Fila, now: datetime, *, current_token: str) -> TokenStatus:
    """Renova o token se a última renovação tiver mais de uma semana (só Instagram Login).

    ``fila.token`` guarda as datas do token que está no segredo (``refreshed_at``,
    ``expires_at``) e, em ``pending``, as do token novo ainda não gravado.
    """
    status = TokenStatus(expires_at=_parse(fila.token.get("expires_at")))
    if client.host != INSTAGRAM_LOGIN_HOST:
        return status
    if status.expires_at is None:
        # Primeira execução: o token acabou de ser gerado no painel (vale até 60 dias).
        status.expires_at = now + FRESH_TOKEN_LIFETIME
        fila.token["expires_at"] = _iso(status.expires_at)
        fila.token["estimated"] = True
    pending = fila.token.get("pending") or {}
    attempts = [d for d in (_parse(fila.token.get("refreshed_at")), _parse(pending.get("refreshed_at"))) if d]
    if attempts and now - max(attempts) < REFRESH_EVERY:
        status.warning = _expiry_warning(status.expires_at, now)
        return status
    try:
        info = client.refresh_token()
    except GraphError as exc:
        if exc.token_invalid:
            raise
        log.warning("Instagram: não deu para renovar o token agora (%s); tento na próxima execução", exc)
        status.warning = _expiry_warning(status.expires_at, now)
        return status
    status.refreshed = True
    new_expires = now + timedelta(seconds=info.expires_in) if info.expires_in else now + FRESH_TOKEN_LIFETIME
    record = {"refreshed_at": _iso(now), "expires_at": _iso(new_expires)}
    if info.access_token != current_token:
        # Vale quando o workflow gravar o segredo (confirm_refresh).
        status.new_token = info.access_token
        fila.token["pending"] = record
        log.info("Instagram: token renovado (o novo vale até %s, depois de guardado no segredo)", record["expires_at"])
    else:
        # Mesma string, validade estendida: já vale para o token do segredo.
        fila.token.update(record)
        fila.token.pop("pending", None)
        fila.token.pop("estimated", None)
        status.expires_at = new_expires
        log.info("Instagram: token renovado (vale até %s)", record["expires_at"])
    status.warning = _expiry_warning(status.expires_at, now)
    return status


def confirm_refresh(fila: Fila) -> bool:
    """O token novo foi gravado no segredo: as datas dele passam a valer."""
    pending = fila.token.pop("pending", None)
    if not pending:
        return False
    fila.token.update(pending)
    fila.token.pop("estimated", None)
    return True


def _expiry_warning(expires_at: datetime | None, now: datetime) -> str | None:
    if expires_at is not None and expires_at - now < WARN_BEFORE:
        days = max(0, (expires_at - now).days)
        return f"o token do Instagram expira em {days} dia(s) ({expires_at:%d/%m/%Y})"
    return None


def write_token_file(token: str, path: Path) -> None:
    """Grava o token renovado para o workflow guardar no segredo; mascara no log do Actions."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{token}", flush=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
