"""Orquestração do jornal: coleta → edição → renderização → arquivos → e-mail.

Layout publicado (raiz do GitHub Pages)::

    index.html                 edição do dia (capa)
    edicoes/AAAA-MM-DD.html    cópia arquivada de cada edição
    edicoes/index.html         índice do arquivo
    edicoes/latest.json        manifesto da última edição (usado pela rotina de e-mail)
    edicoes/email.html|.txt    e-mail da última edição
    data/AAAA-MM-DD.json       edição em JSON (``Edition.to_dict``)
    build/bundle-AAAA-MM-DD.json   coleta crua (fora do git; artefato do Actions)

Todos os arquivos são gravados de forma atômica (arquivo temporário +
``os.replace``) e as páginas são renderizadas antes de qualquer escrita: um
erro no meio do caminho nunca deixa uma capa pela metade. Se a coleta não
atingir os mínimos da configuração, nada é escrito e a edição anterior continua
no ar.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar
from zoneinfo import ZoneInfo

from qijournal import net, text
from qijournal.collect.enrich import PageInfo, enrich
from qijournal.collect.feeds import collect_feeds, dedupe_articles
from qijournal.collect.market import collect_market
from qijournal.collect.weather import collect_weather
from qijournal.config import Config
from qijournal.deliver.smtp import EmailDeliveryError, SMTPSettings, smtp_settings_from_env
from qijournal.deliver.smtp import send_email as deliver_email
from qijournal.edit import make_edition
from qijournal.models import Article, Bundle, Edition
from qijournal.net import Fetcher
from qijournal.render.email import render_email
from qijournal.render.web import render_archive_index, render_edition_page

log = logging.getLogger(__name__)

ARCHIVE_DIR = "edicoes"
DATA_DIR = "data"
BUILD_DIR = "build"
EMAIL_CHANNEL_SMTP = "smtp"
# Fração de feeds com erro a partir da qual a execução emite um aviso.
FAILED_FEEDS_WARNING_RATIO = 0.25

_DATED_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(html|json)$")

T = TypeVar("T")
EnrichFn = Callable[[list[Article]], dict[str, PageInfo]]


class InsufficientData(Exception):
    """A coleta não atingiu os mínimos da configuração; a edição não é publicada."""

    def __init__(self, message: str, *, articles: int, sources_ok: int) -> None:
        super().__init__(message)
        self.articles = articles
        self.sources_ok = sources_ok


class BundleError(ValueError):
    """Arquivo de coleta (bundle) ausente, ilegível ou em formato inválido."""


@dataclass
class RunResult:
    edition: Edition
    outputs: dict[str, Path]
    email_sent: bool
    warnings: list[str] = field(default_factory=list)


# ── Datas ────────────────────────────────────────────────────────────────────


def as_utc(moment: datetime) -> datetime:
    """Converte para UTC; horário sem fuso é interpretado como UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_iso_datetime(value: str) -> datetime:
    """``"2026-09-29T08:07:00Z"`` / ``"...+00:00"`` / sem fuso (UTC) → ``datetime`` em UTC."""
    try:
        return as_utc(datetime.fromisoformat(value.strip()))
    except (AttributeError, ValueError):
        raise ValueError(f"data/hora ISO 8601 inválida: {value!r}") from None


def local_date(moment: datetime, tz: str) -> date:
    """Data civil de ``moment`` no fuso do site (a data da edição)."""
    return as_utc(moment).astimezone(ZoneInfo(tz)).date()


def _utc_iso(moment: datetime) -> str:
    return as_utc(moment).isoformat(timespec="seconds")


# ── Arquivos ─────────────────────────────────────────────────────────────────


