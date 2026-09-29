"""HTTP mínimo (stdlib) com retentativas, limite de tamanho e decodificação de charset.

Todo código que acessa a rede recebe um ``fetch`` injetável com a mesma
assinatura de :func:`fetch`, para que os testes rodem sem internet.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, TypeVar

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 QIJournalBot/2.0 (+https://github.com/pbcvphyton/qi-journal)"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, application/json;q=0.8, */*;q=0.5",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FetchError(Exception):
    """Falha de rede ou HTTP >= 400 (depois das retentativas)."""

    def __init__(self, url: str, message: str, status: int | None = None):
        super().__init__(f"{message} ({url})")
        self.url = url
        self.status = status


@dataclass
class Response:
    url: str  # URL final (após redirecionamentos)
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)  # chaves em minúsculas
    elapsed_ms: int = 0

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def text(self) -> str:
        """Decodifica respeitando charset do cabeçalho, depois <meta charset>, depois utf-8/latin-1."""
        charset = None
        m = re.search(r"charset=([\w.-]+)", self.content_type, re.I)
        if m:
            charset = m.group(1)
        if not charset:
            head = self.content[:4096].decode("ascii", "ignore")
            m = re.search(r"""<meta[^>]+charset=["']?([\w.-]+)""", head, re.I) or re.search(
                r"""<\?xml[^>]+encoding=["']([\w.-]+)""", head, re.I
            )
            if m:
                charset = m.group(1)
        for enc in [charset, "utf-8", "latin-1"]:
            if not enc:
                continue
            try:
                return self.content.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text())


Fetcher = Callable[..., Response]


def _decompress(data: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        return gzip.decompress(data)
    if "deflate" in encoding:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def fetch(
    url: str,
    *,
    timeout: float = 20.0,
    retries: int = 2,
    headers: dict[str, str] | None = None,
    max_bytes: int = 8_000_000,
) -> Response:
    """GET com retentativas exponenciais (1s, 2s, ...) para erros transitórios.

    Levanta :class:`FetchError` para HTTP >= 400 ou falha de rede persistente.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError(url, "esquema de URL não suportado")
    req_headers = {**DEFAULT_HEADERS, **(headers or {})}
    last_error: FetchError | None = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(2 ** (attempt - 1), 8))
        start = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise FetchError(url, f"resposta maior que {max_bytes} bytes")
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                content = _decompress(raw, hdrs.get("content-encoding", ""))
                return Response(
                    url=resp.geturl(),
                    status=resp.status,
                    content=content,
                    headers=hdrs,
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                )
        except urllib.error.HTTPError as e:
            last_error = FetchError(url, f"HTTP {e.code}", status=e.code)
            if e.code not in RETRY_STATUS:
                break
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
            reason = getattr(e, "reason", e)
            last_error = FetchError(url, f"{type(e).__name__}: {reason}")
        log.debug("tentativa %d falhou para %s: %s", attempt + 1, url, last_error)
    assert last_error is not None
    raise last_error


T = TypeVar("T")
R = TypeVar("R")


def parallel_map(fn: Callable[[T], R], items: Iterable[T], max_workers: int = 12) -> list[R]:
    """``map`` em threads preservando a ordem. ``fn`` deve tratar os próprios erros."""
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items)))) as pool:
        return list(pool.map(fn, items))
