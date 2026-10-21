"""Comandos do Instagram (python -m qijournal instagram ...), sem rede."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from qijournal.config import load_config
from qijournal.instagram import cli
from qijournal.instagram.graph import GraphError, TokenInfo
from qijournal.instagram.publicar import Resultado


def args(tmp_path, **kw):
    base = dict(media_dir=str(tmp_path), now=None, sync=False, wait_max=0, token_file=None, token_out=None, alert_out=str(tmp_path / "alerta.txt"))
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
