"""Orquestração do jornal: coleta → edição → renderização → arquivos → e-mail.

Layout publicado (raiz do GitHub Pages)::

    index.html                 edição do dia (capa)
    edicoes/AAAA-MM-DD.html    cópia arquivada de cada edição
    edicoes/AAAA-MM-DD-todas.html  todas as notícias do dia, por editoria e assunto
    edicoes/index.html         índice do arquivo
    edicoes/latest.json        manifesto da última edição (usado pela rotina de e-mail)
    edicoes/email.html|.txt    e-mail da última edição
    data/AAAA-MM-DD.json       edição em JSON (``Edition.to_dict``)
    build/bundle-AAAA-MM-DD.json   coleta crua (fora do git; artefato do Actions)
    build/pages-AAAA-MM-DD.json    páginas enriquecidas (reproduz o enriquecimento no ``render --bundle``)

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
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar
from zoneinfo import ZoneInfo

from qijournal import net, text
from qijournal.collect.enrich import PageInfo, enrich
from qijournal.collect.feeds import collect_feeds, dedupe_articles, sanitize_articles
from qijournal.collect.market import collect_market
from qijournal.collect.weather import collect_weather
from qijournal.config import Config
from qijournal.deliver.smtp import EmailDeliveryError, SMTPSettings, smtp_settings_from_env
from qijournal.deliver.smtp import send_email as deliver_email
from qijournal.edit import make_edition
from qijournal.models import Article, Bundle, Edition
from qijournal.net import Fetcher
from qijournal.render.email import render_email
from qijournal.render.web import index_page_name, render_archive_index, render_edition_page, render_index_page

log = logging.getLogger(__name__)

ARCHIVE_DIR = "edicoes"
DATA_DIR = "data"
BUILD_DIR = "build"
EMAIL_CHANNEL_SMTP = "smtp"
# Fração de feeds com erro a partir da qual a execução emite um aviso.
FAILED_FEEDS_WARNING_RATIO = 0.25
# A rotina do Gmail copia email.html no parâmetro htmlBody: acima disso, aviso.
EMAIL_MAX_BYTES = 40_000

_DATED_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(html|json)$")
_INDEX_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})-todas\.html$")  # todas as notícias do dia

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
            exclude_title_patterns=config.edition.exclude_title_patterns,
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
    """Levanta :class:`InsufficientData` se a coleta estiver abaixo dos mínimos.

    Além dos números absolutos (artigos, feeds ok), exige uma fração mínima de
    feeds ok (``min_sources_ratio``) e de veículos em português com artigos
    (``min_pt_sources_ok``): com os sites brasileiros fora do ar, sairia uma
    edição só em inglês por cima da boa.
    """
    edition = config.edition
    articles = len(bundle.articles)
    total = len(bundle.sources)
    sources_ok = sum(1 for s in bundle.sources if s.ok)
    problems = []
    if articles < edition.min_articles:
        problems.append(f"{articles} artigos (mínimo {edition.min_articles})")
    if sources_ok < edition.min_sources_ok:
        problems.append(f"{sources_ok} feeds ok (mínimo {edition.min_sources_ok})")
    if total and edition.min_sources_ratio > 0 and sources_ok / total < edition.min_sources_ratio:
        problems.append(f"{sources_ok}/{total} feeds ok (mínimo {edition.min_sources_ratio:.0%})")
    pt_sources = len({a.source_id for a in bundle.articles if a.lang == "pt"})
    if pt_sources < edition.min_pt_sources_ok:
        problems.append(f"{pt_sources} fontes em português (mínimo {edition.min_pt_sources_ok})")
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


class _RecordingEnricher:
    """Guarda tudo o que o enriquecimento devolveu (salvo em ``build/pages-<data>.json``)."""

    def __init__(self, fn: EnrichFn) -> None:
        self._fn = fn
        self.pages: dict[str, PageInfo] = {}

    def __call__(self, articles: list[Article]) -> dict[str, PageInfo]:
        result = self._fn(articles) or {}
        self.pages.update(result)
        return result


def pages_path_for(bundle_path: Path) -> Path:
    """``build/bundle-2026-09-29.json`` → ``build/pages-2026-09-29.json``."""
    bundle_path = Path(bundle_path)
    return bundle_path.with_name(bundle_path.name.replace("bundle-", "pages-", 1))


def save_pages(pages: Mapping[str, PageInfo], path: Path) -> Path:
    data = {article_id: asdict(info) if not isinstance(info, dict) else info for article_id, info in pages.items()}
    return _write_json(Path(path), data)


def load_pages(path: Path) -> dict[str, PageInfo]:
    """Lê ``pages-*.json``; arquivo ausente ou inválido → ``{}`` (sem enriquecimento)."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = _read_json(path)
        return {
            str(article_id): PageInfo(
                image=info.get("image"), description=info.get("description"), text=info.get("text")
            )
            for article_id, info in data.items()
            if isinstance(info, dict)
        }
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("Páginas enriquecidas ilegíveis (%s): %s — seguindo sem enriquecimento", path, exc)
        return {}


