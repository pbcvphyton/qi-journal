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
    """Cliente falso: registra containers e publicações; ``fail`` estoura na criação do container."""

    def __init__(self, *, recent=(), fail=None, quota=(0, 100), statuses=None):
        self.recent = list(recent)
        self.fail = fail
        self.quota = quota
        self.statuses = dict(statuses or {})
        self.containers = []
        self.published = []

    def recent_media(self, limit=25):
        return self.recent

    def publishing_quota(self):
        return self.quota

    def create_image(self, url, *, caption=None, alt_text=None, carousel_item=False, story=False):
        if self.fail:
            raise self.fail
        cid = f"c{len(self.containers) + 1}"
        self.containers.append((cid, "story" if story else "item" if carousel_item else "image", url, caption, alt_text))
        return cid

    def create_carousel(self, children, caption):
        cid = f"c{len(self.containers) + 1}"
        self.containers.append((cid, "carousel", list(children), caption, None))
        return cid

    def wait_ready(self, cid):
        pass

    def container_status(self, cid):
        return self.statuses.get(cid, "FINISHED")

    def publish(self, cid, *, feed=True):
        self.published.append(cid)
        return f"m{len(self.published)}"

    def find_published(self):
        return "m-recente"

    def permalink(self, media_id):
        return f"https://www.instagram.com/p/{media_id}/"


def test_publica_o_que_venceu_e_grava_a_cada_publicacao():
    fila = Fila(posts=[post(), post("2026-10-20-story", kind="story", images=["instagram/2026-10-20/story.jpg"], due="2026-10-20T07:00:00-03:00")])
    saves = []
    client = FakeClient()
    res = publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True, save=saves.append)
    assert [p.id for p in res.publicados] == ["2026-10-20-story", "2026-10-20-carrossel"]
    assert len(saves) == 4  # container criado + publicado, para cada um
    carousel = [c for c in client.containers if c[1] == "carousel"][0]
    items = [c for c in client.containers if c[1] == "item"]
    assert carousel[2] == [c[0] for c in items] and carousel[3].startswith("Manchete do dia")
    assert items[0][2] == BASE + "instagram/2026-10-20/carrossel-01.jpg"
    p = fila.get("2026-10-20-carrossel")
    assert p.status == PUBLISHED and p.media_id == "m2" and p.permalink.endswith("/m2/") and p.container_id is None
    assert fila.get("2026-10-20-story").permalink is None
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
    assert client.containers == [] and client.published == []
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


def test_intervalo_minimo_entre_posts_de_feed():
    manha = post("2026-10-20-carrossel", status=PUBLISHED, published_at="2026-10-20T16:30:00-03:00")
    noite = post("2026-10-20-raiox", due="2026-10-20T18:30:00-03:00", caption="Raio-X\n")
    story = post("2026-10-20-story", kind="story", images=["s.jpg"], due="2026-10-20T18:00:00-03:00")
    fila = Fila(posts=[manha, noite, story])
    res = publish_due(fila, FakeClient(), media_url=BASE, now=at("18:45"), check_url=lambda u: True)
    assert [p.id for p in res.publicados] == ["2026-10-20-story"]  # story não respeita o intervalo
    assert [p.id for p in res.aguardando] == ["2026-10-20-raiox"]
    res = publish_due(fila, FakeClient(), media_url=BASE, now=at("19:31"), check_url=lambda u: True)
    assert [p.id for p in res.publicados] == ["2026-10-20-raiox"]


def test_container_gravado_e_reaproveitado_na_execucao_seguinte():
    fila = Fila(posts=[post(container_id="c-antigo", status=FAILED, attempts=1)])
    client = FakeClient(statuses={"c-antigo": "FINISHED"})
    publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert client.containers == [] and client.published == ["c-antigo"]

    fila = Fila(posts=[post(container_id="c-antigo", status=FAILED, attempts=1)])
    client = FakeClient(statuses={"c-antigo": "PUBLISHED"})
    publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert client.published == [] and fila.posts[0].status == PUBLISHED and fila.posts[0].media_id == "m-recente"

    fila = Fila(posts=[post(container_id="c-antigo", status=FAILED, attempts=1)])
    client = FakeClient(statuses={"c-antigo": "EXPIRED"})
    publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert len(client.containers) == 3 and client.published == ["c3"]


