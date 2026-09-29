"""Utilitários de texto compartilhados (sem dependências externas)."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import date, datetime
from html.parser import HTMLParser

WEEKDAYS_PT = [
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
]
MONTHS_PT = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

STOPWORDS = set(
    """
    a o as os um uma uns umas de da do das dos em na no nas nos por para pra com sem sob sobre
    e ou mas que se ao aos à às pelo pela pelos pelas este esta isto esse essa isso aquele aquela
    seu sua seus suas ele ela eles elas diz dizem apos após ate até mais menos muito já ja
    ser foi são sao tem têm vai vão como quando onde porque entre contra desde também tambem
    the a an of to in on for and or but with from by at as is are was were be been it its this
    that these those after over into about amid says said new will not than more
    """.split()
)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(s: str) -> str:
    """Minúsculas, sem acentos, só letras/dígitos e espaços simples."""
    s = strip_accents(s or "").lower()
    s = re.sub(r"[^a-z0-9&]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> set[str]:
    """Conjunto de palavras significativas (sem stopwords, >= 3 letras)."""
    return {t for t in normalize(s).split() if len(t) >= 3 and t not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    """Jaccard entre os tokens de dois textos (0..1)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "section", "article"}
    SKIP = {"script", "style", "noscript", "iframe", "svg", "figure", "figcaption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    """Converte HTML (ou texto com entidades) em texto puro, preservando quebras de parágrafo."""
    if not value:
        return ""
    if "<" in value and ">" in value:
        parser = _TextExtractor()
        try:
            parser.feed(value)
            parser.close()
            value = "".join(parser.parts)
        except Exception:  # HTML muito quebrado: remove tags na força bruta
            value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in value.split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    return text.strip()


def truncate(text: str, max_chars: int) -> str:
    """Corta em limite de palavra e acrescenta reticências."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    space = cut.rfind(" ")
    if space > max_chars * 0.6:
        cut = cut[:space]
    else:
        cut = cut[:max_chars]
    return cut.rstrip(" ,;:.-–—") + "…"


# Fim de frase: pontuação final seguida de espaço e de maiúscula (ou aspas/parêntese + maiúscula).
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"“'(]?[A-ZÁÉÍÓÚÂÊÔÃÕÇ])")
# Trechos que terminam em abreviação não encerram a frase: siglas com pontos
# (R.A.F., U.S., S.A.), iniciais (John F. Kennedy) e tratamentos (Sr., Dr., Inc.).
_ABBREV_END = re.compile(
    r"(?:\b(?:[A-Za-z]\.){2,}|\b[A-Z]\.|\b(?:Sr|Sra|Srs|Srta|Dr|Dra|Drs|Jr|St|Mr|Mrs|Ms|Prof|Profa|Gov|Sen"
    r"|Rep|Gen|Inc|Corp|Co|Ltd|Av|No|vs|Exmo|Exma)\.)$"
)


def split_sentences(value: str) -> list[str]:
    """Divide o texto em frases sem cortar em abreviações ("R.A.F.", "U.S.", "Dr.", "S.A.").

    Custo aceito: uma sigla com pontos no fim de uma frase ("in the U.S. The
    market") junta as duas frases em vez de truncar uma delas.
    """
    parts = [s.strip() for s in _SENTENCE_END.split(re.sub(r"\s+", " ", value or "").strip()) if s.strip()]
    out: list[str] = []
    for part in parts:
        if out and _ABBREV_END.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


# Avisos de paywall, cadastro ou "presente" dos sites, casados em texto normalizado
# (minúsculas, sem acento e sem pontuação; ver :func:`normalize`). Evita termos
# genéricos ("assine o decreto", "subscribe to the IPO", "assinantes da operadora").
PAYWALL_RE = re.compile(
    r"exclusiv[oa]s? (?:para|de|a) assinantes|restrit[oa] (?:a|para) assinantes|para continuar lendo"
    r"|ja e assinante|^assine\b|\bassine (?:ja|agora)\b|\bassine para (?:ler|continuar|ter acesso)"
    r"|faca (?:seu |o )?login para|subscribe (?:now|to (?:continue|read|unlock))|subscribers? only"
    r"|to continue reading|sign in to (?:read|continue)|already a subscriber|log in to continue"
    r"|para ter acesso completo|faca (?:o )?seu cadastro|acessos? por dia para (?:dar de presente|conteudos)"
    r"|dar de presente|assinantes podem liberar|qualquer pessoa que nao e assinante"
    r"|reportagem foi antecipada a assinantes"
)


def is_paywall(value: str) -> bool:
    """O trecho é um aviso de paywall/cadastro (e não notícia)?"""
    return bool(PAYWALL_RE.search(normalize(value)))


def strip_paywall(value: str) -> str:
    """Remove as frases de paywall/cadastro de um texto, preservando as quebras de linha."""
    if not value:
        return ""
    lines = []
    for line in value.split("\n"):
        if not is_paywall(line):
            lines.append(line)
            continue
        kept = [s for s in split_sentences(line) if not is_paywall(s)]
        if kept:
            lines.append(" ".join(kept))
    return "\n".join(lines).strip()


def first_sentences(text: str, max_chars: int = 260) -> str:
    """Primeiras frases completas que cabem em ``max_chars`` (ou truncado)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    out = ""
    for sentence in split_sentences(text):
        candidate = (out + " " + sentence).strip()
        if len(candidate) > max_chars:
            break
        out = candidate
    return out or truncate(text, max_chars)


_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")


def strip_markdown(s: str) -> str:
    """Remove marcação markdown comum (negrito, itálico, títulos, links, crases)."""
    s = s or ""
    s = _MD_LINK.sub(r"\1", s)
    s = re.sub(r"(\*\*|__)", "", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)
    s = s.replace("`", "")
    s = s.replace("*", "")
    return re.sub(r"\s+", " ", s).strip()


def slugify(text: str, max_len: int = 60) -> str:
    s = normalize(text).replace("&", " ")
    s = re.sub(r"\s+", "-", s.strip())
    s = s[:max_len].strip("-")
    return s or "materia"


def short_hash(value: str, n: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:n]


def pt_date_label(d: date | datetime) -> str:
    """``date(2026, 9, 29)`` → ``"Terça-feira, 29 de setembro de 2026"``."""
    return f"{WEEKDAYS_PT[d.weekday()]}, {d.day} de {MONTHS_PT[d.month - 1]} de {d.year}"


def pt_short_date(d: date | datetime) -> str:
    """``date(2026, 9, 29)`` → ``"29/09/2026"``."""
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def format_number_pt(value: float, decimals: int = 2) -> str:
    """Formata no padrão brasileiro: 1234567.891 → ``"1.234.567,89"``."""
    s = f"{abs(value):,.{decimals}f}"  # 1,234,567.89
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return ("-" if value < 0 and float(s.replace(".", "").replace(",", ".")) != 0 else "") + s