def write_atomic(path: Path, content: str) -> Path:
    """Grava ``content`` (UTF-8) em ``path`` sem nunca expor um arquivo pela metade."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(0o644)  # mkstemp cria com 0600; páginas precisam ser legíveis
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def _write_json(path: Path, data: Any) -> Path:
    return write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_bundle(bundle: Bundle, path: Path) -> Path:
    """Salva a coleta crua em JSON (entrada reprodutível para ``render --bundle``)."""
    return _write_json(Path(path), bundle.to_dict())


def load_bundle(path: Path) -> Bundle:
    """Lê um bundle salvo por :func:`save_bundle`. Levanta :class:`BundleError` se inválido."""
    path = Path(path)
    try:
        return Bundle.from_dict(_read_json(path))
    except FileNotFoundError:
        raise BundleError(f"arquivo de coleta não encontrado: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"não foi possível ler {path}: {exc}") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise BundleError(f"{path} não é um bundle válido ({type(exc).__name__}: {exc})") from exc


# ── Coleta ───────────────────────────────────────────────────────────────────


def _optional(job: Future[list[T]], what: str) -> list[T]:
    """Resultado de uma coleta acessória (mercado/clima): falha vira lista vazia."""
    try:
        return job.result()
    except Exception:
        log.exception("Coleta de %s falhou; a edição sai sem esse bloco", what)
        return []


def collect_bundle(config: Config, *, now: datetime, fetch: Fetcher = net.fetch) -> Bundle:
    """Coleta feeds, cotações e clima em paralelo e deduplica os artigos.

    Mercado e clima são acessórios: uma falha inesperada neles é registrada e
    ignorada. Um erro inesperado na coleta de feeds (que já trata as falhas de
    cada feed) indica defeito e é propagado.
    """
    now = as_utc(now)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="coleta") as pool:
        feeds_job = pool.submit(
            collect_feeds,
            config.sources,
            now=now,
            max_age_hours=config.edition.max_age_hours,
            global_exclude=config.edition.exclude_url_patterns,
            fetch=fetch,
        )
        market_job = pool.submit(collect_market, config.market, fetch=fetch, now=now)
        weather_job = pool.submit(collect_weather, config.weather, fetch=fetch)
        articles, statuses = feeds_job.result()
        quotes = _optional(market_job, "cotações")
        weather = _optional(weather_job, "clima")

    unique = dedupe_articles(articles)
    ok = sum(1 for s in statuses if s.ok)
    log.info(
        "Coleta concluída: %d/%d feeds ok, %d artigos (%d após deduplicação), %d cotações, %d cidades",
        ok,
        len(statuses),
        len(articles),
        len(unique),
        len(quotes),
        len(weather),
    )
    return Bundle(
        collected_at=_utc_iso(now),
        articles=unique,
        quotes=quotes,
        weather=weather,
        sources=statuses,
    )


def check_minimums(bundle: Bundle, config: Config) -> None:
    """Levanta :class:`InsufficientData` se a coleta estiver abaixo dos mínimos."""
    articles = len(bundle.articles)
    sources_ok = sum(1 for s in bundle.sources if s.ok)
    problems = []
    if articles < config.edition.min_articles:
        problems.append(f"{articles} artigos (mínimo {config.edition.min_articles})")
    if sources_ok < config.edition.min_sources_ok:
        problems.append(f"{sources_ok} feeds ok (mínimo {config.edition.min_sources_ok})")
    if problems:
        raise InsufficientData(
            "dados insuficientes para a edição: " + "; ".join(problems),
            articles=articles,
            sources_ok=sources_ok,
        )


def _no_enrichment(articles: list[Article]) -> dict[str, PageInfo]:
    """Enriquecimento desligado (modo offline): nenhuma página é consultada."""
    return {}


def _page_enricher(config: Config, fetch: Fetcher) -> EnrichFn:
    """Enriquecimento das páginas originais usando o mesmo ``fetch`` da coleta."""

    def enrich_pages(articles: list[Article]) -> dict[str, PageInfo]:
        return enrich(articles, fetch=fetch, limit=config.edition.enrich_limit)

    return enrich_pages


# ── Publicação ───────────────────────────────────────────────────────────────


def _dated_files(directory: Path, suffix: str) -> dict[date, Path]:
    """Arquivos ``AAAA-MM-DD.<suffix>`` de ``directory`` indexados pela data."""
    found: dict[date, Path] = {}
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        match = _DATED_FILE.match(path.name)
        if not match or match.group(2) != suffix or not path.is_file():
            continue
        try:
            found[date.fromisoformat(match.group(1))] = path
        except ValueError:  # ex.: 2026-02-30
            continue
    return found


def _archive_cutoff(today: date, keep_days: int) -> date | None:
    """Edições anteriores a esta data são removidas (``None``: guardar tudo)."""
    return today - timedelta(days=keep_days) if keep_days > 0 else None


def _lead_headline(data: dict[str, Any]) -> str:
    lead = data.get("lead")
    for story in data.get("stories") or []:
        if isinstance(story, dict) and story.get("id") == lead:
            return str(story.get("headline") or "")
    return ""


def _archive_entry(day: date, data_dir: Path, current: Edition) -> dict[str, Any]:
    """Entrada do índice do arquivo; lê ``data/<data>.json`` quando existir."""
    entry: dict[str, Any] = {
        "date": day.isoformat(),
        "date_label": text.pt_date_label(day),
        "href": f"{day.isoformat()}.html",
        "lead_headline": "",
        "mode": None,
    }
    if day.isoformat() == current.date:
        entry.update(
            date_label=current.date_label,
            lead_headline=current.story(current.lead).headline,
            mode=current.mode,
        )
        return entry
    json_path = data_dir / f"{day.isoformat()}.json"
    if not json_path.is_file():
        return entry
    try:
        data = _read_json(json_path)
        entry.update(
            date_label=str(data.get("date_label") or entry["date_label"]),
            lead_headline=_lead_headline(data),
            mode=data.get("mode"),
        )
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("Arquivo de edição ilegível (%s): %s — usando só a data no índice", json_path, exc)
    return entry


def latest_manifest(edition: Edition, config: Config, subject: str) -> dict[str, Any]:
    """Conteúdo de ``edicoes/latest.json`` (e-mail ainda não enviado)."""
    base_url = config.site.base_url
    return {
        "date": edition.date,
        "date_label": edition.date_label,
        "generated_at": edition.generated_at,
        "mode": edition.mode,
        "model": edition.model,
        "url": base_url,
        "edition_url": f"{base_url}{ARCHIVE_DIR}/{edition.date}.html",
        "subject": subject,
        "email_html": f"{ARCHIVE_DIR}/email.html",
        "email_text": f"{ARCHIVE_DIR}/email.txt",
        "email_sent": False,
        "email_sent_at": None,
        "email_channel": None,
        "lead_headline": edition.story(edition.lead).headline,
        "stories": len(edition.stories),
        "sources_ok": edition.stats.sources_ok,
        "sources_total": edition.stats.sources_total,
    }


def publish(
    edition: Edition,
    config: Config,
    *,
    out_dir: Path,
    subject: str,
    email_html: str,
    email_text: str,
) -> dict[str, Path]:
    """Grava a edição e o arquivo histórico em ``out_dir``.

    Tudo é renderizado em memória antes da primeira escrita. Rodar de novo no
    mesmo dia sobrescreve a edição do dia. Edições (HTML + JSON) mais antigas
    que ``site.archive_keep_days`` dias são removidas (0 = guardar todas). A
    capa (``index.html``) é o último arquivo gravado.
    """
    out_dir = Path(out_dir)
    archive_dir = out_dir / ARCHIVE_DIR
    data_dir = out_dir / DATA_DIR
    today = date.fromisoformat(edition.date)
    paths = {
        "index": out_dir / "index.html",
        "edition": archive_dir / f"{edition.date}.html",
        "data": data_dir / f"{edition.date}.json",
        "email_html": archive_dir / "email.html",
        "email_text": archive_dir / "email.txt",
        "latest": archive_dir / "latest.json",
        "archive": archive_dir / "index.html",
    }

    # Arquivo histórico: edições existentes + a de hoje, menos as que expiraram
    # (o corte é sempre anterior a hoje, então a edição do dia nunca expira).
    cutoff = _archive_cutoff(today, config.site.archive_keep_days)

    def is_expired(day: date) -> bool:
        return cutoff is not None and day < cutoff

    pages = _dated_files(archive_dir, "html")
    pages[today] = paths["edition"]
    records = _dated_files(data_dir, "json")
    expired = sorted(p for d, p in [*pages.items(), *records.items()] if is_expired(d))
    kept_days = sorted((d for d in pages if not is_expired(d)), reverse=True)
    entries = [_archive_entry(day, data_dir, edition) for day in kept_days]

    # Renderiza tudo antes de escrever.
    home_page = render_edition_page(edition, config, home_href="./", archive_href=f"{ARCHIVE_DIR}/")
    archived_page = render_edition_page(edition, config, home_href="../", archive_href="./")
    archive_index = render_archive_index(entries, config, home_href="../")
    manifest = latest_manifest(edition, config, subject)

    _write_json(paths["data"], edition.to_dict())
    write_atomic(paths["edition"], archived_page)
    write_atomic(paths["email_html"], email_html)
    write_atomic(paths["email_text"], email_text)
    for path in expired:
        path.unlink(missing_ok=True)
    if expired:
        log.info("Arquivo: %d arquivo(s) de edições anteriores a %s removido(s)", len(expired), cutoff)
    write_atomic(paths["archive"], archive_index)
    _write_json(paths["latest"], manifest)
    write_atomic(paths["index"], home_page)
    log.info("Edição %s gravada em %s (arquivo com %d edição(ões))", edition.date, out_dir, len(entries))
    return paths


# ── E-mail ───────────────────────────────────────────────────────────────────


def email_settings(config: Config, env: Mapping[str, str]) -> SMTPSettings | None:
    """Configuração SMTP do ambiente, com o nome da marca como remetente."""
    return smtp_settings_from_env(env, config.email.to, sender_name=config.brand.name)


def mark_email_sent(out_dir: Path, *, channel: str = EMAIL_CHANNEL_SMTP, sent_at: datetime | None = None) -> Path:
    """Registra em ``edicoes/latest.json`` que o e-mail da edição foi enviado."""
    path = Path(out_dir) / ARCHIVE_DIR / "latest.json"
    manifest = _read_json(path)
    manifest.update(
        email_sent=True,
        email_sent_at=_utc_iso(sent_at or datetime.now(timezone.utc)),
        email_channel=channel,
    )
    return _write_json(path, manifest)


def send_published_email(settings: SMTPSettings, *, out_dir: Path) -> None:
    """Envia por SMTP o e-mail já publicado em ``edicoes/`` e atualiza ``latest.json``.

    Levanta ``FileNotFoundError`` se a edição não foi gerada e
    :class:`~qijournal.deliver.smtp.EmailDeliveryError` se o envio falhar.
    """
    archive_dir = Path(out_dir) / ARCHIVE_DIR
    manifest = _read_json(archive_dir / "latest.json")
    html = (archive_dir / "email.html").read_text(encoding="utf-8")
    plain = (archive_dir / "email.txt").read_text(encoding="utf-8")
    subject = manifest.get("subject") or f"Edição de {manifest.get('date_label', '')}".strip()
    deliver_email(settings, subject, html, plain)
    mark_email_sent(out_dir)


def _send_run_email(
    config: Config, env: Mapping[str, str], *, subject: str, html: str, plain: str, warnings: list[str]
) -> bool:
    """Envio SMTP ao fim da execução. Falha vira aviso — a edição já está publicada."""
    try:
        settings = email_settings(config, env)
    except ValueError as exc:
        warnings.append(f"E-mail não enviado: configuração SMTP inválida ({exc})")
        return False
    if settings is None:
        log.info("SMTP não configurado (SMTP_USER/SMTP_PASSWORD/EMAIL_TO); e-mail fica para a rotina externa")
        return False
    try:
        deliver_email(settings, subject, html, plain)
    except EmailDeliveryError as exc:
        warnings.append(f"E-mail não enviado por SMTP: {exc}")
        return False
    return True


# ── Relatórios ───────────────────────────────────────────────────────────────


def _quality_warnings(bundle: Bundle, edition: Edition, config: Config, *, use_llm: bool) -> list[str]:
    """Avisos sobre a qualidade da edição (não impedem a publicação)."""
    warnings: list[str] = []
    if use_llm and config.llm.enabled and edition.mode != "ai":
        warnings.append(
            "Edição gerada sem IA (modo automático): verifique o segredo ANTHROPIC_API_KEY e o log da etapa"
        )
    failed = [s for s in bundle.sources if not s.ok]
    if bundle.sources and len(failed) / len(bundle.sources) >= FAILED_FEEDS_WARNING_RATIO:
        warnings.append(f"{len(failed)} de {len(bundle.sources)} feeds com erro na coleta")
    got = {q.id for q in bundle.quotes}
    missing_quotes = [str(e.get("id")) for e in config.market if e.get("id") not in got]
    if missing_quotes:
        warnings.append("Cotações indisponíveis: " + ", ".join(missing_quotes))
    got_cities = {w.city for w in bundle.weather}
    missing_cities = [c.name for c in config.weather if c.name not in got_cities]
    if missing_cities:
        warnings.append("Clima indisponível: " + ", ".join(missing_cities))
    return warnings


def _fmt_int(value: int | None) -> str:
    return text.format_number_pt(value or 0, 0)


def _cell(value: Any) -> str:
    """Conteúdo seguro para uma célula de tabela markdown."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).replace("|", "\\|").strip()


