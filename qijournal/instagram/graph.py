"""Cliente mínimo (stdlib) da API de publicação de conteúdo do Instagram.

Serve tanto à "Instagram API with Instagram Login" (``graph.instagram.com``)
quanto à "with Facebook Login" (``graph.facebook.com``): o fluxo de publicação
é o mesmo (container → espera ficar pronto → ``media_publish``). O token vai no
cabeçalho ``Authorization: Bearer`` (nunca na URL) e não aparece nos logs nem
nas mensagens de erro (o log do Actions é público em repositório público).

Todo acesso à rede passa por um ``transport`` injetável, para os testes rodarem
sem internet.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

DEFAULT_HOST = "graph.instagram.com"
DEFAULT_VERSION = "v23.0"
TIMEOUT_SECONDS = 60
# Códigos de erro da Graph API que valem nova tentativa (além de HTTP 5xx e
# ``is_transient``): 1/2 erro temporário, 4/17/32/613 limite de chamadas,
# 9007 mídia ainda não pronta para publicar.
TRANSIENT_CODES = {1, 2, 4, 17, 32, 613, 9007}
TOKEN_CODES = {190}  # token expirado, revogado ou inválido
PUBLISH_LIMIT_SUBCODES = {2207042}  # limite de publicações em 24 h atingido
# A Meta não conseguiu baixar a mídia (2207052) ou o download expirou (2207003):
# costuma ser passageiro (cache/CDN logo depois do envio das imagens).
TRANSIENT_SUBCODES = {2207003, 2207052}
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException)

# (método, url, parâmetros, token) → (status HTTP, corpo JSON decodificado)
Transport = Callable[[str, str, dict[str, str], str], tuple[int, Any]]


class GraphError(Exception):
    """Erro devolvido pela Graph API (ou falha de rede ao falar com ela)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
        transient: bool = False,
        dead_container: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode
        self.transient = transient
        self.dead_container = dead_container  # o container acabou em ERROR/EXPIRED: criar outro

    @property
    def token_invalid(self) -> bool:
        return self.code in TOKEN_CODES

    @property
    def publish_limit(self) -> bool:
        return self.subcode in PUBLISH_LIMIT_SUBCODES


