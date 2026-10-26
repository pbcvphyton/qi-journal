"""Gera as publicações do dia (imagens JPEG + legendas) e devolve os itens da fila."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from qijournal.config import Config, InstagramSlot
from qijournal.models import Edition
from qijournal.render.filters import clean_svg, md_lite, plain

from . import conteudo
from .fila import Fila, Post
from .render import Renderer, fonts_css

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SIZES = {"carousel": (1080, 1350), "image": (1080, 1350), "story": (1080, 1920)}
STORY_STEP = timedelta(minutes=1)  # stories em sequência: um minuto de diferença no horário-alvo
LAUNCH_ID = "lancamento-comece-aqui"  # sai uma vez só, antes de tudo, quando a conta for conectada
LAUNCH_DAYS = 3  # cada dia renova o post (números do dia) e o prazo, até ele sair


def environment() -> Environment:
    # QIJ_IG_TEMPLATES: outra pasta de templates (para testar um visual novo sem mexer nestes).
    templates = os.environ.get("QIJ_IG_TEMPLATES") or str(TEMPLATES_DIR)
    env = Environment(
        loader=FileSystemLoader(templates),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    env.filters.update(md_lite=md_lite, plain=plain)
    return env


def brand_context(config: Config) -> dict:
    root = config.root
    logo = (root / "assets" / "triangula-marca-cabecalho.svg").read_text(encoding="utf-8")
    icon = (root / "assets" / "triangula-favicon.svg").read_text(encoding="utf-8")
    return {
        "name": config.brand.name,
        "tagline": config.brand.tagline,
        "colors": config.brand.colors,
        "logo": clean_svg(logo),
        "icon": clean_svg(icon),
        "fonts_css": Markup(fonts_css(root)),
    }


def election_mode(config: Config, day: date) -> bool:
    until = config.instagram.election_until
    return bool(until) and day <= date.fromisoformat(str(until))


def slot_window(slot: InstagramSlot, day: date, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """(horário-alvo, prazo) do slot no dia; None se não sai nesse dia da semana."""
    hhmm = slot.weekends if day.weekday() >= 5 else slot.weekdays
    if not hhmm:
        return None
    due = datetime.combine(day, time.fromisoformat(hhmm), tz)
    expires = datetime.combine(day, time.fromisoformat(slot.expires), tz)
    return due, max(expires, due + timedelta(minutes=30))


def _render_piece(
    renderer: Renderer,
    env: Environment,
    piece: conteudo.Peca,
    brand: dict,
    out_dir: Path,
    rel_dir: str,
) -> list[str]:
    """Renderiza os slides de ``piece`` e devolve os caminhos relativos (com hash do conteúdo)."""
    width, height = SIZES[piece.kind]
    paths: list[str] = []
    total = len(piece.slides)
    for i, slide in enumerate(piece.slides, 1):
        html = env.get_template(f"{slide.template}.html.j2").render(
            brand=brand, s=conteudo.polish(slide.ctx), page=i, pages=total, kind=piece.kind, slot=piece.slot
        )
        tmp = out_dir / f".{piece.slot}-{i:02d}.jpg"
        renderer.render(html, tmp, width=width, height=height)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:10]
        final = out_dir / f"{piece.slot}-{i:02d}-{digest}.jpg"
        tmp.replace(final)
        paths.append(f"{rel_dir}/{final.name}")
    return paths


def build_pieces(edition: Edition, config: Config, brand: dict) -> list[conteudo.Peca]:
    """As publicações do dia, na ordem: manhã, stories, Raio-X."""
    day = date.fromisoformat(edition.date)
    election = election_mode(config, day)
    pieces: list[conteudo.Peca] = []
    manha = conteudo.em_1_minuto(edition, brand, election=election) or conteudo.terminal(edition, brand)
    if manha:
        pieces.append(manha)
    stories = conteudo.stories_manha(edition, brand, election=election)
    if stories:
        pieces.append(stories)
    raiox = conteudo.raio_x(edition, brand, election=election)
    if raiox:
        pieces.append(raiox)
    return pieces


def generate(
    edition: Edition,
    config: Config,
    *,
    root: Path,
    media_dir: Path,
    now: datetime,
    renderer_factory: Callable[[], Renderer] = Renderer,
    launch: bool = True,
) -> list[Post]:
    """Renderiza as publicações do dia em ``media_dir/<data>/`` e devolve os itens da fila.

    Slots cujo prazo já passou não são gerados (a edição saiu tarde demais).
    Com ``launch``, inclui o "Comece aqui" (a fila só o aceita enquanto não saiu).
    """
    if not config.instagram.enabled:
        log.info("Instagram desligado em config/site.yaml")
        return []
    tz = ZoneInfo(config.site.timezone)
    day = date.fromisoformat(edition.date)
    brand = brand_context(config)
    env = environment()
    pieces = build_pieces(edition, config, brand)
    rel_dir = edition.date
    out_dir = Path(media_dir) / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    posts: list[Post] = []
    with renderer_factory() as renderer:
        for piece in pieces:
            slot = config.instagram.slots.get(piece.slot)
            window = slot_window(slot, day, tz) if slot else None
            if window is None:
                log.info("Instagram: %s não sai neste dia da semana", piece.title)
                continue
            due, expires = window
            if now >= expires:
                log.warning("Instagram: %s não gerado — o prazo (%s) já passou", piece.title, expires.strftime("%H:%M"))
                continue
            images = _render_piece(renderer, env, piece, brand, out_dir, rel_dir)
            if piece.kind == "story":
                for n, (image, slide) in enumerate(zip(images, piece.slides), 1):
                    posts.append(
                        Post(
                            id=f"{edition.date}-{piece.slot}-{n}",
                            date=edition.date,
                            kind="story",
                            due=(due + STORY_STEP * (n - 1)).isoformat(),
                            expires=expires.isoformat(),
                            images=[image],
                            title=f"{piece.title} {n}/{len(images)}",
                        )
                    )
            else:
                posts.append(
                    Post(
                        id=f"{edition.date}-{piece.slot}",
                        date=edition.date,
                        kind=piece.kind,
                        due=due.isoformat(),
                        expires=expires.isoformat(),
                        images=images,
                        caption=piece.caption,
                        alt_texts=[conteudo.alt(s.alt) for s in piece.slides],
                        title=piece.title,
                    )
                )
            log.info("Instagram: %s pronto (%d imagem(ns), sai a partir de %s)", piece.title, len(images), due.strftime("%d/%m %H:%M"))
        if launch and edition.mode == "ai":
            piece = conteudo.comece_aqui(edition, brand)
            images = _render_piece(renderer, env, piece, brand, out_dir, rel_dir)
            start = datetime.combine(day, time(0, 0), tz)
            posts.insert(
                0,
                Post(
                    id=LAUNCH_ID,
                    date=edition.date,
                    kind="carousel",
                    due=start.isoformat(),
                    expires=(start + timedelta(days=LAUNCH_DAYS)).isoformat(),
                    images=images,
                    caption=piece.caption,
                    alt_texts=[conteudo.alt(s.alt) for s in piece.slides],
                    title=piece.title,
                    permanent=True,
                ),
            )
    return posts


def clean_media(fila: Fila, media_dir: Path, today: date, keep_days: int) -> list[Path]:
    """Apaga pastas de dias antigos e imagens que nenhuma publicação da fila usa mais."""
    media_dir = Path(media_dir)
    removed: list[Path] = []
    referenced = {img for p in fila.posts for img in p.images}
    cutoff = today - timedelta(days=keep_days)
    for folder in sorted(p for p in media_dir.iterdir() if p.is_dir()) if media_dir.is_dir() else []:
        try:
            folder_day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if folder_day < cutoff:
            shutil.rmtree(folder)
            removed.append(folder)
            continue
        for image in folder.glob("*.jpg"):
            if f"{folder.name}/{image.name}" not in referenced:
                image.unlink()
                removed.append(image)
    return removed
