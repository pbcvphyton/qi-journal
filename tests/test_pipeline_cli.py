"""Testes da linha de comando (``python -m qijournal``)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import qijournal.__main__ as cli
from qijournal import net, pipeline
from qijournal.config import SourceConfig
from qijournal.deliver.smtp import EmailDeliveryError
from qijournal.models import Bundle
from qijournal.net import FetchError, Response
from tests.fixtures.pipeline.factory import FakeRender, make_bundle, make_config, make_edition, rss_feed

SMTP_ENV = {"SMTP_USER": "robo@gmail.com", "SMTP_PASSWORD": "senha", "EMAIL_TO": "leitor@example.com"}


@pytest.fixture
def config():
    return make_config()


def main(argv: list[str], config) -> int:
    return cli.main(argv, config_loader=lambda: config)


class RunRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.error = error

    def __call__(self, config, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        out = kwargs["out_dir"]
        return pipeline.RunResult(edition=make_edition(), outputs={"index": out / "index.html"}, email_sent=False)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> RunRecorder:
    rec = RunRecorder()
    monkeypatch.setattr(pipeline, "run", rec)
    return rec


# ── run / render ─────────────────────────────────────────────────────────────


def test_run_defaults(recorder: RunRecorder, config):
    assert main(["run"], config) == 0
    assert recorder.kwargs == dict(
        out_dir=Path("."), now=None, bundle_path=None, use_llm=True, send_email=True, force_email=False
    )


def test_run_with_all_options(recorder: RunRecorder, config, tmp_path: Path):
    argv = [
        "run",
        "--out",
        str(tmp_path),
        "--bundle",
        "b.json",
        "--no-llm",
        "--no-email",
        "--force-email",
        "--now",
        "2026-09-29T05:07:00-03:00",
        "-v",
    ]
    assert main(argv, config) == 0
    assert recorder.kwargs == dict(
        out_dir=tmp_path,
        now=datetime(2026, 9, 29, 8, 7, tzinfo=UTC),
        bundle_path=Path("b.json"),
        use_llm=False,
        send_email=False,
        force_email=True,
    )


def test_render_is_offline_and_never_sends_email(recorder: RunRecorder, config, tmp_path: Path):
    assert main(["render", "--bundle", "b.json", "--out", str(tmp_path)], config) == 0
    assert recorder.kwargs["bundle_path"] == Path("b.json")
    assert recorder.kwargs["send_email"] is False
    assert recorder.kwargs["use_llm"] is True
    assert main(["render", "--bundle", "b.json", "--out", str(tmp_path), "--no-llm"], config) == 0
    assert recorder.kwargs["use_llm"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["render", "--out", "x"],  # --bundle obrigatório
        ["render", "--bundle", "b.json"],  # --out obrigatório
        ["run", "--now", "amanhã"],
        ["collect"],
        [],
        ["publicar"],
    ],
)
def test_invalid_arguments_exit_2(argv: list[str], config, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as excinfo:
        main(argv, config)
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "error, code, logged",
    [
        (pipeline.InsufficientData("poucos artigos", articles=3, sources_ok=1), 2, None),
        (pipeline.BundleError("arquivo de coleta não encontrado: x.json"), 1, "arquivo de coleta não encontrado"),
        (RuntimeError("bug"), 1, "Erro inesperado no comando 'run'"),
    ],
)
def test_run_exit_codes(monkeypatch: pytest.MonkeyPatch, config, caplog: pytest.LogCaptureFixture, error, code, logged):
    monkeypatch.setattr(pipeline, "run", RunRecorder(error))
    assert main(["run"], config) == code
    if logged:
        assert logged in caplog.text
    if isinstance(error, RuntimeError):
        assert "Traceback" in caplog.text


# ── collect ──────────────────────────────────────────────────────────────────


def test_collect_saves_the_bundle(monkeypatch: pytest.MonkeyPatch, config, tmp_path: Path):
    calls = []

    def fake_collect(cfg, *, now):
        calls.append(now)
        return make_bundle()

    monkeypatch.setattr(pipeline, "collect_bundle", fake_collect)
    out = tmp_path / "coleta" / "bundle.json"
    assert main(["collect", "--out", str(out)], config) == 0
    assert calls[0].tzinfo is not None
    saved = Bundle.from_dict(json.loads(out.read_text(encoding="utf-8")))
    assert saved.to_dict() == make_bundle().to_dict()


# ── send-email ───────────────────────────────────────────────────────────────


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch, config, tmp_path: Path) -> Path:
    fake = FakeRender()
    monkeypatch.setattr(pipeline, "render_edition_page", fake.edition_page)
    monkeypatch.setattr(pipeline, "render_archive_index", fake.archive_index)
    subject, html, text = fake.email(make_edition(), config)
    pipeline.publish(make_edition(), config, out_dir=tmp_path, subject=subject, email_html=html, email_text=text)
    return tmp_path


def test_send_email_sends_published_files(monkeypatch: pytest.MonkeyPatch, config, published: Path):
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)
    sent = []
    monkeypatch.setattr(pipeline, "deliver_email", lambda settings, subject, html, text: sent.append((subject, html)))

    assert main(["send-email", "--dir", str(published)], config) == 0

    assert sent == [("QI Journal — Terça-feira, 29 de setembro de 2026", "<html>e-mail 2026-09-29</html>")]
    latest = json.loads((published / "edicoes" / "latest.json").read_text(encoding="utf-8"))
    assert latest["email_sent"] is True and latest["email_channel"] == "smtp"


def test_send_email_without_smtp_config(monkeypatch, config, published: Path, caplog: pytest.LogCaptureFixture):
    for key in [*SMTP_ENV, "SMTP_PORT"]:
        monkeypatch.delenv(key, raising=False)
    assert main(["send-email", "--dir", str(published)], config) == 1
    assert "SMTP não configurado" in caplog.text


def test_send_email_with_invalid_port(monkeypatch, config, published: Path, caplog: pytest.LogCaptureFixture):
    for key, value in {**SMTP_ENV, "SMTP_PORT": "x"}.items():
        monkeypatch.setenv(key, value)
    assert main(["send-email", "--dir", str(published)], config) == 1
    assert "Configuração SMTP inválida" in caplog.text


def test_send_email_without_edition(monkeypatch, config, tmp_path: Path, caplog: pytest.LogCaptureFixture):
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)
    assert main(["send-email", "--dir", str(tmp_path)], config) == 1
    assert "Edição não encontrada" in caplog.text and "latest.json" in caplog.text


def test_send_email_delivery_failure(monkeypatch, config, published: Path, caplog: pytest.LogCaptureFixture):
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)

    def failing(*args):
        raise EmailDeliveryError("autenticação recusada")

    monkeypatch.setattr(pipeline, "deliver_email", failing)
    assert main(["send-email", "--dir", str(published)], config) == 1
    assert "autenticação recusada" in caplog.text
    latest = json.loads((published / "edicoes" / "latest.json").read_text(encoding="utf-8"))
    assert latest["email_sent"] is False


# ── check-sources ────────────────────────────────────────────────────────────


def feeds_config(config, count: int = 3):
    config.sources = [
        SourceConfig(id=f"fonte{i}", name=f"Fonte {i}", url=f"https://fonte{i}.example/rss", topics=["brasil"])
        for i in range(count)
    ]
    return config


def fake_fetch_for(feeds: dict[str, bytes | Exception]):
    def fetch(url: str, **kwargs) -> Response:
        body = feeds[url]
        if isinstance(body, Exception):
            raise body
        return Response(url=url, status=200, content=body, headers={"content-type": "application/rss+xml"})

    return fetch


def test_check_sources_reports_each_feed(config):
    now = datetime(2026, 9, 29, 8, 7, tzinfo=UTC)
    feeds_config(config)
    fetch = fake_fetch_for(
        {
            "https://fonte0.example/rss": rss_feed(
                [
                    ("Selic fica estável em reunião do Copom", "https://fonte0.example/a", now - timedelta(hours=2)),
                    ("Dólar recua com fluxo estrangeiro na bolsa", "https://fonte0.example/b", now - timedelta(days=3)),
                ]
            ),
            "https://fonte1.example/rss": rss_feed(
                [("Inflação de serviços preocupa o Banco Central", "https://fonte1.example/c", now - timedelta(days=5))]
            ),
            "https://fonte2.example/rss": FetchError("https://fonte2.example/rss", "HTTP 403", status=403),
        }
    )

    checks = cli.check_sources(config, now=now, fetch=fetch)

    by_id = {c.source_id: c for c in checks}
    assert (by_id["fonte0"].ok, by_id["fonte0"].items, by_id["fonte0"].recent) == (True, 2, 1)
    assert by_id["fonte0"].newest_age_hours == pytest.approx(2)
    assert (by_id["fonte1"].ok, by_id["fonte1"].recent) == (True, 0)
    assert by_id["fonte1"].newest_age_hours == pytest.approx(120)
    assert (by_id["fonte2"].ok, by_id["fonte2"].error, by_id["fonte2"].newest_age_hours) == (False, "HTTP 403", None)

    table = cli.format_source_table(checks, max_age_hours=30).splitlines()
    assert table[0].split() == ["fonte", "estado", "itens", "≤30h", "mais", "novo", "url"]
    assert table[1].split() == ["fonte0", "ok", "2", "1", "2", "h", "https://fonte0.example/rss"]
    assert table[2].split() == ["fonte1", "ok", "1", "0", "5", "d", "https://fonte1.example/rss"]
    assert table[3].split() == ["fonte2", "ERRO:", "HTTP", "403", "0", "0", "—", "https://fonte2.example/rss"]
    assert table[-1] == "2/3 feeds ok; 1 feed(s) ok sem itens nas últimas 30 h"


@pytest.mark.parametrize(
    "hours, label", [(None, "—"), (0.2, "12 min"), (0, "0 min"), (5.4, "5 h"), (47, "47 h"), (50, "2 d")]
)
def test_format_age(hours, label):
    assert cli._format_age(hours) == label


def test_check_sources_command(monkeypatch: pytest.MonkeyPatch, config, capsys: pytest.CaptureFixture[str]):
    now = datetime.now(UTC)
    feeds_config(config, count=2)
    monkeypatch.setattr(
        net,
        "fetch",
        fake_fetch_for(
            {
                "https://fonte0.example/rss": rss_feed(
                    [
                        (
                            "Governo anuncia corte de gastos no orçamento",
                            "https://fonte0.example/a",
                            now - timedelta(hours=1),
                        )
                    ]
                ),
                "https://fonte1.example/rss": FetchError("https://fonte1.example/rss", "HTTP 500", status=500),
            }
        ),
    )
    assert main(["check-sources"], config) == 0
    out = capsys.readouterr().out
    assert "fonte0" in out and "ERRO: HTTP 500" in out and "1/2 feeds ok" in out


def test_check_sources_command_fails_when_every_feed_fails(monkeypatch, config, capsys):
    feeds_config(config, count=2)
    monkeypatch.setattr(
        net,
        "fetch",
        fake_fetch_for({f"https://fonte{i}.example/rss": FetchError("u", "HTTP 403", status=403) for i in range(2)}),
    )
    assert main(["check-sources"], config) == 1
    assert "0/2 feeds ok" in capsys.readouterr().out


# ── logging ──────────────────────────────────────────────────────────────────


def test_setup_logging_levels():
    root = logging.getLogger()
    previous = root.level
    try:
        cli.setup_logging(verbose=True)
        assert root.level == logging.DEBUG
        assert logging.getLogger("httpx").level == logging.INFO  # nunca DEBUG: despejaria prompts
        cli.setup_logging(verbose=False)
        assert root.level == logging.INFO
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        root.setLevel(previous)


def test_send_email_twice_needs_force(monkeypatch: pytest.MonkeyPatch, config, published: Path, caplog):
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)
    sent = []
    monkeypatch.setattr(pipeline, "deliver_email", lambda settings, subject, html, text: sent.append(subject))
    assert main(["send-email", "--dir", str(published)], config) == 0
    assert main(["send-email", "--dir", str(published)], config) == 0  # não reenvia, sem erro
    assert len(sent) == 1 and "já enviado" in caplog.text
    assert main(["send-email", "--dir", str(published), "--force-email"], config) == 0
    assert len(sent) == 2
