"""Fila de publicações e publicador do Instagram (sem rede)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from qijournal.instagram.fila import EXPIRED, FAILED, MAX_ATTEMPTS, PENDING, PUBLISHED, Fila, Post
from qijournal.instagram.graph import GraphError
from qijournal.instagram.publicar import TokenInvalido, publish_due

BRT = timezone(timedelta(hours=-3))
BASE = "https://raw.githubusercontent.com/pbcvphyton/qi-journal/instagram/"


def post(pid="2026-10-20-carrossel", kind="carousel", due="2026-10-20T07:30:00-03:00", expires="2026-10-20T23:00:00-03:00", **kw):
    images = kw.pop("images", ["instagram/2026-10-20/carrossel-01.jpg", "instagram/2026-10-20/carrossel-02.jpg"])
    return Post(id=pid, date=pid[:10], kind=kind, due=due, expires=expires, images=images, caption=kw.pop("caption", "Manchete do dia\n\nCorpo"), **kw)


def at(hhmm: str, day: str = "2026-10-20") -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00-03:00")


def test_fila_grava_e_le(tmp_path):
    fila = Fila.load(tmp_path)
    assert fila.posts == []
    fila.upsert([post(), post("2026-10-20-story", kind="story", images=["instagram/2026-10-20/story.jpg"])])
    fila.token = {"refreshed_at": "2026-10-20T07:00:00-03:00"}
    fila.save()
    again = Fila.load(tmp_path)
    assert [p.id for p in again.posts] == ["2026-10-20-carrossel", "2026-10-20-story"]
    assert again.token["refreshed_at"].startswith("2026-10-20")
    assert (tmp_path / "fila.json").is_file()


def test_refazer_a_edicao_nao_troca_o_que_ja_saiu(tmp_path):
    fila = Fila.load(tmp_path)
    fila.upsert([post(caption="v1"), post("2026-10-20-mercados", kind="image", images=["a.jpg"], caption="m1")])
    fila.get("2026-10-20-carrossel").status = PUBLISHED
    changed = fila.upsert([post(caption="v2"), post("2026-10-20-mercados", kind="image", images=["a.jpg"], caption="m2")])
    assert changed == ["2026-10-20-mercados"]
    assert fila.get("2026-10-20-carrossel").caption == "v1"
    assert fila.get("2026-10-20-mercados").caption == "m2"


def test_tipo_desconhecido_e_recusado(tmp_path):
    with pytest.raises(ValueError):
        Fila.load(tmp_path).upsert([post(kind="reel")])


def test_due_respeita_horario_e_prazo():
    fila = Fila(posts=[post(), post("2026-10-19-carrossel", due="2026-10-19T07:30:00-03:00", expires="2026-10-19T23:00:00-03:00")])
    assert fila.due(at("07:00")) == []
    ready = fila.due(at("07:45"))
    assert [p.id for p in ready] == ["2026-10-20-carrossel"]
    assert fila.get("2026-10-19-carrossel").status == EXPIRED


def test_falha_permanente_nao_volta_para_a_fila():
    p = post(status=FAILED, attempts=MAX_ATTEMPTS)
    assert Fila(posts=[p]).due(at("08:00")) == []
    p2 = post(status=FAILED, attempts=1)
    assert Fila(posts=[p2]).due(at("08:00")) == [p2]


def test_prune_remove_registros_antigos():
    fila = Fila(posts=[post(), post("2026-08-01-carrossel")])
    removed = fila.prune(date(2026, 10, 20), keep_days=45)
    assert [p.id for p in removed] == ["2026-08-01-carrossel"]
    assert [p.id for p in fila.posts] == ["2026-10-20-carrossel"]


class FakeClient:
    def __init__(self, *, recent=(), fail=None):
        self.recent = list(recent)
        self.fail = fail
        self.posted = []

    def recent_media(self, limit=25):
        return self.recent

    def _go(self, kind, *args):
        if self.fail:
            raise self.fail
        self.posted.append((kind, args))
        return f"m{len(self.posted)}"

    def post_carousel(self, urls, caption, alts):
        return self._go("carousel", urls, caption, alts)

    def post_image(self, url, caption, alt):
        return self._go("image", url, caption, alt)

    def post_story(self, url):
        return self._go("story", url)

    def permalink(self, media_id):
        return f"https://www.instagram.com/p/{media_id}/"


def test_publica_o_que_venceu_e_grava_a_cada_publicacao():
    fila = Fila(posts=[post(), post("2026-10-20-story", kind="story", images=["instagram/2026-10-20/story.jpg"], due="2026-10-20T07:00:00-03:00")])
    saves = []
    client = FakeClient()
    res = publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True, save=lambda: saves.append(1))
    assert [p.id for p in res.publicados] == ["2026-10-20-story", "2026-10-20-carrossel"]
    assert len(saves) == 2
    kind, (urls, caption, alts) = client.posted[1]
    assert kind == "carousel" and urls[0] == BASE + "instagram/2026-10-20/carrossel-01.jpg"
    p = fila.get("2026-10-20-carrossel")
    assert p.status == PUBLISHED and p.media_id == "m2" and p.permalink.endswith("/m2/")
    assert publish_due(fila, client, media_url=BASE, now=at("09:00"), check_url=lambda u: True).publicados == []


def test_espera_as_imagens_no_ar_sem_gastar_tentativa():
    fila = Fila(posts=[post()])
    res = publish_due(fila, FakeClient(), media_url=BASE, now=at("08:00"), check_url=lambda u: not u.endswith("02.jpg"))
    assert [p.id for p in res.aguardando] == ["2026-10-20-carrossel"]
    assert fila.get("2026-10-20-carrossel").status == PENDING
    assert fila.get("2026-10-20-carrossel").attempts == 0


def test_nao_publica_de_novo_o_que_ja_esta_no_perfil():
    fila = Fila(posts=[post(caption="Manchete do dia: algo\n\nCorpo")])
    recent = [{"id": "999", "caption": "Manchete do dia — algo\noutro corpo", "timestamp": "2026-10-20T10:31:00+0000", "permalink": "https://www.instagram.com/p/x/"}]
    client = FakeClient(recent=recent)
    res = publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert client.posted == []
    assert fila.get("2026-10-20-carrossel").media_id == "999"
    assert res.publicados


def test_erro_passageiro_conta_tentativa_e_permanente_desiste():
    fila = Fila(posts=[post()])
    publish_due(fila, FakeClient(fail=GraphError("instável", transient=True)), media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    p = fila.get("2026-10-20-carrossel")
    assert p.status == FAILED and p.attempts == 1 and "instável" in p.last_error
    publish_due(fila, FakeClient(fail=GraphError("imagem inválida", code=9004)), media_url=BASE, now=at("09:00"), check_url=lambda u: True)
    assert p.attempts == MAX_ATTEMPTS
    assert fila.due(at("10:00")) == []


def test_limite_de_24h_para_a_execucao_sem_gastar_tentativa():
    fila = Fila(posts=[post(), post("2026-10-20-mercados", kind="image", images=["a.jpg"], due="2026-10-20T07:40:00-03:00")])
    res = publish_due(fila, FakeClient(fail=GraphError("limite", subcode=2207042)), media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert res.limite
    assert all(p.status == PENDING and p.attempts == 0 for p in fila.posts)


def test_token_invalido_interrompe_tudo():
    fila = Fila(posts=[post()])
    with pytest.raises(TokenInvalido):
        publish_due(fila, FakeClient(fail=GraphError("expirou", code=190)), media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert fila.get("2026-10-20-carrossel").attempts == 0
