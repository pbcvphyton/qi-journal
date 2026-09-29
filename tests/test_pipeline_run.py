"""Testes de ``pipeline.run``/``collect_bundle`` com editor, renderização e SMTP falsos."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qijournal import pipeline
from qijournal.deliver.smtp import EmailDeliveryError
from qijournal.models import Article, Bundle, EditionStats, SourceStatus
from tests.fixtures.pipeline.factory import NOW, FakeRender, make_article, make_bundle, make_config, make_edition

SMTP_ENV = {"SMTP_USER": "robo@gmail.com", "SMTP_PASSWORD": "senha", "EMAIL_TO": "leitor@example.com"}


class FakeEditor:
    """Substitui ``make_edition``: registra os argumentos e devolve uma edição fixa."""

    def __init__(self, *, tokens: tuple[int, int] | None = None, **edition_kwargs: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tokens = tokens
        self.edition_kwargs = edition_kwargs

    def __call__(self, bundle: Bundle, config, *, now, use_llm, client, enrich_fn):
        self.calls.append(dict(bundle=bundle, now=now, use_llm=use_llm, client=client, enrich_fn=enrich_fn))
        stats = EditionStats(
            sources_total=len(bundle.sources),
            sources_ok=sum(1 for s in bundle.sources if s.ok),
            sources_failed=[
                {"source_id": s.source_id, "url": s.url, "error": s.error} for s in bundle.sources if not s.ok
            ],
            articles_collected=len(bundle.articles),
            articles_considered=len(bundle.articles),
        )
        if self.tokens:
            stats.llm_input_tokens, stats.llm_output_tokens = self.tokens
        return make_edition(stats=stats, **self.edition_kwargs)


class FakeMailer:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[Any, str, str, str]] = []
        self.error = error

    def __call__(self, settings, subject: str, html: str, text: str) -> None:
        if self.error:
            raise self.error
        self.sent.append((settings, subject, html, text))


@pytest.fixture
def render(monkeypatch: pytest.MonkeyPatch) -> FakeRender:
    fake = FakeRender()
    monkeypatch.setattr(pipeline, "render_edition_page", fake.edition_page)
    monkeypatch.setattr(pipeline, "render_archive_index", fake.archive_index)
    monkeypatch.setattr(pipeline, "render_email", fake.email)
    return fake


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch) -> FakeEditor:
    fake = FakeEditor()
    monkeypatch.setattr(pipeline, "make_edition", fake)
    return fake


@pytest.fixture
def mailer(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(pipeline, "deliver_email", fake)
    return fake


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha o teste se o pipeline tentar coletar da rede."""

    def forbidden(*args, **kwargs):
        raise AssertionError("coleta de rede não deveria acontecer neste teste")

    for name in ("collect_feeds", "collect_market", "collect_weather", "enrich"):
        monkeypatch.setattr(pipeline, name, forbidden)


def write_bundle(tmp_path: Path, bundle: Bundle | None = None) -> Path:
    path = tmp_path / "entrada" / "bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps((bundle or make_bundle()).to_dict()), encoding="utf-8")
    return path


def latest(out: Path) -> dict[str, Any]:
    return json.loads((out / "edicoes" / "latest.json").read_text(encoding="utf-8"))


# ── Modo offline (bundle) ────────────────────────────────────────────────────


def test_offline_run_replays_the_bundle_without_network(
    tmp_path: Path, render: FakeRender, editor: FakeEditor, mailer: FakeMailer, no_network: None
):
    out = tmp_path / "site"
    result = pipeline.run(make_config(), out_dir=out, bundle_path=write_bundle(tmp_path), use_llm=False, env={})

    call = editor.calls[0]
    assert call["now"] == NOW  # padrão = collected_at do bundle
    assert call["use_llm"] is False
    assert len(call["bundle"].articles) == 20
    assert call["enrich_fn"]([make_article(1)]) == {}  # enriquecimento desligado
    assert not (out / "build").exists()
    assert (out / "index.html").is_file() and (out / "edicoes" / "2026-09-29.html").is_file()
    assert set(result.outputs) == {"index", "edition", "data", "email_html", "email_text", "latest", "archive"}
    assert result.email_sent is False and mailer.sent == []


