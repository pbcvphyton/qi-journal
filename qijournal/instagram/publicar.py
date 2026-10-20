"""Publica no Instagram o que venceu na fila (instagram/fila.json)."""

from __future__ import annotations

import logging
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from .fila import FAILED, MAX_ATTEMPTS, PUBLISHED, Fila, Post
from .graph import GraphError, InstagramClient

log = logging.getLogger(__name__)

UrlCheck = Callable[[str], bool]


class TokenInvalido(Exception):
    """O token do Instagram expirou ou foi revogado: nada sai até trocá-lo."""


def url_ok(url: str, timeout: float = 20) -> bool:
    """A imagem já está no ar (HTTP 200 e tipo image/*)? A API do Instagram baixa dali."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "TriangulaBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image/")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError):
        return False


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\W+", " ", text).strip()


def _first_line(caption: str) -> str:
    return _norm((caption or "").strip().split("\n", 1)[0])[:80]


@dataclass
class Resultado:
    publicados: list[Post] = field(default_factory=list)
    falhas: list[Post] = field(default_factory=list)
    aguardando: list[Post] = field(default_factory=list)  # imagens ainda fora do ar
    limite: bool = False  # limite de publicações em 24 h atingido


def _already_on_instagram(post: Post, recent: list[dict], since: datetime) -> dict | None:
    """Publicação com a mesma primeira linha de legenda feita desde ``since``.

    Rede de segurança contra publicar em dobro quando uma execução publicou mas
    morreu antes de gravar a fila. Stories não têm legenda: não se aplica.
    """
    if post.kind == "story" or not post.caption:
        return None
    key = _first_line(post.caption)
    if not key:
        return None
    for media in recent:
        try:
            ts = datetime.strptime(str(media.get("timestamp")), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if ts >= since and _first_line(str(media.get("caption") or "")) == key:
            return media
    return None


def publish_due(
    fila: Fila,
    client: InstagramClient,
    *,
    media_url: str,
    now: datetime,
    check_url: UrlCheck = url_ok,
    save: Callable[[], None] | None = None,
) -> Resultado:
    """Publica as publicações vencidas, na ordem do horário-alvo.

    Grava a fila (``save``) depois de cada publicação, para que uma falha no meio
    não faça a próxima execução publicar de novo. Levanta :class:`TokenInvalido`
    se o token não serve mais.
    """
    resultado = Resultado()
    due = fila.due(now)
    if not due:
        return resultado
    try:
        recent = client.recent_media(limit=25)
    except GraphError as exc:
        if exc.token_invalid:
            raise TokenInvalido(str(exc)) from exc
        log.warning("Instagram: não deu para listar as publicações recentes (%s)", exc)
        recent = []

    for post in due:
        urls = [media_url + path for path in post.images]
        missing = [u for u in urls if not check_url(u)]
        if missing:
            log.warning("Instagram: %s esperando %d imagem(ns) fora do ar (%s)", post.id, len(missing), missing[0])
            resultado.aguardando.append(post)
            continue
        found = _already_on_instagram(post, recent, post.due_at() - timedelta(hours=1))
        if found:
            log.warning("Instagram: %s já estava no perfil (%s); só registrando", post.id, found.get("id"))
            post.status, post.media_id = PUBLISHED, str(found.get("id"))
            post.permalink = found.get("permalink")
            post.published_at = str(found.get("timestamp"))
            resultado.publicados.append(post)
            if save:
                save()
            continue
        post.attempts += 1
        try:
            if post.kind == "carousel":
                media_id = client.post_carousel(urls, post.caption, post.alt_texts)
            elif post.kind == "image":
                media_id = client.post_image(urls[0], post.caption, (post.alt_texts or [None])[0])
            else:
                media_id = client.post_story(urls[0])
        except GraphError as exc:
            post.last_error = str(exc)[:500]
            if exc.token_invalid:
                post.attempts -= 1  # não conta: o problema é o token, não a publicação
                if save:
                    save()
                raise TokenInvalido(str(exc)) from exc
            if exc.publish_limit:
                post.attempts -= 1
                log.warning("Instagram: limite de publicações em 24 h atingido; %s fica para depois", post.id)
                resultado.limite = True
                if save:
                    save()
                break
            post.status = FAILED
            if not exc.transient:
                post.attempts = MAX_ATTEMPTS  # erro permanente: não adianta tentar de novo
            log.error("Instagram: %s não saiu (tentativa %d): %s", post.id, post.attempts, exc)
            resultado.falhas.append(post)
            if save:
                save()
            continue
        post.status = PUBLISHED
        post.media_id = media_id
        post.last_error = None
        post.published_at = now.isoformat(timespec="seconds")
        post.permalink = client.permalink(media_id)
        log.info("Instagram: %s publicado (%s)", post.id, post.permalink or media_id)
        resultado.publicados.append(post)
        if save:
            save()
    return resultado
