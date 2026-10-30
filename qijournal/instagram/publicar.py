"""Publica no Instagram o que venceu na fila (``fila.json`` da branch ``instagram``).

Proteções contra publicar em dobro (o log da execução é público e ninguém
acompanha a conta):

- o id do container é gravado (e enviado à branch) ANTES do ``media_publish``;
  a execução seguinte confere o status dele em vez de criar outro;
- ``media_publish`` nunca é repetido às cegas (ver ``InstagramClient.publish``);
- antes de publicar um post de feed, a primeira linha da legenda é procurada
  nas publicações recentes do perfil;
- no máximo um post de feed a cada :data:`~.fila.FEED_GAP`.
"""

from __future__ import annotations

import http.client
import logging
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from .fila import FAILED, FEED_GAP, MAX_ATTEMPTS, PUBLISHED, Fila, Post
from .graph import GraphError, InstagramClient

log = logging.getLogger(__name__)

UrlCheck = Callable[[str], bool]
# Recebe uma descrição curta do que mudou; devolve False se não conseguiu guardar o
# estado na branch (aí o post não segue para o media_publish).
Save = Callable[[str], "bool | None"]


class TokenInvalido(Exception):
    """O token do Instagram expirou ou foi revogado: nada sai até trocá-lo."""


def url_ok(url: str, timeout: float = 20) -> bool:
    """A imagem já está no ar (HTTP 200 e tipo image/*)? A API do Instagram baixa dali."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "TriangulaBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200 and resp.headers.get("Content-Type", "").startswith("image/")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError, http.client.HTTPException):
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
    aguardando: list[Post] = field(default_factory=list)  # imagens ainda fora do ar ou intervalo do feed
    limite: bool = False  # cota de publicações em 24 h atingida


def _already_on_instagram(post: Post, recent: list[dict], since: datetime) -> dict | None:
    """Publicação com a mesma primeira linha de legenda feita desde ``since``.

    Rede de segurança contra publicar em dobro quando uma execução publicou mas
    morreu antes de gravar a fila. Stories não têm legenda: não se aplica.
    """
    if not post.feed or not post.caption:
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


def _create_container(client: InstagramClient, post: Post, urls: list[str]) -> str:
    if post.kind == "carousel":
        alts = list(post.alt_texts) + [None] * (len(urls) - len(post.alt_texts))
        children = [client.create_image(u, carousel_item=True, alt_text=a) for u, a in zip(urls, alts)]
        for child in children:
            client.wait_ready(child)
        return client.create_carousel(children, post.caption)
    if post.kind == "image":
        return client.create_image(urls[0], caption=post.caption, alt_text=(post.alt_texts or [None])[0])
    return client.create_image(urls[0], story=True)


def _publish_one(client: InstagramClient, post: Post, urls: list[str], save: Save) -> str:
    """Cria (ou reaproveita) o container, grava o id e publica. Devolve o id da mídia."""
    if post.container_id:
        status = client.container_status(post.container_id)
        if status == "PUBLISHED":
            log.warning("Instagram: %s já tinha sido publicado (container %s)", post.id, post.container_id)
            return (client.find_published() if post.feed else None) or post.container_id
        if status != "FINISHED":  # EXPIRED, ERROR ou desconhecido: cria outro
            post.container_id = None
    if not post.container_id:
        post.container_id = _create_container(client, post, urls)
        if save(f"{post.id}: container criado") is False:
            # Sem o container gravado na branch, uma execução que morresse depois do
            # media_publish publicaria de novo: melhor deixar para a próxima.
            raise GraphError(f"{post.id}: não deu para gravar o container na branch; fica para depois", transient=True)
    client.wait_ready(post.container_id)
    return client.publish(post.container_id, feed=post.feed)


def _quota_reached(client: InstagramClient) -> bool:
    try:
        usage, total = client.publishing_quota()
    except GraphError as exc:
        if exc.token_invalid:
            raise TokenInvalido(str(exc)) from exc
        log.warning("Instagram: cota de publicação indisponível (%s)", exc)
        return False
    if usage is not None and total:
        log.info("Instagram: %s de %s publicações nas últimas 24 h", usage, total)
        return usage >= total - 1
    return False


def publish_due(
    fila: Fila,
    client: InstagramClient,
    *,
    media_url: str,
    now: datetime,
    check_url: UrlCheck = url_ok,
    save: Save | None = None,
) -> Resultado:
    """Publica as publicações vencidas, na ordem do horário-alvo.

    ``save`` é chamado a cada mudança de estado (container criado, publicado,
    falha), para que nada se perca se a execução morrer no meio. Levanta
    :class:`TokenInvalido` se o token não serve mais.
    """
    save = save or (lambda what: None)
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
    if _quota_reached(client):
        log.warning("Instagram: cota de publicações em 24 h no limite; a fila fica para depois")
        resultado.limite = True
        return resultado

    for post in due:
        if post.feed:
            last = fila.last_feed_publication()
            if last is not None and now - last < FEED_GAP:
                log.info("Instagram: %s espera o intervalo de %s desde o último post de feed", post.id, FEED_GAP)
                resultado.aguardando.append(post)
                continue
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
            post.published_at = now.isoformat(timespec="seconds")
            fila.registrar_publicacao(post)
            resultado.publicados.append(post)
            save(f"{post.id}: já publicado")
            continue
        post.attempts += 1
        try:
            media_id = _publish_one(client, post, urls, save)
        except GraphError as exc:
            post.last_error = str(exc)[:500]
            if exc.token_invalid:
                post.attempts -= 1  # não conta: o problema é o token, não a publicação
                save(f"{post.id}: token inválido")
                raise TokenInvalido(str(exc)) from exc
            if exc.publish_limit:
                post.attempts -= 1
                log.warning("Instagram: cota de publicações em 24 h atingida; %s fica para depois", post.id)
                resultado.limite = True
                save(f"{post.id}: cota atingida")
                break
            post.status = FAILED
            if exc.dead_container:
                post.container_id = None  # o próximo tenta com um container novo
            if not exc.transient:
                post.attempts = MAX_ATTEMPTS  # erro permanente: não adianta tentar de novo
                post.container_id = None
            log.error("Instagram: %s não saiu (tentativa %d): %s", post.id, post.attempts, exc)
            resultado.falhas.append(post)
            save(f"{post.id}: falhou")
            continue
        post.status = PUBLISHED
        post.media_id = media_id
        post.container_id = None
        post.last_error = None
        post.published_at = now.isoformat(timespec="seconds")
        post.permalink = client.permalink(media_id) if post.feed else None
        fila.registrar_publicacao(post)  # a série só avança com o post no ar
        log.info("Instagram: %s publicado (%s)", post.id, post.permalink or media_id)
        resultado.publicados.append(post)
        save(f"{post.id}: publicado")
    return resultado