def test_offline_run_accepts_explicit_now_and_enricher(
    tmp_path: Path, render: FakeRender, editor: FakeEditor, no_network: None
):
    def enrich_fn(articles: list[Article]) -> dict:
        return {}

    naive = datetime(2026, 9, 29, 12, 0)  # sem fuso = UTC
    pipeline.run(
        make_config(),
        out_dir=tmp_path,
        bundle_path=write_bundle(tmp_path),
        now=naive,
        enrich_fn=enrich_fn,
        send_email=False,
        env={},
    )
    assert editor.calls[0]["now"] == datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    assert editor.calls[0]["enrich_fn"] is enrich_fn


@pytest.mark.parametrize(
    "content, message",
    [
        (None, "não encontrado"),
        ("{quebrado", "não foi possível ler"),
        ('{"articles": []}', "não é um bundle válido"),
        ('["lista"]', "não é um bundle válido"),
    ],
)
def test_invalid_bundle_raises_bundle_error(tmp_path: Path, content: str | None, message: str):
    path = tmp_path / "bundle.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(pipeline.BundleError, match=message):
        pipeline.run(make_config(), out_dir=tmp_path / "out", bundle_path=path, env={})
    assert not (tmp_path / "out").exists()


# ── Mínimos ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bundle, reason",
    [
        (make_bundle(articles=14), "14 artigos (mínimo 15)"),
        (make_bundle(sources_ok=3, sources_failed=10), "3 feeds ok (mínimo 4)"),
        (make_bundle(articles=0, sources_ok=0), "0 artigos (mínimo 15); 0 feeds ok (mínimo 4)"),
    ],
)
def test_insufficient_data_publishes_nothing(
    tmp_path: Path,
    render: FakeRender,
    editor: FakeEditor,
    capsys: pytest.CaptureFixture[str],
    bundle: Bundle,
    reason: str,
):
    out = tmp_path / "site"
    out.mkdir()
    (out / "index.html").write_text("edição anterior", encoding="utf-8")
    summary = tmp_path / "summary.md"
    env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}

    with pytest.raises(pipeline.InsufficientData, match=re.escape(reason)) as excinfo:
        pipeline.run(make_config(), out_dir=out, bundle_path=write_bundle(tmp_path, bundle), env=env)

    assert excinfo.value.articles == len(bundle.articles)
    assert editor.calls == [] and render.pages == []
    assert sorted(p.name for p in out.iterdir()) == ["index.html"]
    assert (out / "index.html").read_text(encoding="utf-8") == "edição anterior"
    assert "::error title=QI Journal::Edição não publicada" in capsys.readouterr().out
    assert "edição anterior continua no ar" in summary.read_text(encoding="utf-8")


def test_minimums_come_from_the_config(tmp_path: Path, render: FakeRender, editor: FakeEditor):
    config = make_config(min_articles=3, min_sources_ok=1)
    bundle = make_bundle(articles=3, sources_ok=1, sources_failed=0)
    pipeline.run(config, out_dir=tmp_path, bundle_path=write_bundle(tmp_path, bundle), send_email=False, env={})
    assert len(editor.calls) == 1


# ── Coleta ao vivo ───────────────────────────────────────────────────────────


class FakeCollectors:
    """Coletores falsos que registram o ``fetch`` recebido."""

    def __init__(self, articles: list[Article], statuses: list[SourceStatus]) -> None:
        self.articles, self.statuses = articles, statuses
        self.fetches: dict[str, Any] = {}
        self.feeds_kwargs: dict[str, Any] = {}
        self.enrich_calls: list[dict[str, Any]] = []
        self.market_error: Exception | None = None
        self.weather_error: Exception | None = None

    def feeds(self, sources, *, now, max_age_hours, global_exclude, fetch):
        self.fetches["feeds"] = fetch
        self.feeds_kwargs = dict(sources=sources, now=now, max_age_hours=max_age_hours, global_exclude=global_exclude)
        return list(self.articles), list(self.statuses)

    def market(self, entries, *, fetch, now):
        self.fetches["market"] = fetch
        if self.market_error:
            raise self.market_error
        return make_bundle().quotes

    def weather(self, cities, *, fetch):
        self.fetches["weather"] = fetch
        if self.weather_error:
            raise self.weather_error
        return make_bundle().weather

    def enrich(self, articles, *, fetch, limit):
        self.enrich_calls.append(dict(ids=[a.id for a in articles], fetch=fetch, limit=limit))
        return {}


