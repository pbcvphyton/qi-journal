"""Coleta de feeds RSS/Atom e conversão dos itens em :class:`~qijournal.models.Article`.

Fluxo: :func:`collect_feeds` baixa cada feed (em paralelo, com ``fetch``
injetável), :func:`parse_feed` transforma o XML em artigos (função pura) e
:func:`dedupe_articles` elimina repetições pela URL canônica. O agrupamento de
matérias diferentes sobre o mesmo fato é responsabilidade do editor.

Os feeds reais variam muito (RSS 0.91 em ISO-8859-1 da Folha, WordPress com
``content:encoded``, ``media:content`` do Guardian/NYT, Atom sem datas...);
o ``feedparser`` normaliza a estrutura e aqui ficam as regras editoriais:
limpeza de texto, datas, imagem, filtros de URL e limite por feed.
"""

from __future__ import annotations

import calendar
import html
import io
import logging
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import feedparser

from .. import net, text
from ..config import SourceConfig
from ..models import Article, SourceStatus
from ..net import Fetcher, FetchError

log = logging.getLogger(__name__)

MAX_ITEMS_PER_FEED = 40
SUMMARY_MAX_CHARS = 1500
FUTURE_TOLERANCE = timedelta(hours=1)
FEED_TIMEOUT = 20.0
FEED_RETRIES = 2

# Parâmetros de rastreamento removidos da URL canônica (comparação sem caixa).
_TRACKING_PARAMS = {
    "mod", "fbclid", "gclid", "ref", "cmpid", "rss",
    # BBC, NYT, Mailchimp, Microsoft e similares
    "at_medium", "at_campaign", "smid", "ocid", "xtor", "mc_cid", "mc_eid", "msclkid", "igshid",
}
_TRACKING_PREFIXES = ("utm_", "syn-")

# Redirecionador dos feeds da Folha: .../redir/online/<seção>/rss091/*https://www1.folha...
_FOLHA_REDIR = re.compile(r"^https?://redir\.folha\.com\.br/redir/[^*]*\*(https?://.+)$", re.I)

# Imagens que não são foto de matéria: pixels de rastreamento, ícones e logos genéricos.
_TRACKING_IMAGE = re.compile(
    r"/pixel(?:[./?_-]|$)|[._-]pixel\.(?:gif|png)|tracking|feedburner|doubleclick|/beacon"
    r"|(?<![0-9])1x1(?![0-9])|spacer\.gif|blank\.gif",
    re.I,
)
_GENERIC_IMAGE = re.compile(
    r"logo|share-default|default-share|facebook-share|default-image|og-default|placeholder"
    r"|no-image|sem-imagem|favicon|avatar",
    re.I,
)
_MIN_GIF_WIDTH = 200
_MAX_PIXEL_SIZE = 10

# Restos de tags depois da primeira conversão (HTML escapado duas vezes pelo publicador).
_RESIDUAL_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^<>]*>")

# Boilerplate de feeds (WordPress, Guardian, portais brasileiros).
_BOILERPLATE_INLINE = [
    re.compile(r"The post .{1,300}? appeared first on [^\n]{1,120}?(?:\.(?=\s|$)|(?=\n)|$)", re.I | re.S),
    re.compile(r"O post .{1,300}? apareceu primeiro em [^\n]{1,120}?(?:\.(?=\s|$)|(?=\n)|$)", re.I | re.S),
    re.compile(r"\b(?:Continue|Keep) reading\s*(?:\.\.\.|…)?", re.I),
    re.compile(r"^\s*\(Reuters\)\s*[-–—]\s*", re.I),  # "(Reuters) - " no início do texto
]
_BOILERPLATE_LINE = re.compile(
    r"^(?:Leia (?:mais|também)|Veja também|Saiba mais|Read more|Assine\b|\(Reuters\)[\s.:-]*$)",
    re.I,
)
_BOILERPLATE_LINE_MAX = 200  # linhas longas começando por "Assine..." provavelmente são conteúdo

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_HTML_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")


class FeedError(Exception):
    """Feed baixado, mas inutilizável (XML inválido ou sem itens)."""


# ── URLs ─────────────────────────────────────────────────────────────────────


