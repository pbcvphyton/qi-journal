"""Utilitários compartilhados pelos testes de renderização (tests/test_render_*.py)."""

from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from qijournal.config import Config, load_config
from qijournal.models import Edition

FIXTURE = Path(__file__).with_name("edition.json")
MALICIOUS_ID = "teste-de-seguranca-script"
LEAD_ID = "arrecadacao-federal-bate-recorde-em-agosto"

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def load_edition() -> Edition:
    """Edição realista (7 seções, 24 matérias, incluindo uma maliciosa)."""
    return Edition.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))


def default_config() -> Config:
    """Configuração do repositório com a marca padrão, ignorando o ambiente do processo."""
    return load_config(env={})


def pbcv_config() -> Config:
    return load_config(env={"QIJ_BRAND": "pbcv"})


class PageStructure(HTMLParser):
    """Coleta a estrutura de um HTML: equilíbrio de tags, ids, títulos, links e imagens."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.ids: Counter[str] = Counter()
        self.headings: list[tuple[str, str]] = []
        self.hrefs: list[str] = []
        self.srcs: list[str] = []
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self._heading: list[str] | None = None
        self._heading_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        if tag in HEADINGS and self._heading is None:
            self._heading = [tag]
            self._heading_depth = len(self.stack)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> inesperado (pilha: {self.stack[-3:]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()
        if self._heading is not None and tag in HEADINGS and len(self.stack) == self._heading_depth - 1:
            level, *parts = self._heading
            self.headings.append((level, " ".join("".join(parts).split())))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags.append((tag, values))
        if values.get("id"):
            self.ids[values["id"]] += 1
        if tag in ("a", "link") and values.get("href") is not None:
            self.hrefs.append(values["href"] or "")
        if values.get("src") is not None:
            self.srcs.append(values["src"] or "")


def parse(html: str) -> PageStructure:
    page = PageStructure()
    page.feed(html)
    page.close()
    return page


def visible_text(html: str) -> str:
    """Texto sem <script>/<style> (para buscar vazamentos de marcação no conteúdo)."""
    return re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S | re.I)