def _mode_label(edition: Edition) -> str:
    return f"IA ({edition.model})" if edition.mode == "ai" else "Automática (sem IA)"


def _run_summary_markdown(result: RunResult, config: Config) -> str:
    edition = result.edition
    stats = edition.stats
    base_url = config.site.base_url
    lines = [
        f"### 🗞️ {config.brand.name} — {edition.date_label}",
        "",
        "| | |",
        "|---|---|",
        f"| Modo | {_cell(_mode_label(edition))} |",
        f"| Matérias | {len(edition.stories)} |",
        f"| Feeds ok | {stats.sources_ok}/{stats.sources_total} |",
        f"| Artigos coletados | {_fmt_int(stats.articles_collected)} |",
    ]
    if stats.llm_input_tokens is not None or stats.llm_output_tokens is not None:
        lines.append(
            f"| Tokens (entrada / saída) | {_fmt_int(stats.llm_input_tokens)} / {_fmt_int(stats.llm_output_tokens)} |"
        )
    lines += [
        f"| E-mail (SMTP) | {'enviado' if result.email_sent else 'não enviado'} |",
        "",
        f"**Manchete:** {_cell(edition.story(edition.lead).headline)}",
        "",
        f"[Edição do dia]({base_url}{ARCHIVE_DIR}/{edition.date}.html) · [Capa]({base_url}) · "
        f"[Arquivo]({base_url}{ARCHIVE_DIR}/)",
    ]
    if result.warnings:
        lines += ["", "#### Avisos", *[f"- {_cell(w)}" for w in result.warnings]]
    if stats.sources_failed:
        lines += [
            "",
            f"<details><summary>Feeds com erro ({len(stats.sources_failed)})</summary>",
            "",
            "| Fonte | Erro | URL |",
            "|---|---|---|",
            *[
                f"| {_cell(f.get('source_id'))} | {_cell(f.get('error'))} | {_cell(f.get('url'))} |"
                for f in stats.sources_failed
            ],
            "",
            "</details>",
        ]
    return "\n".join(lines) + "\n"