def _saved_enricher(pages: Mapping[str, PageInfo]) -> EnrichFn:
    """Enriquecimento "gravado": devolve as páginas salvas na execução original."""

    def replay(articles: list[Article]) -> dict[str, PageInfo]:
        return {a.id: pages[a.id] for a in articles if a.id in pages}

    return replay


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


def _index_files(directory: Path) -> dict[date, Path]:
    """Páginas ``AAAA-MM-DD-todas.html`` (todas as notícias do dia) indexadas pela data."""
    found: dict[date, Path] = {}
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        match = _INDEX_FILE.match(path.name)
        if match and path.is_file():
            try:
                found[date.fromisoformat(match.group(1))] = path
            except ValueError:
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


def _raw_base(repo_url: str) -> str | None:
    """``https://github.com/dono/repo`` → ``https://raw.githubusercontent.com/dono/repo/main/``."""
    match = re.match(r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (repo_url or "").strip())
    return f"https://raw.githubusercontent.com/{match.group(1)}/{match.group(2)}/main/" if match else None


def latest_manifest(edition: Edition, config: Config, subject: str, email_html: str | None = None) -> dict[str, Any]:
    """Conteúdo de ``edicoes/latest.json`` (e-mail ainda não enviado).

    Os campos relativos (``email_html``/``email_text``) continuam para
    compatibilidade; os ``*_url`` absolutos e os ``*_raw_url`` (branch main no
    raw.githubusercontent.com) servem à rotina do Gmail sem depender do Pages.
    """
    base_url = config.site.base_url
    raw = _raw_base(config.site.repo_url)
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
        "email_html_url": f"{base_url}{ARCHIVE_DIR}/email.html",
        "email_text_url": f"{base_url}{ARCHIVE_DIR}/email.txt",
        "email_html_raw_url": f"{raw}{ARCHIVE_DIR}/email.html" if raw else None,
        "email_text_raw_url": f"{raw}{ARCHIVE_DIR}/email.txt" if raw else None,
        "email_html_bytes": len(email_html.encode("utf-8")) if email_html is not None else None,
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
    if edition.index:  # todas as notícias do dia (edições antigas não têm a lista)
        paths["all_news"] = archive_dir / index_page_name(edition.date)

    # Arquivo histórico: edições existentes + a de hoje, menos as que expiraram
    # (o corte é sempre anterior a hoje, então a edição do dia nunca expira).
    cutoff = _archive_cutoff(today, config.site.archive_keep_days)

    def is_expired(day: date) -> bool:
        return cutoff is not None and day < cutoff

    pages = _dated_files(archive_dir, "html")
    pages[today] = paths["edition"]
    records = _dated_files(data_dir, "json")
    index_pages = _index_files(archive_dir)
    expired = sorted(p for d, p in [*pages.items(), *records.items(), *index_pages.items()] if is_expired(d))
    kept_days = sorted((d for d in pages if not is_expired(d)), reverse=True)
    entries = [_archive_entry(day, data_dir, edition) for day in kept_days]

    # Renderiza tudo antes de escrever.
    home_page = render_edition_page(edition, config, home_href="./", archive_href=f"{ARCHIVE_DIR}/")
    archived_page = render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)
    archive_index = render_archive_index(entries, config, home_href="../")
    all_news_page = (
        render_index_page(edition, config, home_href="../", edition_href=f"{edition.date}.html")
        if edition.index
        else None
    )
    manifest = latest_manifest(edition, config, subject, email_html)
    # Refazer a edição no mesmo dia não apaga o registro de e-mail já enviado
    # (a rotina do Gmail usa email_sent para não enviar em dobro).
    previous = previous_manifest(out_dir)
    if previous.get("date") == edition.date and previous.get("email_sent"):
        for key in ("email_sent", "email_sent_at", "email_channel"):
            manifest[key] = previous.get(key)

    _write_json(paths["data"], edition.to_dict())
    write_atomic(paths["edition"], archived_page)
    if all_news_page is not None:
        write_atomic(paths["all_news"], all_news_page)
    else:  # refazer o dia sem a lista não deixa uma página velha no lugar
        (archive_dir / index_page_name(edition.date)).unlink(missing_ok=True)
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