@pytest.fixture
def collectors(monkeypatch: pytest.MonkeyPatch) -> FakeCollectors:
    base = make_bundle()
    duplicate = make_article(0, url="https://valor.globo.com/brasil/noticia/0.ghtml")  # mesmo id que art000
    fake = FakeCollectors(base.articles + [duplicate], base.sources)
    monkeypatch.setattr(pipeline, "collect_feeds", fake.feeds)
    monkeypatch.setattr(pipeline, "collect_market", fake.market)
    monkeypatch.setattr(pipeline, "collect_weather", fake.weather)
    monkeypatch.setattr(pipeline, "enrich", fake.enrich)
    return fake


def fake_fetch(url: str, **kwargs):
    raise AssertionError("o fetch falso não deveria ser chamado pelos coletores falsos")


def test_collect_bundle_runs_all_collectors_with_the_same_fetch(collectors: FakeCollectors):
    config = make_config()
    bundle = pipeline.collect_bundle(config, now=NOW, fetch=fake_fetch)

    assert collectors.fetches == {"feeds": fake_fetch, "market": fake_fetch, "weather": fake_fetch}
    assert collectors.feeds_kwargs == dict(
        sources=config.sources,
        now=NOW,
        max_age_hours=config.edition.max_age_hours,
        global_exclude=config.edition.exclude_url_patterns,
    )
    assert bundle.collected_at == "2026-09-29T08:07:00+00:00"
    assert len(bundle.articles) == 20  # duplicata removida
    assert [q.id for q in bundle.quotes] == ["usd"]
    assert [w.city for w in bundle.weather] == ["São Paulo"]
    assert bundle.sources == collectors.statuses


def test_collect_bundle_tolerates_market_and_weather_failures(
    collectors: FakeCollectors, caplog: pytest.LogCaptureFixture
):
    collectors.market_error = RuntimeError("yahoo mudou o formato")
    collectors.weather_error = ValueError("open-meteo fora")
    bundle = pipeline.collect_bundle(make_config(), now=NOW, fetch=fake_fetch)

    assert bundle.quotes == [] and bundle.weather == []
    assert len(bundle.articles) == 20
    assert "cotações" in caplog.text and "clima" in caplog.text


def test_collect_bundle_propagates_feed_bugs(collectors: FakeCollectors, monkeypatch: pytest.MonkeyPatch):
    def broken(*args, **kwargs):
        raise TypeError("bug na coleta")

    monkeypatch.setattr(pipeline, "collect_feeds", broken)
    with pytest.raises(TypeError, match="bug na coleta"):
        pipeline.collect_bundle(make_config(), now=NOW, fetch=fake_fetch)


def test_online_run_saves_raw_bundle_and_enriches_with_same_fetch(
    tmp_path: Path, render: FakeRender, editor: FakeEditor, collectors: FakeCollectors
):
    config = make_config()
    # 02:00 UTC de 30/09 ainda é 29/09 em Brasília.
    now = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)
    result = pipeline.run(config, out_dir=tmp_path, now=now, fetch=fake_fetch, send_email=False, env={})

    saved = tmp_path / "build" / "bundle-2026-09-29.json"
    assert result.outputs["bundle"] == saved
    assert Bundle.from_dict(json.loads(saved.read_text(encoding="utf-8"))).collected_at == "2026-09-30T02:00:00+00:00"

    enrich_fn = editor.calls[0]["enrich_fn"]
    enrich_fn([make_article(1), make_article(2)])
    assert collectors.enrich_calls == [
        dict(ids=["art001", "art002"], fetch=fake_fetch, limit=config.edition.enrich_limit)
    ]


def test_online_run_saves_bundle_even_when_data_is_insufficient(
    tmp_path: Path, render: FakeRender, editor: FakeEditor, collectors: FakeCollectors
):
    collectors.articles = collectors.articles[:5]
    with pytest.raises(pipeline.InsufficientData):
        pipeline.run(make_config(), out_dir=tmp_path, now=NOW, fetch=fake_fetch, env={})
    assert [p.name for p in tmp_path.iterdir()] == ["build"]
    assert (tmp_path / "build" / "bundle-2026-09-29.json").is_file()