def urllib_transport(method: str, url: str, params: dict[str, str], token: str) -> tuple[int, Any]:
    """Transporte real: GET com parâmetros na query, POST com corpo form-urlencoded."""
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(f"{url}?{data.decode()}" if params else url, method="GET")
    else:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", "TriangulaBot/1.0 (+https://github.com/pbcvphyton/qi-journal)")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, _decode(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _decode(e.read())


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": {"message": raw[:300].decode("utf-8", "replace")}}


@dataclass
class TokenInfo:
    access_token: str
    expires_in: int | None  # segundos até expirar (None: a API não informou)


class InstagramClient:
    """Publica no perfil ``user_id`` com o token ``token``."""

    def __init__(
        self,
        user_id: str,
        token: str,
        *,
        host: str = DEFAULT_HOST,
        version: str = DEFAULT_VERSION,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ):
        if not user_id or not token:
            raise ValueError("user_id e token são obrigatórios")
        self.user_id = user_id
        self._token = token
        self.host = host
        self.version = version.strip("/")
        self._transport = transport or urllib_transport
        self._sleep = sleep
        self._retries = retries

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _url(self, path: str, *, versioned: bool = True) -> str:
        prefix = f"https://{self.host}"
        if versioned and self.version:
            prefix += f"/{self.version}"
        return f"{prefix}/{path.lstrip('/')}"

    def _call(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        versioned: bool = True,
        retries: int | None = None,
        token_in_query: bool = False,
    ) -> Any:
        """Chama a API com retentativas exponenciais para erros passageiros."""
        query = {k: str(v) for k, v in (params or {}).items() if v is not None}
        token = self._token
        if token_in_query:  # refresh_access_token só aceita o token como parâmetro
            query["access_token"], token = self._token, ""
        url = self._url(path, versioned=versioned)
        retries = self._retries if retries is None else retries
        last: GraphError | None = None
        for attempt in range(retries + 1):
            if attempt:
                self._sleep(min(15 * 4 ** (attempt - 1), 180))  # 15 s, 60 s, 180 s
            try:
                status, body = self._transport(method, url, query, token)
            except NETWORK_ERRORS as e:
                last = GraphError(f"falha de rede em {method} {path}: {type(e).__name__}", transient=True)
            else:
                error = body.get("error") if isinstance(body, dict) else None
                if status < 400 and not error:
                    return body
                last = self._error(method, path, status, error or {})
            if not last.transient:
                break
            log.warning("Instagram: %s (tentativa %d/%d)", last, attempt + 1, retries + 1)
        assert last is not None
        raise last

    def _error(self, method: str, path: str, status: int, error: dict[str, Any]) -> GraphError:
        code = error.get("code")
        subcode = error.get("error_subcode")
        message = str(error.get("error_user_msg") or error.get("message") or f"HTTP {status}")
        # A mensagem da API nunca traz o token, mas por garantia ele é removido.
        message = message.replace(self._token, "***")
        transient = (
            bool(error.get("is_transient")) or status >= 500 or code in TRANSIENT_CODES or subcode in TRANSIENT_SUBCODES
        )
        if subcode in PUBLISH_LIMIT_SUBCODES:
            transient = False
        detail = f"código {code}" + (f"/{subcode}" if subcode else "") if code is not None else f"HTTP {status}"
        return GraphError(
            f"{method} {path}: {message} ({detail})", status=status, code=code, subcode=subcode, transient=transient
        )

    # ── Containers e publicação ───────────────────────────────────────────

    def create_image(
        self,
        image_url: str,
        *,
        caption: str | None = None,
        alt_text: str | None = None,
        carousel_item: bool = False,
        story: bool = False,
    ) -> str:
        """Cria o container de uma imagem (JPEG em URL pública) e devolve o id."""
        params: dict[str, Any] = {"image_url": image_url}
        if story:
            params["media_type"] = "STORIES"
        if carousel_item:
            params["is_carousel_item"] = "true"
        if caption and not carousel_item and not story:
            params["caption"] = caption
        if alt_text and not story:
            params["alt_text"] = alt_text[:1000]
        return str(self._call("POST", f"{self.user_id}/media", params)["id"])

    def create_carousel(self, children: Sequence[str], caption: str) -> str:
        if not 2 <= len(children) <= 10:
            raise ValueError(f"carrossel precisa de 2 a 10 itens (recebeu {len(children)})")
        params = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
        return str(self._call("POST", f"{self.user_id}/media", params)["id"])

    def container_status(self, container_id: str) -> str:
        body = self._call("GET", container_id, {"fields": "status_code,status"})
        return str(body.get("status_code") or "")

    def wait_ready(self, container_id: str, *, timeout: float = 300, interval: float = 5) -> None:
        """Espera o container ficar FINISHED; ERROR/EXPIRED ou prazo esgotado levantam GraphError."""
        waited = 0.0
        while True:
            status = self.container_status(container_id)
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                # Quase sempre falha ao baixar a imagem: vale tentar de novo com outro container.
                raise GraphError(f"container {container_id} terminou em {status}", transient=True, dead_container=True)
            if waited >= timeout:
                raise GraphError(f"container {container_id} não ficou pronto em {int(timeout)} s ({status})", transient=True)
            self._sleep(interval)
            waited += interval

    def publish(self, container_id: str, *, feed: bool = True) -> str:
        """Publica o container. Sem retentativa às cegas: se a chamada falhar de um jeito
        ambíguo (rede, 5xx), confere o status do container antes de desistir — se ele
        já está PUBLISHED, o post saiu (o id vem da lista de mídias do feed; story não
        aparece lá e fica registrado pelo id do container)."""
        try:
            return str(self._call("POST", f"{self.user_id}/media_publish", {"creation_id": container_id}, retries=0)["id"])
        except GraphError as exc:
            if not exc.transient:
                raise
            # A 1ª chamada pode ter dado certo no servidor: espera o container virar
            # PUBLISHED por até 2 min. Se continuar FINISHED, NÃO publica de novo
            # agora: a próxima execução confere o status com folga e só então publica.
            for _ in range(8):
                self._sleep(15)
                status = self.container_status(container_id)
                if status == "PUBLISHED":
                    log.warning("Instagram: media_publish falhou (%s), mas o container %s já está publicado", exc, container_id)
                    return (self.find_published() if feed else None) or container_id
                if status in ("ERROR", "EXPIRED"):
                    raise GraphError(f"container {container_id} terminou em {status}", transient=True, dead_container=True) from exc
            raise

    def find_published(self) -> str | None:
        """Id da publicação mais recente do feed (a que acabou de sair)."""
        try:
            recent = self.recent_media(limit=1)
        except GraphError:
            return None
        return str(recent[0]["id"]) if recent else None

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._call("GET", media_id, {"fields": "permalink"}).get("permalink")
        except GraphError as exc:
            log.warning("Instagram: link da publicação %s indisponível: %s", media_id, exc)
            return None

    # ── Fluxos completos ──────────────────────────────────────────────────

    def post_carousel(self, image_urls: Sequence[str], caption: str, alt_texts: Sequence[str | None] = ()) -> str:
        alts = list(alt_texts) + [None] * (len(image_urls) - len(alt_texts))
        children = [self.create_image(url, carousel_item=True, alt_text=alt) for url, alt in zip(image_urls, alts)]
        for child in children:
            self.wait_ready(child)
        container = self.create_carousel(children, caption)
        self.wait_ready(container)
        return self.publish(container)

    def post_image(self, image_url: str, caption: str, alt_text: str | None = None) -> str:
        container = self.create_image(image_url, caption=caption, alt_text=alt_text)
        self.wait_ready(container)
        return self.publish(container)

    def post_story(self, image_url: str) -> str:
        container = self.create_image(image_url, story=True)
        self.wait_ready(container)
        return self.publish(container, feed=False)

    # ── Consultas e token ─────────────────────────────────────────────────

    def account(self) -> dict[str, Any]:
        """Teste de saúde do token: devolve id e username do perfil."""
        return self._call("GET", self.user_id, {"fields": "id,username"}, retries=1)

    def publishing_quota(self) -> tuple[int | None, int | None]:
        """(publicações nas últimas 24 h, limite), ou (None, None) se a API não informar."""
        body = self._call("GET", f"{self.user_id}/content_publishing_limit", {"fields": "quota_usage,config"})
        data = (body.get("data") or [{}])[0] if isinstance(body, dict) else {}
        config = data.get("config") or {}
        return data.get("quota_usage"), config.get("quota_total")

    def recent_media(self, limit: int = 25) -> list[dict[str, Any]]:
        """Publicações recentes do feed (carrossel, imagem, reels), da mais nova para a mais antiga."""
        body = self._call("GET", f"{self.user_id}/media", {"fields": "id,caption,timestamp,permalink,media_type", "limit": limit})
        return list(body.get("data") or [])

    def refresh_token(self) -> TokenInfo:
        """Renova o token de longa duração (só na API with Instagram Login).

        O token precisa ter ao menos 24 h e ainda estar válido; o novo vale 60 dias.
        """
        body = self._call(
            "GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, versioned=False, token_in_query=True
        )
        token = str(body.get("access_token") or "")
        if not token:
            raise GraphError("renovação do token sem access_token na resposta")
        self._token = token
        expires = body.get("expires_in")
        return TokenInfo(access_token=token, expires_in=int(expires) if expires is not None else None)