def previous_manifest(out_dir: Path) -> dict[str, Any]:
    """``edicoes/latest.json`` já publicado (``{}`` se ausente ou ilegível)."""
    try:
        data = _read_json(Path(out_dir) / ARCHIVE_DIR / "latest.json")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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


class EmailAlreadySent(Exception):
    """O e-mail desta edição já foi enviado (use ``force`` para reenviar)."""


def send_published_email(settings: SMTPSettings, *, out_dir: Path, force: bool = False) -> None:
    """Envia por SMTP o e-mail já publicado em ``edicoes/`` e atualiza ``latest.json``.

    Levanta ``FileNotFoundError`` se a edição não foi gerada,
    :class:`EmailAlreadySent` se ``latest.json`` já registra o envio (salvo com
    ``force``) e :class:`~qijournal.deliver.smtp.EmailDeliveryError` se o envio falhar.
    """
    archive_dir = Path(out_dir) / ARCHIVE_DIR
    manifest = _read_json(archive_dir / "latest.json")
    if manifest.get("email_sent") and not force:
        raise EmailAlreadySent(
            f"e-mail de {manifest.get('date')} já enviado em {manifest.get('email_sent_at')}; "
            "use --force-email para reenviar"
        )
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
    except Exception as exc:  # noqa: BLE001 — a edição já foi gravada; o envio é acessório
        log.exception("Falha inesperada no envio SMTP")
        warnings.append(f"E-mail não enviado por SMTP: erro inesperado ({type(exc).__name__})")
        return False
    return True


# ── Relatórios ───────────────────────────────────────────────────────────────


