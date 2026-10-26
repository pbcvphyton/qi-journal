"""HTML → JPEG com o Chromium do Playwright (fontes da marca em assets/fonts).

Os templates marcam com ``data-fit`` os textos de tamanho variável: antes da
captura, :data:`FIT_JS` reduz a fonte de cada um, de 2 em 2 px, até caber na
caixa (ou até ``data-min``); o que ainda assim não couber é cortado com
reticências e informado de volta, para o gerador registrar no log.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FONTS_DIR = Path("assets") / "fonts"
JPEG_QUALITY = 92

# Família CSS de cada arquivo em assets/fonts (prefixo do nome do arquivo).
FONT_FAMILIES = {
    "noto-serif": "Noto Serif",
    "noto-sans": "Noto Sans",
    "ibm-plex-mono": "IBM Plex Mono",
    "newsreader": "Newsreader",
    "archivo": "Archivo",  # ganchos e números de capa, na largura extracondensada (font-stretch: 62%)
}
# Fontes variáveis com eixo de largura: a faixa que o arquivo cobre.
FONT_STRETCH = {"archivo": "62% 125%"}
# unicode-range dos recortes do Google Fonts (latin e latin-ext).
UNICODE_RANGES = {
    "latin": "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, "
    "U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    "latin-ext": "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, "
    "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF",
}

FIT_JS = """
() => {
  const report = [];
  for (const el of document.querySelectorAll('[data-fit]')) {
    const min = parseFloat(el.dataset.min || '20');
    let size = parseFloat(getComputedStyle(el).fontSize);
    const over = () => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
    while (over() && size > min) {
      size = Math.max(min, size - 2);
      el.style.fontSize = size + 'px';
    }
    if (over()) {
      el.classList.add('fit-clamp');
      report.push({fit: el.dataset.fit, size});
    }
  }
  return report;
}
"""


def fonts_css(root: Path) -> str:
    """@font-face de todas as fontes de assets/fonts, com URLs file://."""
    rules: list[str] = []
    for path in sorted((Path(root) / FONTS_DIR).glob("*.woff2")):
        # nome: <familia>-<estilo>-<peso(s)>-<recorte>.woff2
        stem = path.stem
        prefix = next((p for p in FONT_FAMILIES if stem.startswith(p + "-")), None)
        if prefix is None:
            continue
        parts = stem[len(prefix) + 1 :].split("-")  # ex.: normal-100-900-latin, normal-400-latin-ext
        style, subset = parts[0], "latin-ext" if parts[-1] == "ext" else "latin"
        weight = " ".join(w for w in parts[1:] if w.isdigit()) or "400"
        family = FONT_FAMILIES[prefix]
        stretch = f"font-stretch:{FONT_STRETCH[prefix]};" if prefix in FONT_STRETCH else ""
        rules.append(
            f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};{stretch}font-display:block;"
            f"src:url('{path.resolve().as_uri()}') format('woff2');unicode-range:{UNICODE_RANGES[subset]};}}"
        )
    return "\n".join(rules)


@dataclass
class RenderResult:
    path: Path
    clamped: list[dict[str, Any]] = field(default_factory=list)  # textos cortados por não caberem


class Renderer:
    """Abre o Chromium uma vez e captura vários HTML. Use como gerenciador de contexto."""

    def __init__(self, *, quality: int = JPEG_QUALITY):
        self.quality = quality
        self._pw = None
        self._browser = None
        self._tmp = tempfile.TemporaryDirectory(prefix="triangula-ig-")

    def __enter__(self) -> "Renderer":
        from playwright.sync_api import sync_playwright  # dependência só do Instagram

        self._pw = sync_playwright().start()
        executable = os.environ.get("QIJ_CHROMIUM") or None
        try:
            self._browser = self._pw.chromium.launch(executable_path=executable)
        except Exception:
            if executable:
                raise
            # Sem o Chromium do Playwright baixado: tenta o Chrome do sistema (runners do GitHub têm).
            self._browser = self._pw.chromium.launch(channel="chrome")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._tmp.cleanup()

    def render(self, html: str, out: Path, *, width: int, height: int) -> RenderResult:
        """Captura ``html`` em ``out`` (JPEG, ``width`` × ``height``)."""
        assert self._browser is not None, "use Renderer como gerenciador de contexto"
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        page_file = Path(self._tmp.name) / (out.stem + ".html")
        page_file.write_text(html, encoding="utf-8")
        page = self._browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        try:
            page.goto(page_file.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready.then(() => true)")
            clamped = page.evaluate(FIT_JS)
            missing = page.evaluate(
                "() => [...document.fonts].filter(f => f.status === 'error').map(f => f.family)"
            )
            if missing:
                log.warning("Instagram: fonte(s) não carregada(s) em %s: %s", out.name, sorted(set(missing)))
            page.screenshot(
                path=str(out),
                type="jpeg",
                quality=self.quality,
                clip={"x": 0, "y": 0, "width": width, "height": height},
            )
        finally:
            page.close()
        if clamped:
            log.warning("Instagram: texto cortado em %s: %s", out.name, clamped)
        return RenderResult(path=out, clamped=list(clamped))
