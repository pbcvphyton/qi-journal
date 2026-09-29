"""Ponta a ponta com os módulos reais (coleta salva → edição automática → páginas → arquivos).

Usa ``tests/fixtures/bundle.json`` (o mesmo do smoke test do CI); nada acessa a rede.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import qijournal.__main__ as cli
from qijournal import pipeline
from qijournal.config import ROOT
from qijournal.models import Edition
from tests.fixtures.pipeline.factory import LATEST_KEYS, make_config

BUNDLE = ROOT / "tests" / "fixtures" / "bundle.json"
EXPECTED_FILES = [
    "index.html",
    "edicoes/2026-09-29.html",
    "edicoes/index.html",
    "edicoes/latest.json",
    "edicoes/email.html",
    "edicoes/email.txt",
    "data/2026-09-29.json",
]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garante modo automático e nenhum envio, independentemente da máquina."""
    for key in ("ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
        monkeypatch.delenv(key, raising=False)


def render(out: Path, *extra: str) -> int:
    return cli.main(
        ["render", "--bundle", str(BUNDLE), "--out", str(out), "--no-llm", *extra], config_loader=make_config
    )


def test_render_fixture_bundle_produces_a_complete_site(tmp_path: Path, clean_env: None):
    assert render(tmp_path) == 0

    for rel in EXPECTED_FILES:
        assert (tmp_path / rel).is_file() and (tmp_path / rel).stat().st_size > 0, rel
    assert not (tmp_path / "build").exists()  # modo offline não salva coleta

    edition = Edition.from_dict(json.loads((tmp_path / "data" / "2026-09-29.json").read_text(encoding="utf-8")))
    latest = json.loads((tmp_path / "edicoes" / "latest.json").read_text(encoding="utf-8"))
    assert set(latest) == LATEST_KEYS
    assert latest["mode"] == edition.mode == "heuristic"
    assert latest["lead_headline"] == edition.story(edition.lead).headline
    assert latest["stories"] == len(edition.stories) >= 8
    assert (latest["sources_ok"], latest["sources_total"]) == (59, 64)

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    archived = (tmp_path / "edicoes" / "2026-09-29.html").read_text(encoding="utf-8")
    assert 'href="edicoes/"' in index
    assert 'href="../"' in archived
    for page in (index, archived):
        assert "**" not in page and "<h2>:</h2>" not in page
    assert (tmp_path / "edicoes" / "email.html").stat().st_size < 90_000


def test_archive_accumulates_days_and_reruns_are_idempotent(tmp_path: Path, clean_env: None):
    assert render(tmp_path) == 0
    assert render(tmp_path) == 0  # mesma data: sobrescreve
    assert render(tmp_path, "--now", "2026-09-30T08:07:00Z") == 0

    archive = sorted(p.name for p in (tmp_path / "edicoes").glob("2026-*.html"))
    assert archive == ["2026-09-29.html", "2026-09-30.html"]
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["2026-09-29.json", "2026-09-30.json"]
    assert json.loads((tmp_path / "edicoes" / "latest.json").read_text(encoding="utf-8"))["date"] == "2026-09-30"
    archive_index = (tmp_path / "edicoes" / "index.html").read_text(encoding="utf-8")
    assert archive_index.index('href="2026-09-30.html"') < archive_index.index('href="2026-09-29.html"')
    assert not list(tmp_path.rglob("*.tmp"))


def test_run_function_with_real_modules(tmp_path: Path, clean_env: None):
    result = pipeline.run(make_config(), out_dir=tmp_path, bundle_path=BUNDLE, use_llm=False, send_email=False, env={})
    assert result.edition.date == "2026-09-29"
    assert result.email_sent is False
    assert result.warnings == []  # fixture tem todas as cotações e cidades; 5/64 feeds com erro
    assert result.outputs["index"] == tmp_path / "index.html"


def test_module_entry_point(tmp_path: Path):
    """``python -m qijournal`` funciona como processo separado (logs no stderr, código de saída)."""
    env = {"PATH": "", "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "qijournal", "render", "--bundle", str(BUNDLE), "--out", str(tmp_path), "--no-llm"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "INFO qijournal.pipeline: Edição 2026-09-29 publicada" in proc.stderr
    assert (tmp_path / "index.html").is_file()

    missing = subprocess.run(
        [sys.executable, "-m", "qijournal", "render", "--bundle", str(tmp_path / "nao-existe.json"), "--out", "x"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert missing.returncode == 1
    assert "arquivo de coleta não encontrado" in missing.stderr
    assert "Traceback" not in missing.stderr