def _quality_warnings(
    bundle: Bundle, edition: Edition, config: Config, *, use_llm: bool, email_html: str | None = None
) -> list[str]:
    """Avisos sobre a qualidade da edição (não impedem a publicação)."""
    warnings: list[str] = []
    if email_html is not None:
        size = len(email_html.encode("utf-8"))
        if size > EMAIL_MAX_BYTES:
            warnings.append(
                f"email.html com {size / 1024:.0f} KB passa do limite de {EMAIL_MAX_BYTES // 1000} KB "
                "da rotina do Gmail; enxugue o template"
            )
    if use_llm and config.llm.enabled and edition.mode != "ai":
        warnings.append(
            "Edição gerada sem IA (modo automático): verifique os segredos AIMLAPI_KEY / SENSENOVA_API_KEY / "
            "MISTRAL_API_KEY / MOONSHOT_API_KEY / ANTHROPIC_API_KEY "
            "e o log da etapa"
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


def _annotate(env: Mapping[str, str], level: str, messages: list[str], *, title: str) -> None:
    """Emite ``::warning::``/``::error::`` para o GitHub Actions exibir no resumo da execução."""
    if (env.get("GITHUB_ACTIONS") or "").strip().lower() != "true":
        return
    title = re.sub(r"[,:\r\n]", " ", title).strip() or "Edição diária"  # vírgula e ":" delimitam o comando
    for message in messages:
        sys.stdout.write(f"::{level} title={title}::{_escape_command(message)}\n")
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
    force_email: bool = False,
) -> RunResult:
    """Gera e publica a edição do dia.

    - Com ``bundle_path`` (modo offline) a coleta é lida do arquivo, as regras
      atuais de limpeza/exclusão são reaplicadas aos artigos, o enriquecimento
      vem de ``pages-<data>.json`` ao lado do bundle quando existir (senão fica
      desligado, salvo ``enrich_fn``) e ``now`` padrão é o ``collected_at`` do
      bundle, para reproduzir a edição daquele momento. Sem ``bundle_path`` a
      coleta é feita agora e salva em ``out_dir/build/bundle-<data>.json``, e o
      enriquecimento em ``out_dir/build/pages-<data>.json``.
    - O e-mail SMTP não é reenviado se ``latest.json`` já registra o envio da
      edição do mesmo dia, salvo ``force_email``.
    - Abaixo dos mínimos (``min_articles``/``min_sources_ok``) levanta
      :class:`InsufficientData` sem escrever a edição.
    - Falha no envio SMTP vira aviso em ``RunResult.warnings``; não derruba a
      execução.
    """
    env = os.environ if env is None else env
    out_dir = Path(out_dir)
    outputs: dict[str, Path] = {}

    recorder: _RecordingEnricher | None = None
    pages_path: Path | None = None
    if bundle_path is not None:
        bundle = load_bundle(bundle_path)
        bundle.articles = sanitize_articles(
            bundle.articles,
            exclude_url_patterns=config.edition.exclude_url_patterns,
            exclude_title_patterns=config.edition.exclude_title_patterns,
        )
        now = as_utc(now) if now is not None else parse_iso_datetime(bundle.collected_at)
        if enrich_fn is None:
            saved = load_pages(pages_path_for(bundle_path))
            if saved:
                log.info("Enriquecimento reproduzido de %s (%d páginas)", pages_path_for(bundle_path), len(saved))
            enrich_fn = _saved_enricher(saved) if saved else _no_enrichment
        log.info("Modo offline: coleta lida de %s (%d artigos)", bundle_path, len(bundle.articles))
    else:
        now = as_utc(now) if now is not None else datetime.now(timezone.utc)
        bundle = collect_bundle(config, now=now, fetch=fetch)
        day = local_date(now, config.site.timezone)
        outputs["bundle"] = save_bundle(bundle, out_dir / BUILD_DIR / f"bundle-{day.isoformat()}.json")
        recorder = _RecordingEnricher(enrich_fn or _page_enricher(config, fetch))
        enrich_fn = recorder
        pages_path = out_dir / BUILD_DIR / f"pages-{day.isoformat()}.json"

    try:
        check_minimums(bundle, config)
    except InsufficientData as exc:
        log.error("Edição não publicada — %s. A edição anterior continua no ar.", exc)
        _annotate(env, "error", [f"Edição não publicada: {exc}"], title=config.brand.name)
        _append_step_summary(
            env,
            f"### ⚠️ {config.brand.name}: edição não publicada\n\nMotivo: {exc}. A edição anterior continua no ar.\n",
        )
        raise

    edition = make_edition(bundle, config, now=now, use_llm=use_llm, client=client, enrich_fn=enrich_fn)
    if recorder is not None and pages_path is not None:
        try:
            outputs["pages"] = save_pages(recorder.pages, pages_path)
        except (OSError, TypeError, ValueError) as exc:
            log.warning("Páginas enriquecidas não salvas (%s): %s", pages_path, exc)
    subject, email_html, email_text = render_email(edition, config)
    previous = previous_manifest(out_dir)
    already_sent = previous.get("date") == edition.date and previous.get("email_sent") is True
    outputs.update(
        publish(edition, config, out_dir=out_dir, subject=subject, email_html=email_html, email_text=email_text)
    )

    warnings = _quality_warnings(bundle, edition, config, use_llm=use_llm, email_html=email_html)
    email_sent = False
    if send_email and already_sent and not force_email:
        message = (
            f"E-mail de {edition.date} já enviado em {previous.get('email_sent_at')}; "
            "não reenviado (use --force-email para reenviar)"
        )
        log.info("%s", message)
        warnings.append(message)
    elif send_email:
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
    _annotate(env, "warning", warnings, title=config.brand.name)
    _append_step_summary(env, _run_summary_markdown(result, config))
    return result