def test_cota_no_limite_nao_publica():
    fila = Fila(posts=[post()])
    res = publish_due(fila, FakeClient(quota=(99, 100)), media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert res.limite and fila.posts[0].status == PENDING and fila.posts[0].attempts == 0


def test_container_morto_e_recriado_e_falha_de_gravacao_adia_o_post():
    fila = Fila(posts=[post()])
    client = FakeClient()
    client.wait_ready = lambda cid: (_ for _ in ()).throw(GraphError("ERROR", transient=True, dead_container=True))
    publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    p = fila.posts[0]
    assert p.status == FAILED and p.container_id is None and p.attempts == 1

    fila = Fila(posts=[post()])
    client = FakeClient()
    publish_due(fila, client, media_url=BASE, now=at("08:00"), check_url=lambda u: True, save=lambda what: False)
    assert client.published == [] and fila.posts[0].status == FAILED and fila.posts[0].attempts == 1


def test_refazer_a_fila_mantem_o_container_pendente():
    fila = Fila(posts=[post(status=FAILED, attempts=2, container_id="c-velho")])
    fila.upsert([post(caption="nova legenda")])
    p = fila.posts[0]
    assert p.container_id == "c-velho" and p.attempts == 2 and p.caption == "nova legenda"


def test_post_permanente_nunca_sai_da_fila():
    lanc = post("lancamento-comece-aqui", status=PUBLISHED, permanent=True)
    lanc.date = "2026-01-01"
    fila = Fila(posts=[lanc, post("2026-01-02-manha")])
    fila.prune(date(2026, 10, 20))
    assert [p.id for p in fila.posts] == ["lancamento-comece-aqui"]
    assert fila.has_published("lancamento-comece-aqui")


# ── Séries numeradas e campos novos (§4.0.5) ────────────────────────────────


def test_fila_antiga_sem_os_campos_novos_ainda_le(tmp_path):
    import json

    antigo = {
        "posts": [{"id": "2026-10-20-raiox", "date": "2026-10-20", "kind": "carousel", "due": "2026-10-20T18:30:00-03:00",
                   "expires": "2026-10-20T22:30:00-03:00", "images": ["a.jpg"], "caption": "x", "status": "published",
                   "campo_que_nao_existe_mais": 1}],
        "token": {"refreshed_at": "2026-10-20T07:00:00-03:00"},
    }
    (tmp_path / "fila.json").write_text(json.dumps(antigo), encoding="utf-8")
    fila = Fila.load(tmp_path)
    p = fila.posts[0]
    assert (p.serie, p.numero_serie, p.story_ids, p.chave, p.entidades, p.formula, p.redacao, p.metricas) == ("", 0, [], "", [], "", {}, {})
    assert fila.series == {}
    fila.series["raiox"] = 3
    fila.save()
    data = json.loads((tmp_path / "fila.json").read_text(encoding="utf-8"))
    assert data["series"] == {"raiox": 3} and data["posts"][0]["serie"] == ""
    assert Fila.load(tmp_path).series == {"raiox": 3}


def test_numero_de_serie_refazer_mantem_e_expirado_nao_consome():
    fila = Fila(series={"raiox": 14})
    assert fila.numero_para("raiox", "2026-10-21-noite") == 15
    fila.upsert([post("2026-10-21-noite", serie="raiox", numero_serie=15)])
    # Gerar de novo o mesmo dia mantém o número (mesmo id, mesma série).
    assert fila.numero_para("raiox", "2026-10-21-noite") == 15
    # O post expira sem sair: a série não avança e o dia seguinte reaproveita o 15.
    fila.get("2026-10-21-noite").status = EXPIRED
    assert fila.numero_para("raiox", "2026-10-22-noite") == 15
    # O Placar do dia é da série do Raio-X; o Mercado em linha não leva número.
    assert fila.numero_para("raiox", "2026-10-22-noite") == fila.numero_para("raiox", None)
    assert fila.numero_para("mercado", "2026-10-22-noite") == 0 and fila.numero_para("", None) == 0
    assert fila.numero_para("numero", "x") == 1  # série nova começa em 001


def test_refazer_a_noite_trocando_de_serie_nao_herda_o_numero():
    fila = Fila(series={"raiox": 14})
    fila.upsert([post("2026-10-21-noite", serie="mercado", numero_serie=0)])
    assert fila.numero_para("raiox", "2026-10-21-noite") == 15


def test_publicar_avanca_a_serie_so_quando_sai():
    fila = Fila(series={"raiox": 14, "numero": 2},
                posts=[post("2026-10-20-noite", serie="raiox", numero_serie=15),
                       post("2026-10-20-mercado", kind="image", images=["m.jpg"], serie="mercado", due="2026-10-20T07:00:00-03:00")])
    res = publish_due(fila, FakeClient(fail=GraphError("instável", transient=True)), media_url=BASE, now=at("08:00"),
                      check_url=lambda u: True)
    assert res.falhas and fila.series == {"raiox": 14, "numero": 2}
    publish_due(fila, FakeClient(), media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert fila.get("2026-10-20-mercado").status == PUBLISHED
    assert fila.series == {"raiox": 14, "numero": 2}  # o Mercado em linha não mexe em série
    publish_due(fila, FakeClient(), media_url=BASE, now=at("11:30"), check_url=lambda u: True)  # depois do intervalo do feed
    assert fila.series == {"raiox": 15, "numero": 2}
    assert fila.numero_para("raiox", "2026-10-21-noite") == 16


def test_ja_estava_no_perfil_tambem_avanca_a_serie():
    fila = Fila(series={"numero": 2}, posts=[post(caption="Número do dia: 0,3%\n\nx", serie="numero", numero_serie=3)])
    recent = [{"id": "77", "caption": "Número do dia: 0,3%", "timestamp": "2026-10-20T10:31:00+0000"}]
    publish_due(fila, FakeClient(recent=recent), media_url=BASE, now=at("08:00"), check_url=lambda u: True)
    assert fila.series["numero"] == 3


def test_chaves_do_numero_e_entidades_do_raiox_para_o_gerador():
    def numero(day, chave, status=PUBLISHED):
        return post(f"{day}-numero", due=f"{day}T12:00:00-03:00", expires=f"{day}T16:00:00-03:00", serie="numero",
                    chave=chave, status=status)

    def raiox(day, ents, status=PUBLISHED, serie="raiox"):
        return post(f"{day}-noite", due=f"{day}T18:30:00-03:00", expires=f"{day}T22:30:00-03:00", serie=serie,
                    entidades=ents, status=status)

    fila = Fila()
    assert fila.chaves_numero("2026-10-22") is None  # sem histórico: vale a regra antiga
    fila.posts = [
        numero("2026-10-13", "velho"),  # mais de 7 dias antes
        numero("2026-10-15", "acum:ibov"),
        numero("2026-10-20", "0,3%"),
        numero("2026-10-21", "nao-saiu", status=EXPIRED),
        numero("2026-10-22", "hoje"),  # o próprio dia não conta
        raiox("2026-10-18", ["Velho", "Demais"]),
        raiox("2026-10-19", ["Trump", "Rússia"]),
        raiox("2026-10-20", [], serie="raiox"),  # Placar do dia: sem entidades
        raiox("2026-10-21", ["SpaceX", "Tesla"], status=PENDING),
        raiox("2026-10-21", ["Expirou", "Junto"], status=EXPIRED),
    ]
    assert fila.chaves_numero("2026-10-22") == {"acum:ibov", "0,3%"}
    assert fila.entidades_raiox("2026-10-22") == [["Trump", "Rússia"], ["SpaceX", "Tesla"]]


def test_refazer_o_dia_tira_o_que_nao_veio_na_geracao_nova():
    fila = Fila(posts=[
        post("2026-10-20-manha", caption="v1"),
        post("2026-10-20-noite", caption="raiox"),  # a geração nova trocou a noite: sai
        post("2026-10-20-numero", status=PUBLISHED),  # publicado: fica
        post("2026-10-20-stories-4", kind="story", images=["s.jpg"], container_id="c9", status=FAILED, attempts=1),
        post("lancamento-comece-aqui", permanent=True),  # permanente: fica
        post("2026-10-19-noite"),  # outro dia: fica
    ])
    fila.posts[4].date = "2026-10-20"
    changed = fila.replace_day("2026-10-20", [post("2026-10-20-manha", caption="v2"), post("2026-10-20-mercado", kind="image", images=["m.jpg"])])
    ids = {p.id for p in fila.posts}
    assert "2026-10-20-noite" not in ids
    assert {"2026-10-20-numero", "2026-10-20-stories-4", "lancamento-comece-aqui", "2026-10-19-noite"} <= ids
    assert sorted(changed) == ["2026-10-20-manha", "2026-10-20-mercado"]
    assert fila.get("2026-10-20-manha").caption == "v2"
