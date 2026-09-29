"""Testes de ``pipeline.publish``: arquivos, manifesto, arquivo histórico e poda."""

from __future__ import annotations

import json
import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qijournal import pipeline
from qijournal.models import Edition
from tests.fixtures.pipeline.factory import LATEST_KEYS, FakeRender, make_config, make_edition


@pytest.fixture
def render(monkeypatch: pytest.MonkeyPatch) -> FakeRender:
    fake = FakeRender()
    monkeypatch.setattr(pipeline, "render_edition_page", fake.edition_page)
    monkeypatch.setattr(pipeline, "render_archive_index", fake.archive_index)
    monkeypatch.setattr(pipeline, "render_email", fake.email)
    return fake


def publish(edition: Edition, out: Path, config=None) -> dict[str, Path]:
    return pipeline.publish(
        edition,
        config or make_config(),
        out_dir=out,
        subject=f"QI Journal — {edition.date_label}",
        email_html="<html>e-mail</html>",
        email_text="e-mail em texto",
    )


def seed_edition(out: Path, day: str, *, headline: str | None = None, with_json: bool = True) -> None:
    """Simula uma edição publicada num dia anterior."""
    (out / "edicoes").mkdir(parents=True, exist_ok=True)
    (out / "edicoes" / f"{day}.html").write_text(f"<html>{day}</html>", encoding="utf-8")
    if with_json:
        (out / "data").mkdir(parents=True, exist_ok=True)
        edition = make_edition(day, lead_headline=headline or f"Manchete de {day}", mode="ai", model="claude-opus-5-5")
        (out / "data" / f"{day}.json").write_text(json.dumps(edition.to_dict()), encoding="utf-8")


def test_publish_writes_every_output(tmp_path: Path, render: FakeRender):
    edition = make_edition()
    paths = publish(edition, tmp_path)

    expected = {
        "index": "index.html",
        "edition": "edicoes/2026-09-29.html",
        "data": "data/2026-09-29.json",
        "email_html": "edicoes/email.html",
        "email_text": "edicoes/email.txt",
        "latest": "edicoes/latest.json",
        "archive": "edicoes/index.html",
    }
    assert {k: p.relative_to(tmp_path).as_posix() for k, p in paths.items()} == expected
    assert all(p.is_file() and p.stat().st_size > 0 for p in paths.values())
    assert paths["email_html"].read_text(encoding="utf-8") == "<html>e-mail</html>"
    assert paths["email_text"].read_text(encoding="utf-8") == "e-mail em texto"


def test_pages_get_the_right_relative_links(tmp_path: Path, render: FakeRender):
    paths = publish(make_edition(), tmp_path)

    assert sorted(render.pages) == sorted([("2026-09-29", "./", "edicoes/"), ("2026-09-29", "../", "./")])
    assert "href='edicoes/'" in paths["index"].read_text(encoding="utf-8")
    assert "href='../'" in paths["edition"].read_text(encoding="utf-8")
    assert "href='../'" in paths["archive"].read_text(encoding="utf-8")


def test_data_json_round_trips_the_edition(tmp_path: Path, render: FakeRender):
    edition = make_edition(lead_headline="Ações da Petrobras sobem após anúncio")
    paths = publish(edition, tmp_path)

    raw = paths["data"].read_text(encoding="utf-8")
    assert "Ações da Petrobras" in raw  # ensure_ascii=False
    assert Edition.from_dict(json.loads(raw)).to_dict() == edition.to_dict()


def test_latest_json_has_exactly_the_contract_keys(tmp_path: Path, render: FakeRender):
    edition = make_edition(mode="ai", model="claude-opus-5-5", stories=5)
    paths = publish(edition, tmp_path)

    latest = json.loads(paths["latest"].read_text(encoding="utf-8"))
    assert set(latest) == LATEST_KEYS
    assert latest == {
        "date": "2026-09-29",
        "date_label": "Terça-feira, 29 de setembro de 2026",
        "generated_at": edition.generated_at,
        "mode": "ai",
        "model": "claude-opus-5-5",
        "url": "https://pbcvphyton.github.io/qi-journal/",
        "edition_url": "https://pbcvphyton.github.io/qi-journal/edicoes/2026-09-29.html",
        "subject": "QI Journal — Terça-feira, 29 de setembro de 2026",
        "email_html": "edicoes/email.html",
        "email_text": "edicoes/email.txt",
        # URLs absolutas e estáveis para a rotina do Gmail (Pages e raw.githubusercontent.com)
        "email_html_url": "https://pbcvphyton.github.io/qi-journal/edicoes/email.html",
        "email_text_url": "https://pbcvphyton.github.io/qi-journal/edicoes/email.txt",
        "email_html_raw_url": "https://raw.githubusercontent.com/pbcvphyton/qi-journal/main/edicoes/email.html",
        "email_text_raw_url": "https://raw.githubusercontent.com/pbcvphyton/qi-journal/main/edicoes/email.txt",
        "email_html_bytes": paths["email_html"].stat().st_size,
        "email_sent": False,
        "email_sent_at": None,
        "email_channel": None,
        "lead_headline": "Copom mantém a Selic e sinaliza cautela",
        "stories": 5,
        "sources_ok": 5,
        "sources_total": 6,
    }