def _is_tracking_param(name: str) -> bool:
    name = name.lower()
    return name in _TRACKING_PARAMS or name.startswith(_TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Normaliza a URL de uma matéria para deduplicação e links.

    Desembrulha o redirecionador da Folha, força ``https``, põe o host em
    minúsculas, remove porta padrão, fragmento e parâmetros de rastreamento
    (``utm_*``, ``mod``, ``fbclid``, ``gclid``, ``ref``, ``cmpid``, ``syn-*``,
    ``rss`` e afins), preservando os demais parâmetros na forma original.
    A função é idempotente.
    """
    url = (url or "").strip()
    if not url:
        return ""
    match = _FOLHA_REDIR.match(url)
    if match:
        url = match.group(1)
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return url
    netloc = parts.netloc.lower()
    if netloc.endswith(":80") or netloc.endswith(":443"):
        netloc = netloc.rsplit(":", 1)[0]
    kept = [
        pair
        for pair in parts.query.split("&")
        if pair and not _is_tracking_param(pair.split("=", 1)[0])
    ]
    return urlunsplit(("https", netloc, parts.path or "/", "&".join(kept), ""))


def article_id(url: str) -> str:
    """Identificador estável do artigo: hash curto da URL canônica."""
    return text.short_hash(canonical_url(url))


def _is_excluded(url: str, patterns: Iterable[str]) -> bool:
    lowered = url.lower()
    return any(p and p.lower() in lowered for p in patterns)


# ── Imagens ──────────────────────────────────────────────────────────────────


def _to_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def is_usable_image_url(url: str | None, width: int | None = None, height: int | None = None) -> bool:
    """``True`` se a URL parece uma foto de matéria utilizável.

    Rejeita esquemas que não sejam http(s), pixels de rastreamento
    (``pixel``, ``tracking``, ``feedburner``, ``1x1``...), imagens minúsculas,
    GIFs pequenos ou sem dimensão conhecida e logos/imagens genéricas de
    compartilhamento (``logo``, ``share-default``, ``default-image``...).
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    if _TRACKING_IMAGE.search(url) or _GENERIC_IMAGE.search(url):
        return False
    if (width is not None and width <= _MAX_PIXEL_SIZE) or (height is not None and height <= _MAX_PIXEL_SIZE):
        return False
    path = urlsplit(url).path.lower()
    if path.endswith(".gif") and (width is None or width < _MIN_GIF_WIDTH):
        return False
    return True


def _normalize_image_url(url: str | None) -> str | None:
    if not url:
        return None
    url = html.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    return url


def _is_image_media(media: dict[str, Any]) -> bool:
    """``media:content`` sem ``medium``/``type`` (ex.: Guardian) é tratado como imagem."""
    medium = str(media.get("medium") or "").lower()
    mime = str(media.get("type") or "").lower()
    if medium and medium != "image":
        return False
    if mime and not mime.startswith("image/"):
        return False
    return True


def _media_candidates(entry: Any) -> list[tuple[int, str]]:
    """(largura, url) de ``media:content``/``media:thumbnail`` utilizáveis."""
    found: list[tuple[int, str]] = []
    media_items = list(entry.get("media_content") or []) + list(entry.get("media_thumbnail") or [])
    for media in media_items:
        if not isinstance(media, dict) or not _is_image_media(media):
            continue
        url = _normalize_image_url(media.get("url"))
        width, height = _to_int(media.get("width")), _to_int(media.get("height"))
        if url and is_usable_image_url(url, width, height):
            found.append((width or 0, url))
    return found


def _first_html_image(fragments: Iterable[str]) -> str | None:
    """Primeira ``<img>`` utilizável em trechos de HTML (``src`` ou ``data-src``)."""
    for fragment in fragments:
        for tag in _IMG_TAG.findall(fragment or ""):
            attrs = {m[0].lower(): (m[1] or m[2] or m[3]) for m in _HTML_ATTR.findall(tag)}
            src = attrs.get("src") or ""
            if not src or src.startswith("data:"):
                src = attrs.get("data-src") or attrs.get("data-lazy-src") or ""
            url = _normalize_image_url(src)
            if url and is_usable_image_url(url, _to_int(attrs.get("width")), _to_int(attrs.get("height"))):
                return url
    return None


def _entry_image(entry: Any) -> str | None:
    """Imagem do item: mídia RSS de maior largura → enclosure de imagem → ``<img>`` no conteúdo."""
    media = _media_candidates(entry)
    if media:
        best_width = max(width for width, _ in media)
        return next(url for width, url in media if width == best_width)
    for enclosure in entry.get("enclosures") or []:
        mime = str(enclosure.get("type") or "").lower()
        url = _normalize_image_url(enclosure.get("href") or enclosure.get("url"))
        if mime.startswith("image/") and url and is_usable_image_url(url):
            return url
    fragments = [c.get("value", "") for c in entry.get("content") or []]
    fragments.append(entry.get("summary") or "")
    return _first_html_image(fragments)


# ── Texto ────────────────────────────────────────────────────────────────────


def _plain(value: str | None) -> str:
    """HTML → texto puro; uma segunda passada remove tags escapadas duas vezes."""
    result = text.html_to_text(value or "")
    if _RESIDUAL_TAG.search(result):
        result = text.html_to_text(result)
    return result


def clean_title(value: str | None) -> str:
    """Título em texto puro, numa linha, com espaços colapsados."""
    return " ".join(_plain(value).split())


def clean_summary(value: str | None, title: str = "") -> str:
    """Resumo em texto puro sem boilerplate de feed (parágrafos separados por ``\\n``).

    Remove "The post ... appeared first on ...", "Continue reading...",
    linhas "Leia mais"/"Leia também"/"Assine...", "(Reuters)" isolado e a
    repetição do título na primeira linha. Não trunca.
    """
    result = _plain(value)
    for pattern in _BOILERPLATE_INLINE:
        result = pattern.sub(" ", result)
    lines = []
    for line in result.split("\n"):
        line = " ".join(line.split())
        if not line:
            continue
        if len(line) <= _BOILERPLATE_LINE_MAX and _BOILERPLATE_LINE.match(line):
            continue
        lines.append(line)
    if lines and title and text.normalize(lines[0]) == text.normalize(title):
        lines = lines[1:]
    return "\n".join(lines)


def _entry_summary(entry: Any, title: str) -> str:
    """Melhor resumo entre ``content`` (ex.: ``content:encoded``) e ``summary``/``description``."""
    raw = [c.get("value", "") for c in entry.get("content") or []]
    raw.append(entry.get("summary") or "")
    candidates = [clean_summary(value, title) for value in raw if value]
    best = max(candidates, key=len, default="")
    return text.truncate(best, SUMMARY_MAX_CHARS)


# ── Datas ────────────────────────────────────────────────────────────────────


def _as_utc(moment: datetime) -> datetime:
    """Garante ``datetime`` com fuso UTC (ingênuo é interpretado como UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if not parsed:
            continue
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
        except (OverflowError, ValueError, TypeError, OSError):
            continue
    return None


# ── Parsing ──────────────────────────────────────────────────────────────────


def _entry_link(entry: Any) -> str:
    link = (entry.get("link") or "").strip()
    if link.lower().startswith(("http://", "https://", "//")):
        return link
    for candidate in entry.get("links") or []:
        href = (candidate.get("href") or "").strip()
        if candidate.get("rel", "alternate") == "alternate" and href.lower().startswith(("http://", "https://")):
            return href
    guid = (entry.get("id") or "").strip()
    return guid if guid.lower().startswith(("http://", "https://")) else ""


def _load_entries(content: bytes, base_url: str | None = None) -> list[Any]:
    """Interpreta o XML e devolve os itens; levanta :class:`FeedError` se inutilizável."""
    if not content or not content.strip():
        raise FeedError("resposta vazia")
    headers = {"content-location": base_url} if base_url else None
    # BytesIO: o feedparser nunca deve interpretar os bytes como caminho de arquivo ou URL.
    # sanitize_html=False: todo HTML vira texto puro aqui (html_to_text descarta <script> etc.)
    # e o sanitizador removeria atributos úteis como data-src de imagens com lazy loading.
    parsed = feedparser.parse(io.BytesIO(content), response_headers=headers, sanitize_html=False)
    entries = list(parsed.get("entries") or [])
    if not entries:
        raise FeedError("0 itens" if parsed.get("version") else "XML inválido")
    if parsed.get("bozo"):
        log.debug("feed %s com problemas de XML (%s); itens recuperados: %d",
                  base_url, parsed.get("bozo_exception"), len(entries))
    return entries


def _entry_to_article(
    entry: Any,
    source: SourceConfig,
    *,
    now: datetime,
    cutoff: datetime,
    exclude: list[str],
) -> Article | None:
    title = clean_title(entry.get("title"))
    link = _entry_link(entry)
    if not title or not link:
        return None
    url = canonical_url(link)
    if not url.startswith("https://") or _is_excluded(url, exclude):
        return None
    published = _entry_datetime(entry)
    if published is not None:
        if published < cutoff:
            return None
        if published > now + FUTURE_TOLERANCE:
            published = now
    return Article(
        id=text.short_hash(url),
        url=url,
        title=title,
        summary=_entry_summary(entry, title),
        source_id=source.id,
        source_name=source.name,
        lang=source.lang,
        published=published.isoformat() if published else None,
        image=_entry_image(entry),
        topics=list(source.topics),
        weight=source.weight,
        feed_url=source.url,
    )


def _sort_timestamp(article: Article) -> float:
    if not article.published:
        return 0.0
    return -datetime.fromisoformat(article.published).timestamp()


def _entries_to_articles(
    entries: list[Any],
    source: SourceConfig,
    *,
    now: datetime,
    max_age_hours: int,
    exclude: list[str],
) -> list[Article]:
    now = _as_utc(now).replace(microsecond=0)
    cutoff = now - timedelta(hours=max_age_hours)
    articles = []
    for entry in entries:
        try:
            article = _entry_to_article(entry, source, now=now, cutoff=cutoff, exclude=exclude)
        except Exception:  # noqa: BLE001 — um item malformado não invalida o feed inteiro
            log.debug("item ignorado no feed %s", source.url, exc_info=True)
            continue
        if article is not None:
            articles.append(article)
    articles = dedupe_articles(articles)
    # Mais recentes primeiro; itens sem data vão para o fim, na ordem do feed.
    articles.sort(key=lambda a: (a.published is None, _sort_timestamp(a)))
    return articles[:MAX_ITEMS_PER_FEED]


def parse_feed(
    content: bytes,
    source: SourceConfig,
    *,
    now: datetime,
    max_age_hours: int,
    exclude: list[str],
) -> list[Article]:
    """Converte o conteúdo de um feed RSS/Atom em artigos (função pura, sem rede).

    Descarta itens sem título ou link, com URL em ``exclude`` ou mais velhos
    que ``max_age_hours``; datas no futuro (além de 1 h) viram ``now``; itens
    sem data são mantidos com ``published=None``. Devolve no máximo
    ``MAX_ITEMS_PER_FEED`` itens, mais recentes primeiro. Feed inválido ou vazio
    resulta em lista vazia (o motivo aparece em :func:`collect_feeds`).
    """
    try:
        entries = _load_entries(content, source.url)
    except FeedError as exc:
        log.debug("feed %s sem itens aproveitáveis: %s", source.url, exc)
        return []
    return _entries_to_articles(entries, source, now=now, max_age_hours=max_age_hours, exclude=exclude)


# ── Coleta ───────────────────────────────────────────────────────────────────


def _fetch_error_message(exc: FetchError) -> str:
    """Mensagem curta para o status da fonte (sem a URL)."""
    if exc.status:
        return f"HTTP {exc.status}"
    message = str(exc)
    suffix = f" ({exc.url})"
    if message.endswith(suffix):
        message = message[: -len(suffix)]
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "certificate" in lowered:
        return "erro de certificado SSL"
    return text.truncate(f"erro de rede: {message}", 80)


def _collect_one(
    source: SourceConfig,
    *,
    now: datetime,
    max_age_hours: int,
    exclude: list[str],
    fetch: Fetcher,
) -> tuple[list[Article], SourceStatus]:
    """Baixa e interpreta um feed; nunca levanta exceção."""
    start = time.monotonic()
    articles: list[Article] = []
    error: str | None = None
    try:
        response = fetch(source.url, timeout=FEED_TIMEOUT, retries=FEED_RETRIES)
        entries = _load_entries(response.content, source.url)
        articles = _entries_to_articles(entries, source, now=now, max_age_hours=max_age_hours, exclude=exclude)
    except FetchError as exc:
        error = _fetch_error_message(exc)
    except FeedError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 — um feed com problema nunca derruba os demais
        log.warning("erro inesperado ao processar o feed %s", source.url, exc_info=True)
        error = f"erro inesperado: {type(exc).__name__}"
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if error:
        log.warning("feed %s (%s) falhou: %s", source.id, source.url, error)
    else:
        log.debug("feed %s (%s): %d itens em %d ms", source.id, source.url, len(articles), elapsed_ms)
    status = SourceStatus(
        source_id=source.id,
        source_name=source.name,
        url=source.url,
        ok=error is None,
        items=len(articles),
        error=error,
        elapsed_ms=elapsed_ms,
    )
    return articles, status


def collect_feeds(
    sources: list[SourceConfig],
    *,
    now: datetime,
    max_age_hours: int,
    global_exclude: list[str],
    fetch: Fetcher = net.fetch,
    max_workers: int = 12,
) -> tuple[list[Article], list[SourceStatus]]:
    """Baixa todos os feeds em paralelo e devolve ``(artigos, status por URL de feed)``.

    A falha de um feed (HTTP, rede, XML inválido, feed sem itens) vira um
    ``SourceStatus(ok=False, error=...)`` e não afeta os demais; um feed válido
    cujos itens foram todos filtrados (antigos ou excluídos) fica ``ok`` com
    ``items=0``. Fontes desabilitadas e URLs repetidas são ignoradas. Os artigos
    não são deduplicados entre feeds (ver :func:`dedupe_articles`).
    """
    now = _as_utc(now)
    unique: list[SourceConfig] = []
    seen_urls: set[str] = set()
    for source in sources:
        if not source.enabled:
            continue
        if source.url in seen_urls:
            log.warning("feed repetido na configuração ignorado: %s", source.url)
            continue
        seen_urls.add(source.url)
        unique.append(source)

    def work(source: SourceConfig) -> tuple[list[Article], SourceStatus]:
        exclude = [*global_exclude, *source.exclude_url_patterns]
        return _collect_one(source, now=now, max_age_hours=max_age_hours, exclude=exclude, fetch=fetch)

    results = net.parallel_map(work, unique, max_workers=max_workers)
    articles = [article for batch, _ in results for article in batch]
    statuses = [status for _, status in results]
    ok = sum(1 for status in statuses if status.ok)
    log.info("feeds: %d/%d ok, %d artigos coletados", ok, len(statuses), len(articles))
    return articles, statuses


# ── Deduplicação ─────────────────────────────────────────────────────────────


def _merge_duplicate(kept: Article, other: Article) -> Article:
    """Combina dois artigos com a mesma URL canônica (``kept`` tem prioridade)."""
    topics = list(kept.topics) + [t for t in other.topics if t not in kept.topics]
    return replace(
        kept,
        topics=topics,
        image=kept.image or other.image,
        published=kept.published or other.published,
    )


def dedupe_articles(articles: list[Article]) -> list[Article]:
    """Mantém um artigo por ``id`` (URL canônica), na ordem da primeira ocorrência.

    Preferência: maior ``weight``; empate → resumo mais longo. O escolhido herda
    imagem (e data) do descartado se não tiver, e os ``topics`` são unidos.
    Não altera os objetos de entrada.
    """
    order: list[str] = []
    best: dict[str, Article] = {}
    for article in articles:
        current = best.get(article.id)
        if current is None:
            order.append(article.id)
            best[article.id] = replace(article, topics=list(article.topics))
            continue
        challenger_wins = (article.weight, len(article.summary)) > (current.weight, len(current.summary))
        if challenger_wins:
            best[article.id] = _merge_duplicate(article, current)
        else:
            best[article.id] = _merge_duplicate(current, article)
    return [best[article_key] for article_key in order]
