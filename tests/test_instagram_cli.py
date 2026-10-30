"""Comandos do Instagram (python -m qijournal instagram ...), sem rede."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from qijournal.config import load_config
from qijournal.instagram import cli
from qijournal.instagram.graph import GraphError, TokenInfo
from qijournal.instagram.publicar import Resultado


def args(tmp_path, **kw):
    base = dict(media_dir=str(tmp_path), now=None, sync=False, wait_max=0, token_file=None, token_out=None, confirm=False, alert_out=str(tmp_path / "alerta.txt"))
    base.update(kw)
    return argparse.Namespace(**base)


ENV = {"IG_USER_ID": "1784", "IG_ACCESS_TOKEN": "VELHO"}


def test_sem_segredos_ou_pausado_nao_publica(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "publish_due", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia publicar")))
    config = load_config()
    assert cli.cmd_publicar(args(tmp_path), config, env={}) == cli.EXIT_OK
    assert cli.cmd_publicar(args(tmp_path), config, env={**ENV, "IG_PAUSE": "true"}) == cli.EXIT_OK
    assert cli.cmd_token(args(tmp_path), config, env={}) == cli.EXIT_OK


def test_token_renovado_no_job_substitui_o_do_ambiente(tmp_path, monkeypatch):
    seen = {}

    def fake_client(env):
        seen["token"] = env["IG_ACCESS_TOKEN"]
        return object()

    monkeypatch.setattr(cli, "client_from_env", fake_client)
    monkeypatch.setattr(cli, "publish_due", lambda *a, **k: Resultado())
    token_file = tmp_path / "ig-token"
    token_file.write_text("NOVO\n")
    assert cli.cmd_publicar(args(tmp_path, token_file=str(token_file)), load_config(), env=ENV) == cli.EXIT_OK
    assert seen["token"] == "NOVO"
    assert cli.cmd_publicar(args(tmp_path, token_file=str(tmp_path / "nao-existe")), load_config(), env=ENV) == cli.EXIT_OK
    assert seen["token"] == "VELHO"


class FakeTokenClient:
    host = "graph.instagram.com"

    def __init__(self, *, invalid=False, new="NOVO"):
        self.invalid = invalid
        self.new = new

    def account(self):
        if self.invalid:
            raise GraphError("Error validating access token", code=190)
        return {"id": "1784", "username": "triangula"}

    def refresh_token(self):
        return TokenInfo(access_token=self.new, expires_in=5184000)


def test_token_renova_grava_arquivo_e_datas_sem_o_token(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "client_from_env", lambda env: FakeTokenClient())
    out = tmp_path / "ig-token"
    now = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    assert cli.cmd_token(args(tmp_path, token_out=str(out), now=now), load_config(), env=ENV) == cli.EXIT_OK
    assert out.read_text() == "NOVO"
    fila = (tmp_path / "fila.json").read_text()
    assert "2026-10-21T10:00:00+00:00" in fila and "NOVO" not in fila and "VELHO" not in fila
    assert not (tmp_path / "alerta.txt").exists()
    # Uma semana ainda não passou: não renova de novo.
    out.unlink()
    assert cli.cmd_token(args(tmp_path, token_out=str(out), now=now), load_config(), env=ENV) == cli.EXIT_OK
    assert not out.exists()


def test_token_invalido_avisa_o_dono(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "client_from_env", lambda env: FakeTokenClient(invalid=True))
    assert cli.cmd_token(args(tmp_path), load_config(), env=ENV) == cli.EXIT_ERROR
    alerta = (tmp_path / "alerta.txt").read_text()
    assert "expirou ou foi revogado" in alerta and "IG_ACCESS_TOKEN" in alerta


def test_url_da_midia_na_branch_instagram():
    assert cli.media_url(load_config(), {}) == "https://raw.githubusercontent.com/pbcvphyton/qi-journal/instagram/"
    assert cli.media_url(load_config(), {"IG_MEDIA_URL": "https://exemplo.org/ig"}) == "https://exemplo.org/ig/"


def test_link_do_instagram_no_site_e_no_email():
    import json
    from pathlib import Path

    from qijournal.models import Edition
    from qijournal.render.email import render_email
    from qijournal.render.web import build_view, instagram_url

    config = load_config()
    assert instagram_url(config) == ""
    config.instagram.handle = "@triangula.jornal"
    assert instagram_url(config) == "https://www.instagram.com/triangula.jornal/"
    edition = Edition.from_dict(json.loads((Path(__file__).parent / "fixtures" / "edicao-2026-10-20.json").read_text()))
    assert build_view(edition, config).instagram_url.endswith("/triangula.jornal/")
    _subject, html, text = render_email(edition, config)
    assert "https://www.instagram.com/triangula.jornal/" in html and "Siga no Instagram" in text


def test_token_novo_so_vale_depois_de_guardado_no_segredo(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setattr(cli, "client_from_env", lambda env: FakeTokenClient())
    now = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    cli.cmd_token(args(tmp_path, token_out=str(tmp_path / "t"), now=now), load_config(), env=ENV)
    token = _json.loads((tmp_path / "fila.json").read_text())["token"]
    # Antes de guardar: vale a validade estimada do token do segredo; a do novo fica pendente.
    assert token["expires_at"].startswith("2026-12-20") and token["estimated"] is True
    assert token["pending"]["refreshed_at"].startswith("2026-10-21")
    assert cli.cmd_token(args(tmp_path, confirm=True), load_config(), env=ENV) == cli.EXIT_OK
    token = _json.loads((tmp_path / "fila.json").read_text())["token"]
    assert "pending" not in token and "estimated" not in token and token["refreshed_at"].startswith("2026-10-21")


def test_aviso_de_vencimento_quando_o_segredo_nunca_e_atualizado(tmp_path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(cli, "client_from_env", lambda env: FakeTokenClient())
    start = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    alerta = tmp_path / "alerta.txt"
    for week in range(9):  # o token novo nunca é guardado (sem --confirm)
        cli.cmd_token(args(tmp_path, token_out=str(tmp_path / "t"), now=start + timedelta(days=7 * week)), load_config(), env=ENV)
    assert alerta.exists() and "expira em" in alerta.read_text()


def test_oscilacao_da_api_no_teste_do_token_nao_bloqueia_a_publicacao(tmp_path, monkeypatch):
    class Instavel(FakeTokenClient):
        def account(self):
            raise GraphError("Service unavailable", status=503, transient=True)

    monkeypatch.setattr(cli, "client_from_env", lambda env: Instavel())
    assert cli.cmd_token(args(tmp_path), load_config(), env=ENV) == cli.EXIT_OK
    assert not (tmp_path / "alerta.txt").exists()


# ── gerar ─────────────────────────────────────────────────────────────────


def _pasta_publicada(tmp_path, raw=None):
    import json as _json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "edicao-2026-10-20.json"
    raw = raw or _json.loads(fixture.read_text(encoding="utf-8"))
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "edicoes").mkdir(exist_ok=True)
    (tmp_path / "data" / "2026-10-20.json").write_text(_json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "edicoes" / "latest.json").write_text('{"date": "2026-10-20"}', encoding="utf-8")
    return raw


def gerar_args(tmp_path, **kw):
    base = dict(dir=str(tmp_path), media_dir=str(tmp_path / "midia"), now=None, force=False, date=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_gerar_dia_especifico_e_refazer_o_dia(tmp_path, monkeypatch):
    from datetime import date

    from qijournal.instagram import gerar
    from qijournal.instagram.fila import PUBLISHED, Fila
    from tests.test_instagram_conteudo import FakeRenderer

    monkeypatch.setattr(gerar, "Renderer", FakeRenderer)
    raw = _pasta_publicada(tmp_path)
    config = load_config()
    # Sem --date, a última edição (20/10) não é de hoje: nada a gerar.
    assert cli.cmd_gerar(gerar_args(tmp_path, now=datetime(2026, 10, 30, 9, tzinfo=timezone.utc)), config) == cli.EXIT_OK
    assert not (tmp_path / "midia" / "fila.json").exists()
    # --date: o dia de data/, com "agora" no começo do dia (tudo dentro do prazo).
    assert cli.cmd_gerar(gerar_args(tmp_path, date=date(2026, 10, 20)), config) == cli.EXIT_OK
    fila = Fila.load(tmp_path / "midia")
    ids = [p.id for p in fila.posts]
    assert ids == ["lancamento-comece-aqui", "2026-10-20-manha", "2026-10-20-stories-1", "2026-10-20-stories-2",
                   "2026-10-20-stories-3", "2026-10-20-stories-4", "2026-10-20-numero", "2026-10-20-noite"]
    noite = fila.get("2026-10-20-noite")
    assert (noite.serie, noite.numero_serie, noite.title) == ("raiox", 1, "Raio-X da cobertura") and noite.entidades
    # A manhã saiu; a edição é refeita sem IA: a manhã publicada fica, a noite vira o Mercado em linha
    # (mesmo id) e os stories que a geração nova não tem mais saem da fila.
    manha = fila.get("2026-10-20-manha")
    manha.status = PUBLISHED
    fila.save()
    raw["mode"] = "heuristic"
    _pasta_publicada(tmp_path, raw)
    assert cli.cmd_gerar(gerar_args(tmp_path, date=date(2026, 10, 20)), config) == cli.EXIT_OK
    fila = Fila.load(tmp_path / "midia")
    assert fila.get("2026-10-20-manha").title == "Em 1 minuto" and fila.get("2026-10-20-manha").status == PUBLISHED
    assert fila.get("2026-10-20-noite").title == "Mercado em linha" and fila.get("2026-10-20-noite").numero_serie == 0
    stories = [p.id for p in fila.posts if p.kind == "story"]
    assert 2 <= len(stories) < 4 and "2026-10-20-stories-4" not in stories
    assert fila.get("lancamento-comece-aqui") is not None  # permanente: não sai com o dia refeito
    # as imagens que nenhum post usa mais foram apagadas
    usadas = {img for p in fila.posts for img in p.images}
    assert {f"2026-10-20/{f.name}" for f in (tmp_path / "midia" / "2026-10-20").glob("*.jpg")} == usadas


def test_opcao_date_no_parser():
    from datetime import date

    from qijournal.__main__ import build_parser

    args = build_parser().parse_args(["instagram", "gerar", "--date", "2026-10-21", "--media-dir", "m"])
    assert args.date == date(2026, 10, 21) and args.handler is cli.cmd_gerar
    assert build_parser().parse_args(["instagram", "gerar"]).date is None