# ── E-mail ───────────────────────────────────────────────────────────────────


def test_smtp_email_is_sent_and_recorded(tmp_path: Path, render: FakeRender, editor: FakeEditor, mailer: FakeMailer):
    result = pipeline.run(make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), env=SMTP_ENV)

    assert result.email_sent is True
    settings, subject, html, text = mailer.sent[0]
    assert settings.to == ["leitor@example.com"]
    assert settings.sender_name == "QI Journal"
    assert subject == "QI Journal — Terça-feira, 29 de setembro de 2026"
    assert html == (tmp_path / "edicoes" / "email.html").read_text(encoding="utf-8")
    assert text == (tmp_path / "edicoes" / "email.txt").read_text(encoding="utf-8")
    manifest = latest(tmp_path)
    assert manifest["email_sent"] is True
    assert manifest["email_channel"] == "smtp"
    assert datetime.fromisoformat(manifest["email_sent_at"]).tzinfo is not None
    assert not any("mail" in w.lower() for w in result.warnings)


def test_smtp_failure_is_a_warning_not_a_crash(tmp_path: Path, render: FakeRender, editor: FakeEditor, monkeypatch):
    monkeypatch.setattr(pipeline, "deliver_email", FakeMailer(EmailDeliveryError("autenticação recusada")))
    result = pipeline.run(make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), env=SMTP_ENV)

    assert result.email_sent is False
    assert "E-mail não enviado por SMTP: autenticação recusada" in result.warnings
    assert latest(tmp_path)["email_sent"] is False
    assert (tmp_path / "index.html").is_file()


def test_invalid_smtp_port_is_a_warning(tmp_path: Path, render: FakeRender, editor: FakeEditor, mailer: FakeMailer):
    env = {**SMTP_ENV, "SMTP_PORT": "porta"}
    result = pipeline.run(make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), env=env)
    assert result.email_sent is False and mailer.sent == []
    assert any("configuração SMTP inválida" in w for w in result.warnings)


def test_unconfigured_smtp_is_silent(tmp_path: Path, render: FakeRender, editor: FakeEditor, mailer: FakeMailer):
    result = pipeline.run(make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), env={})
    assert result.email_sent is False and mailer.sent == []
    assert not any("mail" in w.lower() for w in result.warnings)
    assert latest(tmp_path)["email_channel"] is None


def test_send_email_false_skips_smtp_even_when_configured(
    tmp_path: Path, render: FakeRender, editor: FakeEditor, mailer: FakeMailer
):
    result = pipeline.run(
        make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), send_email=False, env=SMTP_ENV
    )
    assert result.email_sent is False and mailer.sent == []


def test_send_published_email_uses_files_on_disk(tmp_path: Path, render: FakeRender, editor: FakeEditor, mailer):
    pipeline.run(make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), send_email=False, env={})
    settings = pipeline.email_settings(make_config(), SMTP_ENV)

    pipeline.send_published_email(settings, out_dir=tmp_path)

    _, subject, html, text = mailer.sent[0]
    assert subject == latest(tmp_path)["subject"]
    assert html == "<html>e-mail 2026-09-29</html>" and text == "e-mail 2026-09-29"
    assert latest(tmp_path)["email_sent"] is True


def test_send_published_email_without_edition(tmp_path: Path, mailer: FakeMailer):
    settings = pipeline.email_settings(make_config(), SMTP_ENV)
    with pytest.raises(FileNotFoundError):
        pipeline.send_published_email(settings, out_dir=tmp_path)
    assert mailer.sent == []


# ── Avisos e relatórios do GitHub Actions ────────────────────────────────────


def test_quality_warnings(tmp_path: Path, render: FakeRender, editor: FakeEditor):
    bundle = make_bundle(sources_ok=6, sources_failed=2)  # 25% com erro
    bundle.quotes = []
    bundle.weather = []
    result = pipeline.run(
        make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path, bundle), use_llm=True, env={}
    )
    warnings = "\n".join(result.warnings)
    assert "Edição gerada sem IA" in warnings  # IA pedida, edição saiu heurística
    assert "2 de 8 feeds com erro" in warnings
    assert "Cotações indisponíveis: usd, eur, cny, ibov" in warnings
    assert "Clima indisponível: São Paulo, Montevidéu" in warnings


