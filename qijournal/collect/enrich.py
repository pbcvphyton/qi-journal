"""Enriquecimento de artigos a partir da página original da matéria.

Para as matérias escolhidas pelo editor, baixa a página e extrai imagem
(``og:image``/``twitter:image``/JSON-LD), descrição e até 3000 caracteres do
corpo (``articleBody`` do JSON-LD ou parágrafos dentro de ``<article>``).
Sem dependências de parsing além da stdlib (:class:`html.parser.HTMLParser`).

Tudo aqui é tolerante a falhas: qualquer erro de rede ou de parsing resulta
em :class:`PageInfo` vazio, nunca em exceção.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urljoin

from .. import net, text
from ..models import Article
from ..net import Fetcher
from .feeds import is_usable_image_url, strip_boilerplate

log = logging.getLogger(__name__)

TEXT_MAX_CHARS = 3000
DESCRIPTION_MAX_CHARS = 1000
MIN_PARAGRAPH_CHARS = 60
MIN_BODY_CHARS = 120
PAGE_MAX_BYTES = 5_000_000
HTML_MAX_CHARS = 3_000_000
PAGE_HEADERS = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"}

_ARTICLE_TYPES = {
    "article", "newsarticle", "reportagenewsarticle", "analysisnewsarticle", "opinionnewsarticle",
    "backgroundnewsarticle", "reviewnewsarticle", "blogposting", "liveblogposting", "techarticle", "report",
}

# Frases típicas de paywall/chamada de assinatura, cadastro e "presente" (Folha):
# lista única em :data:`qijournal.text.PAYWALL_RE`, também usada na limpeza dos feeds.
_PAYWALL = text.PAYWALL_RE

# Chamadas para outras matérias ("Leia também: <títulos colados>"): corta do
# marcador até o fim do parágrafo.
_RELATED = re.compile(
    r"\s*(?:Leia (?:mais|tamb[ée]m)|Veja tamb[ée]m|Saiba mais|Not[íi]cias relacionadas|Read more)\s*:.*$",
    re.I | re.S,
)
# Parágrafo que só anuncia outras matérias, e os títulos curtos que o seguem.
_RELATED_START = re.compile(r"^(?:leia|veja) (?:tambem|mais)\b|^saiba mais\b|^read more\b|^related\b")
_RELATED_TITLE_MAX = 160
_TERMINAL = re.compile(r"[.!?…][\"'”’)\]]*$")

# Blocos que, dentro de um <p>, separam palavras (sem espaço, "título1título2" grudaria).
_SPACED_TAGS = {"li", "div", "ul", "ol", "h2", "h3", "h4", "section"}

# Tags cujo conteúdo não é corpo de matéria.
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "figure", "figcaption", "aside", "nav",
              "footer", "form", "button", "iframe"}


@dataclass
class PageInfo:
    """Dados extraídos da página de uma matéria (todos opcionais)."""

    image: str | None = None
    description: str | None = None
    text: str | None = None  # até 3000 chars de corpo da matéria

    def is_empty(self) -> bool:
        return not (self.image or self.description or self.text)


class _PageParser(HTMLParser):
    """Coleta metatags, blocos JSON-LD e parágrafos de ``<article>``/``<main>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.article_paragraphs: list[str] = []
        self.main_paragraphs: list[str] = []
        self._jsonld_buf: list[str] | None = None
        self._skip_depth = 0
        self._article_depth = 0
        self._main_depth = 0
        self._paragraph: list[str] | None = None
        self._paragraph_target: list[str] | None = None

    # -- eventos do HTMLParser --

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag in _SPACED_TAGS and self._paragraph is not None:
            self._paragraph.append(" ")  # itens de lista/blocos dentro do parágrafo não grudam palavras
        if tag == "meta":
            self._handle_meta(attributes)
        elif tag == "link" and attributes.get("rel", "").lower() == "image_src":
            self.meta.setdefault("image_src", attributes.get("href", "").strip())
        elif tag == "script" and "ld+json" in attributes.get("type", "").lower():
            self._jsonld_buf = []
        elif tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "article":
            self._flush_paragraph()
            self._article_depth += 1
        elif tag == "main":
            self._flush_paragraph()
            self._main_depth += 1
        elif tag == "p":
            self._start_paragraph()
        elif tag == "br" and self._paragraph is not None:
            self._paragraph.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SPACED_TAGS and self._paragraph is not None:
            self._paragraph.append(" ")
        if tag == "script" and self._jsonld_buf is not None:
            self.jsonld.append("".join(self._jsonld_buf))
            self._jsonld_buf = None
        elif tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "p":
            self._flush_paragraph()
        elif tag == "article":
            self._flush_paragraph()
            self._article_depth = max(0, self._article_depth - 1)
        elif tag == "main":
            self._flush_paragraph()
            self._main_depth = max(0, self._main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._jsonld_buf is not None:
            self._jsonld_buf.append(data)
        elif not self._skip_depth and self._paragraph is not None:
            self._paragraph.append(data)

    def close(self) -> None:
        super().close()
        self._flush_paragraph()

    # -- auxiliares --

    def _handle_meta(self, attributes: dict[str, str]) -> None:
        key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").lower()
        content = attributes.get("content", "").strip()
        if key and content:
            self.meta.setdefault(key, content)

    def _start_paragraph(self) -> None:
        self._flush_paragraph()
        if self._article_depth:
            self._paragraph_target = self.article_paragraphs
        elif self._main_depth:
            self._paragraph_target = self.main_paragraphs
        else:
            return
        self._paragraph = []

    def _flush_paragraph(self) -> None:
        if self._paragraph is not None and self._paragraph_target is not None:
            paragraph = " ".join("".join(self._paragraph).split())
            if paragraph:
                self._paragraph_target.append(paragraph)
        self._paragraph = None
        self._paragraph_target = None


# ── JSON-LD ──────────────────────────────────────────────────────────────────


def _load_jsonld(raw: str) -> Any:
    raw = raw.strip()
    for wrapper in ("<!--", "-->", "//<![CDATA[", "//]]>", "<![CDATA[", "]]>"):
        raw = raw.replace(wrapper, "")
    raw = raw.strip().rstrip(";")
    if not raw:
        return None
    try:
        return json.loads(raw, strict=False)  # strict=False: aceita quebras de linha dentro de strings
    except ValueError:
        log.debug("JSON-LD inválido ignorado (%d chars)", len(raw))
        return None


def _iter_nodes(value: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Percorre todos os objetos de um documento JSON-LD (listas, ``@graph``, aninhados)."""
    if depth > 8:
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_nodes(item, depth + 1)
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _iter_nodes(child, depth + 1)


def _node_types(node: dict[str, Any]) -> set[str]:
    types = node.get("@type")
    if isinstance(types, str):
        types = [types]
    return {str(t).lower() for t in types or [] if isinstance(t, str)}


def _ld_image(value: Any, index: dict[str, dict[str, Any]], depth: int = 0) -> str | None:
    """URL de imagem de um valor JSON-LD (string, lista, ``ImageObject`` ou referência ``@id``)."""
    if depth > 4 or value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            found = _ld_image(item, index, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, dict):
        url = value.get("url") or value.get("contentUrl")
        if isinstance(url, str) and url.strip():
            return url.strip()
        ref = value.get("@id")
        if isinstance(ref, str) and ref in index and index[ref] is not value:
            return _ld_image(index[ref], index, depth + 1)
    return None


@dataclass
class _LdArticle:
    body: str | None = None
    description: str | None = None
    image: str | None = None


def _jsonld_article(blocks: list[str]) -> _LdArticle:
    """Escolhe o nó de artigo mais completo entre os blocos JSON-LD da página."""
    nodes = [node for block in blocks for node in _iter_nodes(_load_jsonld(block))]
    index = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str)}
    articles = [node for node in nodes if _node_types(node) & _ARTICLE_TYPES]
    if not articles:
        return _LdArticle()
    best = max(articles, key=lambda node: len(str(node.get("articleBody") or "")))
    body = best.get("articleBody")
    description = best.get("description")
    image = _ld_image(best.get("image"), index) or _ld_image(best.get("thumbnailUrl"), index)
    return _LdArticle(
        body=body if isinstance(body, str) else None,
        description=description if isinstance(description, str) else None,
        image=image,
    )


# ── Regras de extração ───────────────────────────────────────────────────────


def _is_paywall(value: str) -> bool:
    return text.is_paywall(value)


def _drop_related(paragraphs: list[str]) -> list[str]:
    """Tira as chamadas "Leia também" (e os títulos curtos que as seguem) e corta
    o marcador quando ele aparece no meio de um parágrafo."""
    out: list[str] = []
    skipping = False
    for paragraph in paragraphs:
        paragraph = " ".join(paragraph.split())
        if skipping and len(paragraph) <= _RELATED_TITLE_MAX and not _TERMINAL.search(paragraph):
            continue
        skipping = bool(_RELATED_START.search(text.normalize(paragraph)))
        paragraph = _RELATED.sub("", paragraph).strip()
        if paragraph:
            out.append(paragraph)
    return out


def _clean_body(value: str | None) -> str | None:
    """Texto do corpo sem parágrafos de paywall nem "Leia também"; ``None`` se sobrar pouco."""
    if not value:
        return None
    paragraphs = _drop_related(text.html_to_text(value).split("\n"))
    paragraphs = [p for p in paragraphs if p and not _is_paywall(p)]
    body = strip_boilerplate("\n".join(paragraphs))
    if len(body) < MIN_BODY_CHARS:
        return None
    return text.truncate(body, TEXT_MAX_CHARS)


def _paragraph_body(paragraphs: list[str]) -> str | None:
    """Junta parágrafos longos (≥ 60 chars, sem paywall) até ~3000 caracteres."""
    selected: list[str] = []
    total = 0
    for paragraph in _drop_related(paragraphs):
        if len(paragraph) < MIN_PARAGRAPH_CHARS or _is_paywall(paragraph):
            continue
        selected.append(paragraph)
        total += len(paragraph) + 1
        if total >= TEXT_MAX_CHARS:
            break
    return _clean_body("\n".join(selected))


def _pick_image(candidates: list[str | None], base_url: str | None) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        url = html.unescape(candidate.strip())
        if base_url:
            url = urljoin(base_url, url)
        elif url.startswith("//"):
            url = "https:" + url
        if is_usable_image_url(url):
            return url
    return None


def _pick_description(candidates: list[str | None]) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        description = " ".join(strip_boilerplate(text.html_to_text(candidate)).split())
        if description and not _is_paywall(description):
            return text.truncate(description, DESCRIPTION_MAX_CHARS)
    return None


def parse_page(page_html: str, base_url: str | None = None) -> PageInfo:
    """Extrai imagem, descrição e corpo do HTML de uma matéria (função pura).

    ``base_url`` resolve URLs de imagem relativas. Imagens genéricas (logo,
    compartilhamento padrão) e textos de paywall são descartados.
    """
    parser = _PageParser()
    try:
        parser.feed(page_html[:HTML_MAX_CHARS])
        parser.close()
    except Exception:  # noqa: BLE001 — HTML patológico: usa o que foi lido até ali
        log.debug("falha ao interpretar HTML de %s", base_url, exc_info=True)
    meta = parser.meta
    ld = _jsonld_article(parser.jsonld)

    image = _pick_image(
        [
            meta.get("og:image:secure_url"),
            meta.get("og:image"),
            meta.get("og:image:url"),
            meta.get("twitter:image"),
            meta.get("twitter:image:src"),
            ld.image,
            meta.get("image_src"),
        ],
        base_url,
    )
    description = _pick_description(
        [meta.get("og:description"), meta.get("description"), meta.get("twitter:description"), ld.description]
    )
    body = _clean_body(ld.body) or _paragraph_body(parser.article_paragraphs or parser.main_paragraphs)
    return PageInfo(image=image, description=description, text=body)


def _looks_like_html(response: net.Response) -> bool:
    content_type = response.content_type.lower()
    return not content_type or any(kind in content_type for kind in ("html", "xml", "text/"))


def fetch_page_info(url: str, *, fetch: Fetcher = net.fetch, timeout: float = 12.0) -> PageInfo:
    """Baixa a página da matéria e extrai :class:`PageInfo`. Nunca levanta exceção."""
    try:
        response = fetch(url, timeout=timeout, retries=1, headers=PAGE_HEADERS, max_bytes=PAGE_MAX_BYTES)
        if not _looks_like_html(response):
            log.debug("página ignorada (%s): %s", response.content_type, url)
            return PageInfo()
        return parse_page(response.text(), base_url=response.url or url)
    except Exception as exc:  # noqa: BLE001 — enriquecimento é opcional
        log.debug("enriquecimento falhou para %s: %s", url, exc)
        return PageInfo()


def enrich(
    articles: list[Article],
    *,
    fetch: Fetcher = net.fetch,
    limit: int = 40,
    max_workers: int = 8,
) -> dict[str, PageInfo]:
    """Consulta a página original dos primeiros ``limit`` artigos (ids únicos, em paralelo).

    Devolve ``{Article.id: PageInfo}`` apenas para as páginas de onde algo foi
    extraído; falhas são silenciosas (log em nível DEBUG).
    """
    chosen: list[Article] = []
    seen: set[str] = set()
    for article in articles:
        if len(chosen) >= max(0, limit):
            break
        if article.id in seen or not article.url.lower().startswith(("http://", "https://")):
            continue
        seen.add(article.id)
        chosen.append(article)
    if not chosen:
        return {}
    infos = net.parallel_map(lambda a: fetch_page_info(a.url, fetch=fetch), chosen, max_workers=max_workers)
    result = {article.id: info for article, info in zip(chosen, infos, strict=True) if not info.is_empty()}
    log.info("enriquecimento: %d/%d páginas com dados", len(result), len(chosen))
    return result