def test_archive_index_lists_existing_editions_newest_first(tmp_path: Path, render: FakeRender):
    seed_edition(tmp_path, "2026-09-27", headline="Dólar cai a R$ 5,10")
    seed_edition(tmp_path, "2026-09-28", with_json=False)
    (tmp_path / "edicoes" / "notas.html").write_text("não é edição", encoding="utf-8")
    (tmp_path / "edicoes" / "2026-02-30.html").write_text("data impossível", encoding="utf-8")

    publish(make_edition(lead_headline="Manchete de hoje"), tmp_path)

    entries = render.archives[-1]
    assert [e["date"] for e in entries] == ["2026-09-29", "2026-09-28", "2026-09-27"]
    assert entries[0] == {
        "date": "2026-09-29",
        "date_label": "Terça-feira, 29 de setembro de 2026",
        "href": "2026-09-29.html",
        "lead_headline": "Manchete de hoje",
        "mode": "heuristic",
    }
    # Sem data/<dia>.json: rótulo derivado da data, sem manchete.
    assert entries[1] == {
        "date": "2026-09-28",
        "date_label": "Segunda-feira, 28 de setembro de 2026",
        "href": "2026-09-28.html",
        "lead_headline": "",
        "mode": None,
    }
    assert entries[2]["lead_headline"] == "Dólar cai a R$ 5,10"
    assert entries[2]["mode"] == "ai"
    # Arquivos que não são edições ficam intactos.
    assert (tmp_path / "edicoes" / "notas.html").exists()
    assert (tmp_path / "edicoes" / "2026-02-30.html").exists()


def test_unreadable_archive_json_falls_back_to_the_date(
    tmp_path: Path, render: FakeRender, caplog: pytest.LogCaptureFixture
):
    seed_edition(tmp_path, "2026-09-27")
    (tmp_path / "data" / "2026-09-27.json").write_text("{corrompido", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="qijournal.pipeline"):
        publish(make_edition(), tmp_path)

    old = render.archives[-1][1]
    assert (old["date_label"], old["lead_headline"], old["mode"]) == ("Domingo, 27 de setembro de 2026", "", None)
    assert "ilegível" in caplog.text


def test_prunes_editions_older_than_keep_days(tmp_path: Path, render: FakeRender):
    config = make_config()
    config.site.archive_keep_days = 30  # corte: 2026-08-30
    for day in ["2026-08-01", "2026-08-29", "2026-08-30", "2026-09-28"]:
        seed_edition(tmp_path, day)
    (tmp_path / "data" / "2026-07-01.json").write_text("{}", encoding="utf-8")  # JSON órfão antigo

    publish(make_edition(), tmp_path, config)

    remaining_html = sorted(p.name for p in (tmp_path / "edicoes").glob("2026-*.html"))
    remaining_json = sorted(p.name for p in (tmp_path / "data").glob("*.json"))
    assert remaining_html == ["2026-08-30.html", "2026-09-28.html", "2026-09-29.html"]
    assert remaining_json == ["2026-08-30.json", "2026-09-28.json", "2026-09-29.json"]
    assert [e["date"] for e in render.archives[-1]] == ["2026-09-29", "2026-09-28", "2026-08-30"]


def test_keep_days_zero_keeps_everything(tmp_path: Path, render: FakeRender):
    config = make_config()
    config.site.archive_keep_days = 0
    seed_edition(tmp_path, "2020-01-01")

    publish(make_edition(), tmp_path, config)

    assert (tmp_path / "edicoes" / "2020-01-01.html").exists()
    assert (tmp_path / "data" / "2020-01-01.json").exists()


def test_publishing_twice_the_same_day_overwrites(tmp_path: Path, render: FakeRender):
    publish(make_edition(lead_headline="Primeira versão"), tmp_path)
    paths = publish(make_edition(lead_headline="Versão corrigida"), tmp_path)

    assert sorted(p.name for p in (tmp_path / "edicoes").iterdir()) == [
        "2026-09-29.html",
        "email.html",
        "email.txt",
        "index.html",
        "latest.json",
    ]
    assert [e["date"] for e in render.archives[-1]] == ["2026-09-29"]
    assert "Versão corrigida" in paths["index"].read_text(encoding="utf-8")
    assert json.loads(paths["latest"].read_text(encoding="utf-8"))["lead_headline"] == "Versão corrigida"
    assert not list(tmp_path.rglob("*.tmp"))


def test_render_failure_leaves_previous_edition_untouched(
    tmp_path: Path, render: FakeRender, monkeypatch: pytest.MonkeyPatch
):
    publish(make_edition("2026-09-28", lead_headline="Edição de ontem"), tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def broken(*args, **kwargs):
        raise RuntimeError("template quebrado")

    monkeypatch.setattr(pipeline, "render_archive_index", broken)
    with pytest.raises(RuntimeError):
        publish(make_edition("2026-09-29"), tmp_path)

    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_write_atomic_replaces_content_and_is_world_readable(tmp_path: Path):
    target = tmp_path / "sub" / "pagina.html"
    pipeline.write_atomic(target, "versão 1")
    pipeline.write_atomic(target, "versão 2 — ç")

    assert target.read_text(encoding="utf-8") == "versão 2 — ç"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert [p.name for p in target.parent.iterdir()] == ["pagina.html"]


def test_write_atomic_failure_keeps_old_file_and_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "index.html"
    target.write_text("original", encoding="utf-8")

    def failing_replace(src, dst):
        raise OSError("disco cheio")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disco cheio"):
        pipeline.write_atomic(target, "novo")

    assert target.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["index.html"]


def test_mark_email_sent_updates_only_the_delivery_fields(tmp_path: Path, render: FakeRender):
    paths = publish(make_edition(), tmp_path)
    before = json.loads(paths["latest"].read_text(encoding="utf-8"))

    pipeline.mark_email_sent(tmp_path, sent_at=datetime(2026, 9, 29, 8, 15, 3, 123, tzinfo=UTC))

    after = json.loads(paths["latest"].read_text(encoding="utf-8"))
    assert after == {
        **before,
        "email_sent": True,
        "email_sent_at": "2026-09-29T08:15:03+00:00",
        "email_channel": "smtp",
    }