def _append_step_summary(env: Mapping[str, str], markdown: str) -> None:
    """Anexa ``markdown`` ao resumo da etapa do GitHub Actions, se disponível."""
    path = (env.get("GITHUB_STEP_SUMMARY") or "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    except OSError as exc:
        log.warning("Não foi possível escrever o resumo do GitHub Actions: %s", exc)


def _escape_command(message: str) -> str:
    """Escapa a mensagem de um comando de workflow (``::warning::``)."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _annotate(env: Mapping[str, str], level: str, messages: list[str]) -> None:
    """Emite ``::warning::``/``::error::`` para o GitHub Actions exibir no resumo da execução."""
    if (env.get("GITHUB_ACTIONS") or "").strip().lower() != "true":
        return
    for message in messages:
        sys.stdout.write(f"::{level} title=QI Journal::{_escape_command(message)}\n")
    sys.stdout.flush()


# ── Execução ─────────────────────────────────────────────────────────────────


def run(
    config: Config,
    *,
    out_dir: Path,
    now: datetime | None = None,
    bundle_path: Path | None = None,
    use_llm: bool = True,
    send_email: bool = True,
    fetch: Fetcher = net.fetch,
    client: Any = None,
    enrich_fn: EnrichFn | None = None,
    env: Mapping[str, str] | None = None,
) -> RunResult:
    """Gera e publica a edição do dia.

    - Com ``bundle_path`` (modo offline) a coleta é lida do arquivo, o
      enriquecimento das páginas fica desligado (salvo ``enrich_fn``) e ``now``
      padrão é o ``collected_at`` do bundle, para reproduzir a edição daquele
      momento. Sem ``bundle_path`` a coleta é feita agora e salva em
      ``out_dir/build/bundle-<data>.json``.
    - Abaixo dos mínimos (``min_articles``/``min_sources_ok``) levanta
      :class:`InsufficientData` sem escrever a edição.
    - Falha no envio SMTP vira aviso em ``RunResult.warnings``; não derruba a
      execução.
    """
    env = os.environ if env is None else env
    out_dir = Path(out_dir)
    outputs: dict[str, Path] = {}

    if bundle_path is not None:
        bundle = load_bundle(bundle_path)
        now = as_utc(now) if now is not None else parse_iso_datetime(bundle.collected_at)
        enrich_fn = enrich_fn or _no_enrichment
        log.info("Modo offline: coleta lida de %s (%d artigos)", bundle_path, len(bundle.articles))
    else:
        now = as_utc(now) if now is not None else datetime.now(timezone.utc)
        bundle = collect_bundle(config, now=now, fetch=fetch)
        day = local_date(now, config.site.timezone)
        outputs["bundle"] = save_bundle(bundle, out_dir / BUILD_DIR / f"bundle-{day.isoformat()}.json")
        enrich_fn = enrich_fn or _page_enricher(config, fetch)

    try:
        check_minimums(bundle, config)
    except InsufficientData as exc:
        log.error("Edição não publicada — %s. A edição anterior continua no ar.", exc)
        _annotate(env, "error", [f"Edição não publicada: {exc}"])
        _append_step_summary(
            env,
            f"### ⚠️ {config.brand.name}: edição não publicada\n\nMotivo: {exc}. A edição anterior continua no ar.\n",
        )
        raise

    edition = make_edition(bundle, config, now=now, use_llm=use_llm, client=client, enrich_fn=enrich_fn)
    subject, email_html, email_text = render_email(edition, config)
    outputs.update(
        publish(edition, config, out_dir=out_dir, subject=subject, email_html=email_html, email_text=email_text)
    )

    warnings = _quality_warnings(bundle, edition, config, use_llm=use_llm)
    email_sent = False
    if send_email:
        email_sent = _send_run_email(config, env, subject=subject, html=email_html, plain=email_text, warnings=warnings)
        if email_sent:
            try:
                mark_email_sent(out_dir)
            except (OSError, ValueError) as exc:
                warnings.append(f"E-mail enviado, mas latest.json não foi atualizado: {exc}")
    else:
        log.info("Envio de e-mail desligado nesta execução")

    result = RunResult(edition=edition, outputs=outputs, email_sent=email_sent, warnings=warnings)
    stats = edition.stats
    log.info(
        "Edição %s publicada: modo %s, %d matérias, %d/%d feeds ok, %d artigos coletados, e-mail %s",
        edition.date,
        _mode_label(edition),
        len(edition.stories),
        stats.sources_ok,
        stats.sources_total,
        stats.articles_collected,
        "enviado" if email_sent else "não enviado",
    )
    for warning in warnings:
        log.warning("%s", warning)
    _annotate(env, "warning", warnings)
    _append_step_summary(env, _run_summary_markdown(result, config))
    return result
