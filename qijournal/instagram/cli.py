"""Comandos ``python -m qijournal instagram gerar|publicar``."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from qijournal import pipeline
from qijournal.config import Config
from qijournal.models import Edition

from .fila import Fila
from .graph import DEFAULT_HOST, DEFAULT_VERSION, GraphError, InstagramClient
from .publicar import TokenInvalido, publish_due
from .token import maybe_refresh, write_token_file

log = logging.getLogger(__name__)

EXIT_OK, EXIT_ERROR = 0, 1
MEDIA_BRANCH = "instagram"
DEFAULT_MEDIA_DIR = "instagram-midia"


def media_url(config: Config, env: Mapping[str, str]) -> str:
    """URL pública da pasta de mídia: a branch instagram em raw.githubusercontent.com."""
    url = (env.get("IG_MEDIA_URL") or "").strip()
    if not url:
        repo = config.site.repo_url.rstrip("/").removeprefix("https://github.com/")
        url = f"https://raw.githubusercontent.com/{repo}/{MEDIA_BRANCH}/"
    return url if url.endswith("/") else url + "/"


def client_from_env(env: Mapping[str, str]) -> InstagramClient | None:
    """Cliente da API com os segredos IG_USER_ID e IG_ACCESS_TOKEN (None sem eles)."""
    user_id = (env.get("IG_USER_ID") or "").strip()
    token = (env.get("IG_ACCESS_TOKEN") or "").strip()
    if not user_id or not token:
        return None
    return InstagramClient(
        user_id,
        token,
        host=(env.get("IG_API_HOST") or DEFAULT_HOST).strip(),
        version=(env.get("IG_API_VERSION") or DEFAULT_VERSION).strip(),
    )


def latest_edition(root: Path) -> Edition:
    """Edição mais recente publicada em data/ (a do manifesto edicoes/latest.json)."""
    manifest = pipeline.previous_manifest(root)
    day = manifest.get("date")
    if not day:
        raise FileNotFoundError(root / pipeline.ARCHIVE_DIR / "latest.json")
    path = root / pipeline.DATA_DIR / f"{day}.json"
    return Edition.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _write_alert(path: str | None, message: str) -> None:
    if path:
        Path(path).write_text(message + "\n", encoding="utf-8")


def cmd_gerar(args: argparse.Namespace, config: Config) -> int:
    from .gerar import generate  # importa o Playwright só aqui

    root = Path(args.dir)
    media_dir = Path(args.media_dir)
    edition = latest_edition(root)
    now = args.now or datetime.now(timezone.utc)
    fila = Fila.load(media_dir)
    posts = generate(edition, config, root=root, media_dir=media_dir, now=now)
    changed = fila.upsert(posts)
    fila.prune(pipeline.local_date(now, config.site.timezone))
    fila.save()
    log.info("Instagram: %d publicação(ões) do dia %s na fila (%d nova(s) ou refeita(s))", len(posts), edition.date, len(changed))
    return EXIT_OK


def cmd_publicar(args: argparse.Namespace, config: Config, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    client = client_from_env(env)
    if client is None:
        log.info("Instagram não configurado (segredos IG_USER_ID e IG_ACCESS_TOKEN); nada a publicar")
        return EXIT_OK
    now = args.now or datetime.now(timezone.utc)
    fila = Fila.load(Path(args.media_dir))
    try:
        status = maybe_refresh(client, fila, now, current_token=env["IG_ACCESS_TOKEN"].strip())
        if status.new_token and args.token_out:
            write_token_file(status.new_token, Path(args.token_out))
        fila.save()
        if status.warning:
            _write_alert(args.alert_out, f"Atenção: {status.warning}.")
        resultado = publish_due(fila, client, media_url=media_url(config, env), now=now, save=fila.save)
    except (TokenInvalido, GraphError) as exc:
        fila.save()
        if isinstance(exc, TokenInvalido) or getattr(exc, "token_invalid", False):
            log.error("Instagram: o token não vale mais (%s)", exc)
            _write_alert(
                args.alert_out,
                "O token do Instagram expirou ou foi revogado; nenhuma publicação sai até ele ser trocado. "
                f"Gere um novo e atualize o segredo IG_ACCESS_TOKEN. Erro: {exc}",
            )
        else:
            log.error("Instagram: erro na API (%s)", exc)
        return EXIT_ERROR
    fila.prune(pipeline.local_date(now, config.site.timezone))
    fila.save()
    for post in resultado.publicados:
        log.info("Instagram: publicado %s → %s", post.id, post.permalink or post.media_id)
    if resultado.aguardando:
        log.info("Instagram: %d publicação(ões) esperando o GitHub Pages; saem na próxima execução", len(resultado.aguardando))
    return EXIT_ERROR if resultado.falhas else EXIT_OK


def add_parser(commands: argparse._SubParsersAction, common: argparse.ArgumentParser, iso_datetime) -> None:
    insta = commands.add_parser("instagram", help="posts do Instagram a partir da edição do dia")
    sub = insta.add_subparsers(dest="instagram_command", required=True, metavar="ação")

    gerar = sub.add_parser("gerar", parents=[common], help="gera as imagens e legendas do dia e põe na fila")
    gerar.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    gerar.add_argument("--media-dir", default=DEFAULT_MEDIA_DIR, metavar="DIR", help="cópia da branch instagram (fila e imagens)")
    gerar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    gerar.set_defaults(handler=cmd_gerar)

    publicar = sub.add_parser("publicar", parents=[common], help="publica o que já venceu na fila")
    publicar.add_argument("--media-dir", default=DEFAULT_MEDIA_DIR, metavar="DIR", help="cópia da branch instagram (fila e imagens)")
    publicar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    publicar.add_argument("--token-out", metavar="FILE", help="onde gravar o token renovado (para o segredo)")
    publicar.add_argument("--alert-out", metavar="FILE", help="onde gravar um alerta para o dono (token)")
    publicar.set_defaults(handler=cmd_publicar)
