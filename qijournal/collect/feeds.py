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

# A janela real é max_age_hours (30 h) e o editor ainda limita os candidatos;
# 40 itens encurtavam a janela de feeds movimentados para poucas horas.
MAX_ITEMS_PER_FEED = 100
SUMMARY_MAX_CHARS = 1500
FUTURE_TOLERANCE = timedelta(hours=1)
FEED_TIMEOUT = 20.0
FEED_RETRIES = 2

# Parâmetros de rastreamento removidos da URL canônica (comparação sem caixa).
_TRACKING_PARAMS = {
    "mod", "fbclid", "gclid", "ref", "cmpid", "rss",
    # BBC, NYT, Mailchimp, Microsoft e similares
    "at_medium", "at_campaign", "smid", "ocid", "xtor", "mc_cid", "mc_eid", "msclkid", "igshid",
    "traffic_source",
}
_TRACKING_PREFIXES = ("utm_", "syn-")

# Redirecionador dos feeds da Folha: .../redir/online/<seção>/rss091/*https://www1.folha...
_FOLHA_REDIR = re.compile(r"^https?://redir\.folha\.com\.br/redir/[^*]*\*(https?://.+)$", re.I)

# Imagens que não são foto de matéria: pixels de rastreamento, ícones e logos genéricos.
_TRACKING_IMAGE = re.compile(
    r"/pixel(?:[./?_-]|$)|[._-]pixel\.(?:gif|png)|tracking|feedburner|doubleclick|/beacon"
    r"|(?<![0-9])1x1(?![0-9])|spacer\.gif|blank\.gif"
    # pixel de contagem da Agência Brasil nos feeds: .../ebc.png?id=...&o=rss (1x1 px)
    r"|/ebc\.(?:png|gif)(?:\?|$)|[?&]o=rss(?:&|$)",
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

# Boilerplate de feeds (WordPress, Guardian, portais brasileiros). Os padrões
# "inline" são removidos em qualquer posição (às vezes vêm colados na última
# frase ou no parágrafo seguinte); os de linha descartam a linha inteira.
_EMOJI_PREFIX = "[\u2705\U0001F4F1\U0001F5D2\u25B6\uFE0F\\s]*"
_BOILERPLATE_INLINE = [
    re.compile(r"The post .{1,300}? appeared first on [^\n]{1,120}?(?:\.(?=\s|$)|(?=\n)|$)", re.I | re.S),
    re.compile(r"O post .{1,300}? apareceu primeiro em [^\n]{1,120}?(?:\.(?=\s|$)|(?=\n)|$)", re.I | re.S),
    # Só no fim da linha/texto e com maiúscula: "Investors continue reading the minutes" é conteúdo.
    re.compile(r"\b(?:Continue|Keep) reading\s*(?:\.\.\.|…)?[ \t]*(?=\n|\Z)"),
    re.compile(r"^\s*\(Reuters\)\s*[-–—]\s*", re.I),  # "(Reuters) - " no início do texto
    # Paywall do Valor e do JOTA
    re.compile(r"(?:Mat[ée]ria|Conte[úu]do|Reportagem)\s+exclusiv[ao]\s+para\s+assinantes\.?", re.I),
    re.compile(r"Para ter acesso completo,?\s+acesse o link da mat[ée]ria e fa[çc]a (?:o )?seu cadastro\.?", re.I),
    re.compile(r"Esta reportagem foi antecipada a assinantes[^\n]*", re.I),
    # Chamadas do g1 (WhatsApp, Google, sugestão de pauta), às vezes na mesma linha do parágrafo seguinte
    re.compile(
        _EMOJI_PREFIX + r"(?:Clique (?:aqui )?(?:e|para) )?(?:siga|seguir) o canal[^\n]{0,80}?no WhatsApp\.?", re.I
    ),
    re.compile(_EMOJI_PREFIX + r"Favorite o g1 no Google e acompanhe as principais not[íi]cias do dia\.?", re.I),
    re.compile(_EMOJI_PREFIX + r"Tem alguma sugest[ãa]o de reportagem\?\s*(?:Mande|Envie)(?: para o g1)?\.?", re.I),
    re.compile(r"Conhe[çc]a o JOTA PRO[^\n]{0,160}?para empresas(?: e escrit[óo]rios)?\.?", re.I),
    re.compile(r"Get our breaking news email, free app or daily news podcast\.?", re.I),
    re.compile(r"\s*Clique aqui para saber mais(?:\.|…)?(?=\n|\Z)", re.I),  # nota de rodapé da CNN Brasil
]
_BOILERPLATE_LINE = re.compile(
    r"^(?:Leia (?:mais|também)|Veja também|Saiba mais|Read more|Assine\b|\(Reuters\)[\s.:-]*$"
    r"|(?:V[ÍI]DEOS:\s*)?Agora no g1\b|Volte ao topo\b|Initial plugin text$|Quer receber (?:reportagens|not[íi]cias)"
    r"|Seguir leyendo|Conhe[çc]a o JOTA PRO|Get our breaking news email"
    r"|Follow (?:the day.s news live|our .{0,60}live)|Esta reportagem foi antecipada a assinantes"
    r"|Sign up for (?:the |our )?.{0,60}newsletter"
    r"|" + _EMOJI_PREFIX + r"(?:Clique (?:aqui|e siga)|Siga o canal|Favorite o g1|Tem alguma sugest))",
    re.I,
)
_BOILERPLATE_LINE_MAX = 200  # linhas longas começando por "Assine..." provavelmente são conteúdo

# "Notícias relacionadas:" (Agência Brasil) é seguido de até 3 títulos de outras
# matérias: linhas curtas, sem vírgula seguida de espaço (prosa tem vírgulas; os
# títulos, no máximo "R$ 2,12").
_RELATED_HEADER = re.compile(r"^(?:Not[íi]cias relacionadas|Mat[ée]rias relacionadas|Related)\s*:?$", re.I)
_RELATED_ITEM_MAX = 110
_RELATED_ITEMS = 3

# Crédito de foto numa linha própria ("Reprodução/Globo", "Ann Wang/Reuters",
# "Arte/g1", "Pixabay", "Divulgação"): curta, sem pontuação final.
_CREDIT_MAX = 60
_CREDIT_START = re.compile(
    r"^(?:Arte|Fotos?|Reprodu[çc][ãa]o|Divulga[çc][ãa]o|Pixabay|Getty Images|AP|AFP|Reuters|EFE)(?:\s*/.*)?$", re.I
)
_CREDIT_SLASH = re.compile(  # "Nome/Agência": sem dígitos, ou terminando numa agência conhecida
    r"^[^/\d]{1,40}\s?/\s?[^/\d]{1,40}$"
    r"|^[^/]{1,40}/\s?(?:g1|Pixabay|Reuters|AFP|AP|Getty Images.*|Ag[êe]ncia Brasil|Folhapress|Estad[ãa]o)$",
    re.I,
)
_CAPTION_MAX = 160  # legenda (linha curta sem pontuação) logo antes de um crédito
_HEADLINE_RUN_MAX = 120  # título de outra matéria, sem pontuação final, em sequência com outros
_SENTENCE_FINAL = re.compile(r"[.!?…:;][\"'”’)\]]*$")
_TERMINAL = re.compile(r"[.!?…][\"'”’)\]]*$")

# g1: item que abre com a foto; o texto entre a imagem e o 1º parágrafo é a legenda.
_G1_PHOTO_CAPTION = re.compile(r"\s*(?:<(?:figure|div)[^>]*>\s*)?<img\b[^>]*>(.*?)<p\b", re.I | re.S)

# Conteúdo patrocinado (publieditorial) identificado pelo texto do próprio item.
# Só o aviso explícito (numa linha própria, no caso dos rótulos em português): uma
# notícia sobre publicidade pode citar "conteúdo patrocinado" no meio do texto.
_SPONSORED = re.compile(
    r"produced by our advertising partner|^\W*(?:conte[úu]do patrocinado|publieditorial|sponsored content)\W*$",
    re.I | re.M,
)

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


def compile_title_patterns(patterns: Iterable[str]) -> re.Pattern[str] | None:
    """Uma regex com as exclusões por título (casadas no título normalizado, sem acento)."""
    parts = [p for p in patterns if p]
    if not parts:
        return None
    return re.compile("|".join(f"(?:{p})" for p in parts))


def is_excluded_title(title: str, pattern: re.Pattern[str] | None) -> bool:
    """Título de conteúdo de serviço (lista de candidatos, loteria, horóscopo...)?"""
    return bool(pattern and pattern.search(text.normalize(title)))


def is_sponsored(summary: str) -> bool:
    """Publieditorial/conteúdo patrocinado declarado no texto do item."""
    return bool(_SPONSORED.search(summary or ""))


# Dica de seção pelo caminho da URL, para feeds gerais (capas) sem ``topics``.
_URL_TOPICS = [
    (re.compile(r"/(?:internacional|mundo|world)/"), "mundo"),
    (re.compile(r"/(?:politica|eleicoes(?:-\d{4})?)/"), "politica"),
    (re.compile(r"/(?:legislacao|justica|juridico)/"), "juridico"),
    (re.compile(r"/(?:imoveis|imobiliario|fundos-imobiliarios)/"), "imobiliario"),
    (re.compile(r"/(?:financas|mercados|investimentos|markets)/"), "mercados"),
    (re.compile(r"/(?:tecnologia|tech|technology|empresas|negocios)/"), "tecnologia"),
    (re.compile(r"/(?:economia|brasil)/"), "brasil"),
]


def url_topics(url: str) -> list[str]:
    """Dica de seção deduzida do caminho da URL (``/internacional/`` → ``mundo``...)."""
    path = urlsplit(url).path.lower()
    return [topic for pattern, topic in _URL_TOPICS if pattern.search(path)][:1]


# ── Imagens ──────────────────────────────────────────────────────────────────


def _to_int(value: Any) -> int | None:
    """Número inicial de um atributo de dimensão (``"600"``, ``"1px"``, ``"300.0"``); senão ``None``."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(value if value is not None else ""))
    return int(float(match.group(1))) if match else None


def _style_size(style: str, name: str) -> int | None:
    match = re.search(rf"(?:^|;)\s*{name}\s*:\s*(\d+)px", style or "", re.I)
    return int(match.group(1)) if match else None


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
            style = attrs.get("style") or ""
            width = _to_int(attrs.get("width")) if attrs.get("width") else _style_size(style, "width")
            height = _to_int(attrs.get("height")) if attrs.get("height") else _style_size(style, "height")
            if url and is_usable_image_url(url, width, height):
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


def _is_credit(line: str) -> bool:
    """Linha só com crédito de foto ("Reprodução/Globo", "Ann Wang/Reuters", "Pixabay")."""
    if len(line) > _CREDIT_MAX or _SENTENCE_FINAL.search(line):
        return False
    return bool(_CREDIT_START.match(line) or (_CREDIT_SLASH.match(line) and len(line.split()) <= 6))


def _filter_lines(lines: list[str]) -> list[str]:
    """Descarta linhas de boilerplate, blocos de "Notícias relacionadas" e créditos/legendas de foto."""
    out: list[str] = []
    related_left = 0
    for line in lines:
        if related_left:
            if len(line) <= _RELATED_ITEM_MAX and ", " not in line:
                related_left -= 1
                continue
            related_left = 0
        if _RELATED_HEADER.match(line):
            related_left = _RELATED_ITEMS
            continue
        if len(line) <= _BOILERPLATE_LINE_MAX and _BOILERPLATE_LINE.match(line):
            continue
        if _is_credit(line):
            # a legenda (linha curta sem pontuação final) vem logo antes do crédito
            if out and len(out[-1]) <= _CAPTION_MAX and not _SENTENCE_FINAL.search(out[-1]):
                out.pop()
            continue
        out.append(line)
    return _drop_headline_runs(out)


def _drop_headline_runs(lines: list[str]) -> list[str]:
    """Tira sequências de 2+ linhas curtas sem pontuação final no meio do texto: são
    chamadas para outras matérias sem cabeçalho (Valor: "Quaest: Lula retoma
    liderança…" / "Gilmar quer suspender…") ou rótulos de gráfico. Listas de
    cotações ("Dólar: R$ 5,22;") têm pontuação e ficam."""

    def is_headline(line: str) -> bool:
        # item de lista que continua a frase ("…em 2025," / "…em junho, e") não é título
        continues = line.endswith(",") or bool(re.search(r"\s(?:e|ou|and|or)$", line))
        return len(line) <= _HEADLINE_RUN_MAX and not _SENTENCE_FINAL.search(line) and not continues

    keep = [True] * len(lines)
    start = 0
    while start < len(lines):
        end = start
        while end < len(lines) and is_headline(lines[end]):
            end += 1
        if end - start >= 2:
            keep[start:end] = [False] * (end - start)
        start = max(end, start + 1)
    kept = [line for line, ok in zip(lines, keep, strict=True) if ok]
    return kept if kept else lines


def strip_boilerplate(value: str | None, *, source_id: str = "", title: str = "") -> str:
    """Remove boilerplate e avisos de paywall de um texto que já é puro (sem HTML).

    Usado por :func:`clean_summary` e também para limpar de novo bundles salvos
    e o texto das páginas enriquecidas (regras novas valem para dados antigos).
    """
    result = value or ""
    for pattern in _BOILERPLATE_INLINE:
        result = pattern.sub(" ", result)
    lines = [" ".join(line.split()) for line in result.split("\n")]
    lines = _filter_lines([line for line in lines if line])
    lines = [line for line in (text.strip_paywall(line) for line in lines) if line]
    if lines and title and text.normalize(lines[0]) == text.normalize(title):
        lines = lines[1:]
    # g1: a 1ª linha costuma ser legenda de foto ou chamada de outra matéria
    # (sem pontuação final), seguida do texto de verdade.
    if source_id.startswith("g1") and len(lines) > 1 and not _TERMINAL.search(lines[0]):
        lines = lines[1:]
    return "\n".join(lines)


def clean_summary(value: str | None, title: str = "", source_id: str = "") -> str:
    """Resumo em texto puro sem boilerplate de feed (parágrafos separados por ``\\n``).

    Remove "The post ... appeared first on ...", "Continue reading...",
    linhas "Leia mais"/"Leia também"/"Assine...", "(Reuters)" isolado, avisos
    de paywall ("Matéria exclusiva para assinantes..."), chamadas dos portais
    ("Siga o canal do g1 no WhatsApp", "Seguir leyendo"), blocos "Notícias
    relacionadas", créditos de foto e a repetição do título na primeira linha.
    Não trunca; um resumo que só tinha boilerplate vira ``""``.
    """
    raw = value or ""
    result = _plain(raw)
    # g1: item que começa por foto (<img>/<figure>) com texto antes do 1º <p> → esse texto é a legenda
    if source_id.startswith("g1"):
        caption = _G1_PHOTO_CAPTION.match(raw)
        if caption and text.html_to_text(caption.group(1)).strip():
            first, _, rest = result.partition("\n")
            if rest:
                result = rest
    return strip_boilerplate(result, source_id=source_id, title=title)


def _entry_summary(entry: Any, title: str, source_id: str = "") -> str:
    """Melhor resumo entre ``content`` (ex.: ``content:encoded``) e ``summary``/``description``."""
    raw = [c.get("value", "") for c in entry.get("content") or []]
    raw.append(entry.get("summary") or "")
    candidates = [clean_summary(value, title, source_id) for value in raw if value]
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
    exclude_titles: re.Pattern[str] | None = None,
) -> Article | None:
    title = clean_title(entry.get("title"))
    link = _entry_link(entry)
    if not title or not link:
        return None
    url = canonical_url(link)
    if not url.startswith("https://") or _is_excluded(url, exclude) or is_excluded_title(title, exclude_titles):
        return None
    published = _entry_datetime(entry)
    if published is not None:
        if published < cutoff:
            return None
        if published > now + FUTURE_TOLERANCE:
            published = now
    summary = _entry_summary(entry, title, source.id)
    if is_sponsored(summary):
        return None
    return Article(
        id=text.short_hash(url),
        url=url,
        title=title,
        summary=summary,
        source_id=source.id,
        source_name=source.name,
        lang=source.lang,
        published=published.isoformat() if published else None,
        image=_entry_image(entry),
        topics=list(source.topics) or url_topics(url),
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
    exclude_titles: re.Pattern[str] | None = None,
) -> list[Article]:
    now = _as_utc(now).replace(microsecond=0)
    cutoff = now - timedelta(hours=max_age_hours)
    articles = []
    for entry in entries:
        try:
            article = _entry_to_article(
                entry, source, now=now, cutoff=cutoff, exclude=exclude, exclude_titles=exclude_titles
            )
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
    exclude_titles: re.Pattern[str] | None = None,
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
    return _entries_to_articles(
        entries, source, now=now, max_age_hours=max_age_hours, exclude=exclude, exclude_titles=exclude_titles
    )


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
    exclude_titles: re.Pattern[str] | None = None,
) -> tuple[list[Article], SourceStatus]:
    """Baixa e interpreta um feed; nunca levanta exceção."""
    start = time.monotonic()
    articles: list[Article] = []
    error: str | None = None
    try:
        response = fetch(source.url, timeout=FEED_TIMEOUT, retries=FEED_RETRIES)
        entries = _load_entries(response.content, source.url)
        articles = _entries_to_articles(
            entries, source, now=now, max_age_hours=max_age_hours, exclude=exclude, exclude_titles=exclude_titles
        )
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
    exclude_title_patterns: Iterable[str] = (),
) -> tuple[list[Article], list[SourceStatus]]:
    """Baixa todos os feeds em paralelo e devolve ``(artigos, status por URL de feed)``.

    A falha de um feed (HTTP, rede, XML inválido, feed sem itens) vira um
    ``SourceStatus(ok=False, error=...)`` e não afeta os demais; um feed válido
    cujos itens foram todos filtrados (antigos ou excluídos) fica ``ok`` com
    ``items=0``. Fontes desabilitadas e URLs repetidas são ignoradas. Os artigos
    não são deduplicados entre feeds (ver :func:`dedupe_articles`).
    ``exclude_title_patterns``: regexes casadas no título normalizado (conteúdo de
    serviço, como listas de candidatos e resultados de loteria).
    """
    now = _as_utc(now)
    title_pattern = compile_title_patterns(exclude_title_patterns)
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
        return _collect_one(
            source, now=now, max_age_hours=max_age_hours, exclude=exclude, fetch=fetch, exclude_titles=title_pattern
        )

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


# ── Bundles salvos ───────────────────────────────────────────────────────────


def sanitize_articles(
    articles: list[Article],
    *,
    exclude_url_patterns: Iterable[str] = (),
    exclude_title_patterns: Iterable[str] = (),
) -> list[Article]:
    """Reaplica as regras atuais de coleta a artigos já coletados (bundle salvo).

    Limpa de novo o resumo (boilerplate, paywall), descarta imagens que hoje
    seriam rejeitadas (pixels de rastreamento) e remove artigos excluídos por URL,
    por título ou patrocinados. Assim ``render --bundle`` reproduz as regras
    vigentes mesmo com bundles antigos. Não altera os objetos de entrada.
    """
    patterns = list(exclude_url_patterns)
    title_pattern = compile_title_patterns(exclude_title_patterns)
    out: list[Article] = []
    for article in articles:
        if _is_excluded(article.url, patterns) or is_excluded_title(article.title, title_pattern):
            continue
        summary = strip_boilerplate(article.summary, source_id=article.source_id, title=article.title)
        if is_sponsored(article.summary):
            continue
        image = article.image if is_usable_image_url(article.image) else None
        out.append(replace(article, summary=summary, image=image, topics=list(article.topics)))
    return out
