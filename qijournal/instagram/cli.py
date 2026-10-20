"""Comandos ``python -m qijournal instagram gerar|publicar``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
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
SYNC_SCRIPT = Path(".github") / "scripts" / "instagram-branch.sh"
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
    from .gerar import clean_media, generate  # importa o Playwright só aqui

    root = Path(args.dir)
    media_dir = Path(args.media_dir)
    edition = latest_edition(root)
    now = args.now or datetime.now(timezone.utc)
    today = pipeline.local_date(now, config.site.timezone)
    if edition.date != today.isoformat() and not args.force:
        log.warning("Instagram: a última edição é de %s, não de hoje (%s); nada a gerar", edition.date, today)
        return EXIT_OK
    fila = Fila.load(media_dir)
    posts = generate(edition, config, root=root, media_dir=media_dir, now=now)
    changed = fila.upsert(posts)
    fila.prune(today)
    removed = clean_media(fila, media_dir, today, config.instagram.keep_days)
    fila.save()
    log.info(
        "Instagram: %d publicação(ões) do dia %s na fila (%d nova(s) ou refeita(s)); %d arquivo(s) antigo(s) removido(s)",
        len(posts), edition.date, len(changed), len(removed),
    )
    return EXIT_OK


def _syncer(fila: Fila, media_dir: Path, enabled: bool):
    """Grava a fila no disco e, com ``--sync``, envia a branch instagram na hora."""

    def save(what: str) -> None:
        fila.save()
        if not enabled:
            return
        env = {**os.environ, "MEDIA_DIR": str(media_dir)}
        done = subprocess.run([str(SYNC_SCRIPT), f"📸 Instagram: {what}"], env=env, check=False)
        if done.returncode != 0:
            log.warning("Instagram: não deu para enviar a fila à branch instagram agora (%s)", what)

    return save


def cmd_publicar(args: argparse.Namespace, config: Config, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    if (env.get("IG_PAUSE") or "").strip().lower() in {"1", "true", "sim", "yes"}:
        log.warning("Instagram pausado pela variável IG_PAUSE; nada é publicado")
        return EXIT_OK
    client = client_from_env(env)
    if client is None:
        log.info("Instagram não configurado (segredos IG_USER_ID e IG_ACCESS_TOKEN); nada a publicar")
        return EXIT_OK
    media_dir = Path(args.media_dir)
    fila = Fila.load(media_dir)
    save = _syncer(fila, media_dir, args.sync)
    now = args.now or datetime.now(timezone.utc)
    deadline = now + timedelta(minutes=max(0, args.wait_max))
    published = 0
    try:
        account = client.account()
        log.info("Instagram: conectado como @%s", account.get("username") or account.get("id"))
        status = maybe_refresh(client, fila, now, current_token=env["IG_ACCESS_TOKEN"].strip())
        if status.new_token and args.token_out:
            write_token_file(status.new_token, Path(args.token_out))
        if status.refreshed:
            save("token renovado")
        if status.warning:
            _write_alert(args.alert_out, f"Atenção: {status.warning}. A renovação automática não está funcionando; veja os passos 6 a 8 do guia (docs/instagram.md).")
        while True:
            resultado = publish_due(fila, client, media_url=media_url(config, env), now=now, save=save)
            published += len(resultado.publicados)
            failed = bool(resultado.falhas)
            # Logo depois da edição: espera o horário-alvo das próximas publicações
            # (minutos do Actions são grátis em repositório público) em vez de
            # depender do agendamento, que atrasa horas.
            upcoming = fila.next_due(now)
            if args.now is not None or upcoming is None or upcoming > deadline or resultado.limite:
                break
            wait = (upcoming - now).total_seconds() + 5
            log.info("Instagram: esperando %d min pela próxima publicação (%s)", wait // 60, upcoming.astimezone().strftime("%H:%M"))
            time.sleep(wait)
            now = datetime.now(timezone.utc)
    except (TokenInvalido, GraphError) as exc:
        fila.save()
        if isinstance(exc, TokenInvalido) or getattr(exc, "token_invalid", False):
            log.error("Instagram: o token não vale mais (%s)", exc)
            _write_alert(
                args.alert_out,
                "O token do Instagram expirou ou foi revogado: nenhuma publicação sai até ele ser trocado. "
                "Gere um novo token (passo 6 do guia docs/instagram.md) e atualize o segredo IG_ACCESS_TOKEN "
                f"(passo 8). Erro: {exc}",
            )
        else:
            log.error("Instagram: erro na API (%s)", exc)
        return EXIT_ERROR
    fila.prune(pipeline.local_date(now, config.site.timezone))
    save("fila em dia")
    log.info("Instagram: %d publicação(ões) nesta execução", published)
    return EXIT_ERROR if failed else EXIT_OK


def add_parser(commands: argparse._SubParsersAction, common: argparse.ArgumentParser, iso_datetime) -> None:
    insta = commands.add_parser("instagram", help="posts do Instagram a partir da edição do dia")
    sub = insta.add_subparsers(dest="instagram_command", required=True, metavar="ação")

    gerar = sub.add_parser("gerar", parents=[common], help="gera as imagens e legendas do dia e põe na fila")
    gerar.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    gerar.add_argument("--media-dir", default=DEFAULT_MEDIA_DIR, metavar="DIR", help="cópia da branch instagram (fila e imagens)")
    gerar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    gerar.add_argument("--force", action="store_true", help="gera mesmo que a última edição não seja de hoje")
    gerar.set_defaults(handler=cmd_gerar)

    publicar = sub.add_parser("publicar", parents=[common], help="publica o que já venceu na fila")
    publicar.add_argument("--media-dir", default=DEFAULT_MEDIA_DIR, metavar="DIR", help="cópia da branch instagram (fila e imagens)")
    publicar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    publicar.add_argument("--sync", action="store_true", help="envia a fila à branch instagram a cada mudança")
    publicar.add_argument("--wait-max", type=int, default=0, metavar="MIN", help="espera até MIN minutos pelas próximas publicações")
    publicar.add_argument("--token-out", metavar="FILE", help="onde gravar o token renovado (para o segredo)")
    publicar.add_argument("--alert-out", metavar="FILE", help="onde gravar um alerta para o dono (token)")
    publicar.set_defaults(handler=cmd_publicar)