def test_no_ai_warning_when_ai_was_not_requested(tmp_path: Path, render: FakeRender, editor: FakeEditor):
    result = pipeline.run(
        make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), use_llm=False, send_email=False, env={}
    )
    assert not any("IA" in w for w in result.warnings)
    assert not any("feeds com erro" in w for w in result.warnings)  # 1 de 6 < 25%


def test_step_summary_and_annotations(
    tmp_path: Path, render: FakeRender, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    editor = FakeEditor(
        mode="ai", model="claude-opus-5-5", lead_headline="Juros | câmbio em foco", tokens=(123456, 7890)
    )
    monkeypatch.setattr(pipeline, "make_edition", editor)
    bundle = make_bundle(sources_ok=5, sources_failed=1)
    bundle.sources[-1].error = "XML inválido | linha 3\nquebrada"
    bundle.quotes = []
    summary = tmp_path / "summary.md"
    summary.write_text("conteúdo anterior\n", encoding="utf-8")
    env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}

    pipeline.run(make_config(), out_dir=tmp_path / "site", bundle_path=write_bundle(tmp_path, bundle), env=env)

    md = summary.read_text(encoding="utf-8")
    assert md.startswith("conteúdo anterior\n")  # anexa, não sobrescreve
    assert "| Modo | IA (claude-opus-5-5) |" in md
    assert "| Feeds ok | 5/6 |" in md
    assert "| Tokens (entrada / saída) | 123.456 / 7.890 |" in md
    assert "**Manchete:** Juros \\| câmbio em foco" in md
    assert "(https://pbcvphyton.github.io/qi-journal/edicoes/2026-09-29.html)" in md
    assert "| quebrada0 | XML inválido \\| linha 3 quebrada | https://quebrada0.example/rss |" in md
    assert "- Cotações indisponíveis:" in md
    out = capsys.readouterr().out
    assert "::warning title=QI Journal::Cotações indisponíveis: usd" in out


def test_annotations_escape_newlines_and_percent(capsys: pytest.CaptureFixture[str]):
    pipeline._annotate({"GITHUB_ACTIONS": "true"}, "warning", ["100% das fontes\nfalharam\r"])
    assert capsys.readouterr().out == "::warning title=QI Journal::100%25 das fontes%0Afalharam%0D\n"


def test_no_annotations_outside_github_actions(capsys: pytest.CaptureFixture[str]):
    pipeline._annotate({}, "warning", ["aviso"])
    assert capsys.readouterr().out == ""


def test_unwritable_step_summary_does_not_break_the_run(
    tmp_path: Path, render: FakeRender, editor: FakeEditor, caplog: pytest.LogCaptureFixture
):
    env = {"GITHUB_STEP_SUMMARY": str(tmp_path / "nao-existe" / "summary.md")}
    result = pipeline.run(make_config(), out_dir=tmp_path, bundle_path=write_bundle(tmp_path), env=env)
    assert result.edition.date == "2026-09-29"
    assert "resumo do GitHub Actions" in caplog.text


# ── Datas ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-09-29T08:07:00Z", datetime(2026, 9, 29, 8, 7, tzinfo=UTC)),
        ("2026-09-29T05:07:00-03:00", datetime(2026, 9, 29, 8, 7, tzinfo=UTC)),
        ("2026-09-29T08:07", datetime(2026, 9, 29, 8, 7, tzinfo=UTC)),
        (" 2026-09-29 ", datetime(2026, 9, 29, tzinfo=UTC)),
    ],
)
def test_parse_iso_datetime(value: str, expected: datetime):
    parsed = pipeline.parse_iso_datetime(value)
    assert parsed == expected and parsed.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("value", ["ontem", "", "2026-13-01"])
def test_parse_iso_datetime_rejects_garbage(value: str):
    with pytest.raises(ValueError, match="ISO 8601"):
        pipeline.parse_iso_datetime(value)


def test_local_date_uses_site_timezone():
    assert (
        pipeline.local_date(datetime(2026, 9, 30, 2, 59, tzinfo=UTC), "America/Sao_Paulo").isoformat() == "2026-09-29"
    )
    assert pipeline.local_date(datetime(2026, 9, 30, 3, 0, tzinfo=UTC), "America/Sao_Paulo").isoformat() == "2026-09-30"
