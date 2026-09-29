"""Filtros e utilitários de apresentação usados pelos templates (web e e-mail).

Regra de segurança: todo texto vindo de feeds ou da IA é tratado como não
confiável. ``md_lite`` é o ÚNICO caminho pelo qual esse texto vira
``Markup`` — ele escapa tudo antes de reintroduzir ``<strong>``. URLs passam
por ``safe_url`` (só http/https) antes de irem para ``href``/``src``.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from .. import text

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# **negrito** com conteúdo não vazio que não começa/termina com espaço.
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_XML_PROLOG = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>|<!--.*?-->", re.S | re.I)

# Abreviações amigáveis: o banco IANA usa "-03" para São Paulo.
_TZ_LABELS = {"America/Sao_Paulo": "BRT"}


def md_lite(value: str | None) -> Markup:
    """Escapa o texto e converte apenas ``**x**`` em ``<strong>x</strong>``.

    Asteriscos que sobrarem (negrito sem par, itálico etc.) são removidos,
    para que nenhum ``*`` de markdown vaze para o leitor.
    """
    if not value:
        return Markup("")
    escaped = str(escape(value))
    html = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    return Markup(html.replace("*", ""))


def plain(value: str | None) -> str:
    """Texto puro, sem marcação markdown (para títulos, atributos e e-mail em texto)."""
    return text.strip_markdown(value or "")


def safe_url(url: str | None) -> str | None:
    """Devolve a URL se for http(s) absoluta e bem formada; senão ``None``.

    Bloqueia ``javascript:``, ``data:``, URLs relativas ao protocolo (``//x``)
    e caracteres de controle usados para disfarçar esquemas.
    """
    if not url or not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate or _CONTROL_CHARS.search(candidate):
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return candidate.replace(" ", "%20")


def safe_href(url: str | None) -> str | None:
    """Como :func:`safe_url`, mas também aceita caminhos relativos (``../``, ``2026-09-29.html``)."""
    if not url or not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate or _CONTROL_CHARS.search(candidate):
        return None
    if _SCHEME.match(candidate) or candidate.startswith("//"):
        return safe_url(candidate)
    return candidate.replace(" ", "%20")


def parse_iso(value: str | None) -> datetime | None:
    """Converte ISO 8601 em ``datetime`` com fuso (sem fuso = UTC); inválido → ``None``."""
    if not value or not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


@lru_cache(maxsize=16)
def _zone(tz: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Fuso horário inválido %r; usando UTC", tz)
        return timezone.utc


def local_time(iso: str | None, tz: str, ref_iso: str | None = None) -> str:
    """Hora local ``"HH:MM"`` de um instante ISO no fuso ``tz``; vazio se inválido.

    Com ``ref_iso`` (o horário da edição), um instante de outro dia ganha a data:
    ``"ontem, 21:10"`` no dia anterior e ``"27/09, 18:30"`` antes disso — "09:00"
    sozinho numa edição de madrugada parece horário futuro.
    """
    moment = parse_iso(iso)
    if moment is None:
        return ""
    local = moment.astimezone(_zone(tz))
    clock = local.strftime("%H:%M")
    ref = parse_iso(ref_iso)
    if ref is None:
        return clock
    days = (ref.astimezone(_zone(tz)).date() - local.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"ontem, {clock}"
    return f"{local.day:02d}/{local.month:02d}, {clock}"


def tz_label(tz: str, iso: str | None = None) -> str:
    """Rótulo curto do fuso: ``"BRT"`` para São Paulo; senão abreviação/offset."""
    if tz in _TZ_LABELS:
        return _TZ_LABELS[tz]
    moment = parse_iso(iso) or datetime.now(timezone.utc)
    abbr = moment.astimezone(_zone(tz)).strftime("%Z")
    return f"UTC{abbr}" if abbr[:1] in "+-" else abbr


def rel_age(iso: str | None, now_iso: str | None) -> str:
    """Idade relativa em pt-BR: ``"há 25 min"``, ``"há 3 h"``, ``"ontem"``, ``"há 3 dias"``."""
    moment, now = parse_iso(iso), parse_iso(now_iso)
    if moment is None or now is None:
        return ""
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "agora"
    if minutes < 60:
        return f"há {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"há {hours} h"
    if hours < 48:
        return "ontem"
    return f"há {hours // 24} dias"


def round_half_up(value: float) -> int:
    """Arredondamento escolar (``round`` do Python arredonda 18,5 para 18)."""
    return math.floor(value + 0.5)


def temp(value: float | None) -> str:
    """Temperatura arredondada: ``18.6`` → ``"19°"``; ausente → ``"–"``."""
    if value is None or not isinstance(value, (int, float)) or math.isnan(value):
        return "–"
    return f"{round_half_up(value)}°"


def change_class(pct: float | None) -> str:
    """Classe CSS da variação: ``u`` (alta), ``d`` (queda), ``f`` (estável) ou ``""`` (indicador)."""
    if pct is None:
        return ""
    rounded = round(pct, 2)
    return "u" if rounded > 0 else "d" if rounded < 0 else "f"


def safe_color(value: str | None, default: str) -> str:
    """Aceita só cores hexadecimais (``#abc``, ``#aabbcc``, ``#aabbccdd``)."""
    if isinstance(value, str) and _HEX_COLOR.match(value.strip()):
        return value.strip()
    return default


def rgb_triplet(color: str) -> str:
    """``"#0A2051"`` → ``"10,32,81"`` (para ``rgba(var(--x-rgb), .5)`` no CSS)."""
    digits = color.lstrip("#")
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits[:3])
    digits = digits[:6]
    return ",".join(str(int(digits[i : i + 2], 16)) for i in (0, 2, 4))


def clean_svg(svg: str | None) -> Markup | None:
    """SVG da marca (arquivo do repositório) pronto para ser embutido no HTML."""
    if not svg or "<svg" not in svg:
        return None
    return Markup(_XML_PROLOG.sub("", svg).strip())


def svg_data_uri(svg: str) -> str:
    """``data:image/svg+xml,...`` com o SVG codificado (favicon)."""
    return "data:image/svg+xml," + quote(_XML_PROLOG.sub("", svg).strip(), safe=" /:=;,'()-._~")


def initial_favicon(initial: str, background: str, foreground: str) -> str:
    """SVG simples com a inicial da marca, usado quando não há favicon próprio."""
    letter = xml_escape((initial.strip() or "Q")[0].upper())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="12" fill="{background}"/>'
        '<text x="32" y="45" font-family="Arial,Helvetica,sans-serif" font-size="40" '
        f'font-weight="700" text-anchor="middle" fill="{foreground}">{letter}</text></svg>'
    )


@lru_cache(maxsize=1)
def environment() -> Environment:
    """Ambiente Jinja2 compartilhado (autoescape sempre ligado)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    env.filters.update(
        md_lite=md_lite,
        plain=plain,
        safe_url=safe_url,
        local_time=local_time,
        rel_age=rel_age,
    )
    return env
