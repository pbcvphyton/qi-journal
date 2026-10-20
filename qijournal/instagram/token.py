"""Renovação automática do token da "Instagram API with Instagram Login".

O token de longa duração vale 60 dias e pode ser renovado depois de 24 h de
idade. O publicador renova a cada :data:`REFRESH_EVERY` e grava na fila só as
datas (nunca o token). O token novo vai para um arquivo temporário do runner,
que o workflow guarda de volta no segredo ``IG_ACCESS_TOKEN``.

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


def maybe_refresh(client: InstagramClient, fila: Fila, now: datetime, *, current_token: str) -> TokenStatus:
    """Renova o token se a última renovação tiver mais de uma semana (só Instagram Login)."""
    status = TokenStatus(expires_at=_parse(fila.token.get("expires_at")))
    if client.host != INSTAGRAM_LOGIN_HOST:
        return status
    last = _parse(fila.token.get("refreshed_at"))
    if last is not None and now - last < REFRESH_EVERY:
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
    if info.expires_in:
        status.expires_at = now + timedelta(seconds=info.expires_in)
    if info.access_token != current_token:
        status.new_token = info.access_token
    fila.token = {
        "refreshed_at": now.isoformat(timespec="seconds"),
        "expires_at": status.expires_at.isoformat(timespec="seconds") if status.expires_at else None,
    }
    log.info("Instagram: token renovado (vale até %s)", fila.token["expires_at"] or "data não informada")
    return status


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
