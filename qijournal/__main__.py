"""Linha de comando do QI Journal: ``python -m qijournal <comando>``.

Comandos:

- ``run``: coleta, edita, publica e (se o SMTP estiver configurado) envia o e-mail.
- ``collect``: só coleta e salva a coleta crua (bundle) em JSON.
- ``render``: gera a edição a partir de um bundle salvo (offline, sem e-mail).
- ``send-email``: envia por SMTP o e-mail da última edição publicada.
- ``check-sources``: baixa cada feed de ``config/sources.yaml`` e mostra o estado.

Códigos de saída: 0 = sucesso; 1 = erro; 2 = dados insuficientes (a edição
não foi publicada e a anterior continua no ar) ou argumentos inválidos.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from qijournal import net, pipeline
from qijournal.collect.feeds import collect_feeds
from qijournal.config import Config, load_config
from qijournal.deliver.smtp import EmailDeliveryError
from qijournal.net import Fetcher

# Nome fixo: executado com ``-m``, ``__name__`` seria "__main__".
log = logging.getLogger("qijournal")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INSUFFICIENT = 2
# Janela ampla no check-sources: queremos ver também feeds parados há dias.
CHECK_WINDOW_HOURS = 24 * 365
# Bibliotecas que registram cada requisição HTTP em INFO (e o conteúdo em DEBUG).
NOISY_LOGGERS = ("httpx", "httpcore", "anthropic")


# ── check-sources ────────────────────────────────────────────────────────────


@dataclass
class FeedCheck:
    """Diagnóstico de um feed."""

    source_id: str
    url: str
    ok: bool
    items: int
    recent: int  # itens dentro de edition.max_age_hours
    newest_age_hours: float | None
    error: str | None = None


def check_sources(config: Config, *, now: datetime, fetch: Fetcher | None = None) -> list[FeedCheck]:
    """Baixa todos os feeds configurados e resume o estado de cada um."""
    now = pipeline.as_utc(now)
    articles, statuses = collect_feeds(
        config.sources,
        now=now,
        max_age_hours=CHECK_WINDOW_HOURS,
        global_exclude=config.edition.exclude_url_patterns,
        fetch=fetch or net.fetch,
    )
    published: dict[str, list[datetime]] = defaultdict(list)
    for article in articles:
        if article.published and article.feed_url:
            published[article.feed_url].append(pipeline.parse_iso_datetime(article.published))

    window = timedelta(hours=config.edition.max_age_hours)
    checks = []
    for status in statuses:
        dates = published.get(status.url, [])
        newest = max(dates, default=None)
        checks.append(
            FeedCheck(
                source_id=status.source_id,
                url=status.url,
                ok=status.ok,
                items=status.items,
                recent=sum(1 for d in dates if now - d <= window),
                newest_age_hours=(now - newest).total_seconds() / 3600 if newest else None,
                error=status.error,
            )
        )
    return checks


def _format_age(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 1:
        return f"{max(0, round(hours * 60))} min"
    if hours < 48:
        return f"{round(hours)} h"
    return f"{round(hours / 24)} d"


def format_source_table(checks: list[FeedCheck], *, max_age_hours: int) -> str:
    """Tabela em texto: id, estado, itens, itens recentes, idade do mais novo, URL."""
    header = ("fonte", "estado", "itens", f"≤{max_age_hours}h", "mais novo", "url")
    rows = [
        (
            c.source_id,
            "ok" if c.ok else f"ERRO: {c.error or 'desconhecido'}",
            str(c.items),
            str(c.recent),
            _format_age(c.newest_age_hours),
            c.url,
        )
        for c in checks
    ]
    # A última coluna (URL) não é alinhada; texto à esquerda, números à direita.
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header) - 1)]

    def line(cells: tuple[str, ...]) -> str:
        *aligned, url = cells
        padded = [
            cell.ljust(w) if i < 2 else cell.rjust(w) for i, (cell, w) in enumerate(zip(aligned, widths, strict=True))
        ]
        return "  ".join([*padded, url])

    ok = sum(1 for c in checks if c.ok)
    stale = sum(1 for c in checks if c.ok and c.recent == 0)
    footer = f"{ok}/{len(checks)} feeds ok; {stale} feed(s) ok sem itens nas últimas {max_age_hours} h"
    return "\n".join([line(header), *(line(r) for r in rows), "", footer])


# ── Comandos ─────────────────────────────────────────────────────────────────


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    result = pipeline.run(
        config,
        out_dir=Path(args.out),
        now=args.now,
        bundle_path=Path(args.bundle) if args.bundle else None,
        use_llm=not args.no_llm,
        send_email=not args.no_email,
        force_email=getattr(args, "force_email", False),
    )
    log.info("Capa: %s", result.outputs["index"])
    return EXIT_OK


def _cmd_collect(args: argparse.Namespace, config: Config) -> int:
    bundle = pipeline.collect_bundle(config, now=datetime.now(timezone.utc))
    path = pipeline.save_bundle(bundle, Path(args.out))
    if not bundle.articles:
        log.warning("Nenhum artigo coletado")
    log.info("Coleta salva em %s", path)
    return EXIT_OK


def _cmd_send_email(args: argparse.Namespace, config: Config) -> int:
    try:
        settings = pipeline.email_settings(config, os.environ)
    except ValueError as exc:
        log.error("Configuração SMTP inválida: %s", exc)
        return EXIT_ERROR
    if settings is None:
        log.error("SMTP não configurado: defina SMTP_USER, SMTP_PASSWORD e EMAIL_TO")
        return EXIT_ERROR
    try:
        pipeline.send_published_email(settings, out_dir=Path(args.dir), force=args.force_email)
    except pipeline.EmailAlreadySent as exc:
        log.warning("%s", exc)
        return EXIT_OK
    except FileNotFoundError as exc:
        log.error("Edição não encontrada (%s); gere-a antes com `python -m qijournal run`", exc.filename or exc)
        return EXIT_ERROR
    except json.JSONDecodeError as exc:
        log.error("edicoes/latest.json inválido: %s", exc)
        return EXIT_ERROR
    except EmailDeliveryError as exc:
        log.error("E-mail não enviado: %s", exc)
        return EXIT_ERROR
    log.info("E-mail enviado; edicoes/latest.json atualizado")
    return EXIT_OK


def _cmd_check_sources(args: argparse.Namespace, config: Config) -> int:
    checks = check_sources(config, now=datetime.now(timezone.utc))
    print(format_source_table(checks, max_age_hours=config.edition.max_age_hours))
    return EXIT_OK if any(c.ok for c in checks) else EXIT_ERROR


# ── Argumentos ───────────────────────────────────────────────────────────────


def _iso_datetime(value: str) -> datetime:
    try:
        return pipeline.parse_iso_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="log detalhado (DEBUG)")

    parser = argparse.ArgumentParser(
        prog="python -m qijournal",
        description="QI Journal — jornal financeiro diário gerado automaticamente.",
        epilog="Códigos de saída: 0 sucesso; 1 erro; 2 dados insuficientes ou argumentos inválidos.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="comando")

    run = commands.add_parser("run", parents=[common], help="gera e publica a edição do dia")
    run.add_argument("--out", default=".", metavar="DIR", help="pasta de saída (padrão: .)")
    run.add_argument("--bundle", metavar="FILE", help="usa uma coleta salva em vez de acessar a rede")
    run.add_argument("--no-llm", action="store_true", help="edição automática, sem IA")
    run.add_argument("--no-email", action="store_true", help="não envia o e-mail por SMTP")
    run.add_argument(
        "--force-email", action="store_true", help="reenvia o e-mail mesmo que o do dia já tenha saído"
    )
    run.add_argument("--now", type=_iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    run.set_defaults(handler=_cmd_run)

    collect = commands.add_parser("collect", parents=[common], help="só coleta e salva o bundle JSON")
    collect.add_argument("--out", required=True, metavar="FILE", help="arquivo JSON de saída")
    collect.set_defaults(handler=_cmd_collect)

    render = commands.add_parser("render", parents=[common], help="gera a edição a partir de um bundle (offline)")
    render.add_argument("--bundle", required=True, metavar="FILE", help="coleta salva (JSON)")
    render.add_argument("--out", required=True, metavar="DIR", help="pasta de saída")
    render.add_argument("--no-llm", action="store_true", help="edição automática, sem IA")
    render.add_argument("--now", type=_iso_datetime, metavar="ISO", help="padrão: horário da coleta")
    render.set_defaults(handler=_cmd_run, no_email=True)  # render nunca envia e-mail

    send = commands.add_parser("send-email", parents=[common], help="envia por SMTP o e-mail da última edição")
    send.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    send.add_argument(
        "--force-email", action="store_true", help="reenvia mesmo que latest.json registre o envio"
    )
    send.set_defaults(handler=_cmd_send_email)

    check = commands.add_parser("check-sources", parents=[common], help="testa cada feed de config/sources.yaml")
    check.set_defaults(handler=_cmd_check_sources)
    return parser


def setup_logging(verbose: bool) -> None:
    """Log no stderr no formato do projeto; INFO por padrão, DEBUG com ``-v``."""
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    root.setLevel(level)
    # Mesmo com -v, nada de DEBUG do SDK/HTTP: despejaria prompts inteiros no
    # log do Actions (público em repositório público).
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO if verbose else logging.WARNING)


def main(argv: list[str] | None = None, *, config_loader: Callable[[], Config] = load_config) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return args.handler(args, config_loader())
    except pipeline.InsufficientData:
        return EXIT_INSUFFICIENT  # já registrado pelo pipeline
    except pipeline.BundleError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.error("Interrompido pelo usuário")
        return EXIT_ERROR
    except Exception:
        log.exception("Erro inesperado no comando %r", args.command)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
