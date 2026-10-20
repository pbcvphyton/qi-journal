"""Cliente da API de publicação do Instagram (sem rede: transporte falso)."""

from __future__ import annotations

import pytest

from qijournal.instagram.graph import GraphError, InstagramClient


class FakeTransport:
    """Responde a cada chamada com a próxima resposta da fila e registra as chamadas."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.tokens = []

    def __call__(self, method, url, params, token):
        self.calls.append((method, url, dict(params)))
        self.tokens.append(token)
        if not self.responses:
            raise AssertionError(f"chamada inesperada: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(responses, **kw):
    transport = FakeTransport(responses)
    return InstagramClient("17841400000000000", "SEGREDO", transport=transport, sleep=lambda s: None, **kw), transport


def test_post_image_cria_container_espera_e_publica():
    c, t = client([
        (200, {"id": "c1"}),
        (200, {"status_code": "IN_PROGRESS"}),
        (200, {"status_code": "FINISHED"}),
        (200, {"id": "m1"}),
    ])
    assert c.post_image("https://x/img.jpg", "Legenda", alt_text="Texto alternativo") == "m1"
    method, url, params = t.calls[0]
    assert (method, url) == ("POST", "https://graph.instagram.com/v23.0/17841400000000000/media")
    assert params["image_url"] == "https://x/img.jpg"
    assert params["caption"] == "Legenda"
    assert params["alt_text"] == "Texto alternativo"
    assert "access_token" not in params and set(t.tokens) == {"SEGREDO"}  # token só no cabeçalho
    assert t.calls[1][0] == "GET" and t.calls[1][1].endswith("/c1")
    assert t.calls[-1][1].endswith("/17841400000000000/media_publish")
    assert t.calls[-1][2]["creation_id"] == "c1"


def test_post_carousel_cria_itens_sem_legenda_e_o_carrossel_com_legenda():
    c, t = client([
        (200, {"id": "i1"}), (200, {"id": "i2"}),
        (200, {"status_code": "FINISHED"}), (200, {"status_code": "FINISHED"}),
        (200, {"id": "car"}), (200, {"status_code": "FINISHED"}),
        (200, {"id": "m9"}),
    ])
    assert c.post_carousel(["https://x/1.jpg", "https://x/2.jpg"], "Legenda", ["alt 1"]) == "m9"
    item1, item2 = t.calls[0][2], t.calls[1][2]
    assert item1["is_carousel_item"] == "true" and "caption" not in item1
    assert item1["alt_text"] == "alt 1" and "alt_text" not in item2
    carousel = t.calls[4][2]
    assert carousel["media_type"] == "CAROUSEL"
    assert carousel["children"] == "i1,i2"
    assert carousel["caption"] == "Legenda"


def test_carrossel_exige_de_2_a_10_itens():
    c, _ = client([])
    with pytest.raises(ValueError):
        c.create_carousel(["a"], "x")
    with pytest.raises(ValueError):
        c.create_carousel([str(i) for i in range(11)], "x")


def test_story_usa_media_type_stories_sem_legenda():
    c, t = client([(200, {"id": "s1"}), (200, {"status_code": "FINISHED"}), (200, {"id": "m2"})])
    assert c.post_story("https://x/story.jpg") == "m2"
    params = t.calls[0][2]
    assert params["media_type"] == "STORIES"
    assert "caption" not in params and "alt_text" not in params


def test_erro_passageiro_tenta_de_novo_e_permanente_nao():
    c, t = client([
        (500, {"error": {"message": "falha", "code": 2, "is_transient": True}}),
        (200, {"id": "c1"}),
    ])
    assert c.create_image("https://x/1.jpg") == "c1"
    assert len(t.calls) == 2

    c, t = client([(400, {"error": {"message": "Only photo or video can be accepted", "code": 9004, "error_subcode": 2207052}})])
    with pytest.raises(GraphError) as info:
        c.create_image("https://x/1.jpg")
    assert not info.value.transient
    assert len(t.calls) == 1


def test_token_invalido_e_limite_de_publicacao_sao_identificados():
    c, _ = client([(400, {"error": {"message": "Error validating access token", "code": 190}})])
    with pytest.raises(GraphError) as info:
        c.account()
    assert info.value.token_invalid

    c, t = client([(400, {"error": {"message": "limit", "code": 9, "error_subcode": 2207042}})])
    with pytest.raises(GraphError) as info:
        c.publish("c1")
    assert info.value.publish_limit and not info.value.transient
    assert len(t.calls) == 1


def test_token_nunca_aparece_na_mensagem_de_erro():
    c, _ = client([(400, {"error": {"message": "token SEGREDO inválido", "code": 100}})])
    with pytest.raises(GraphError) as info:
        c.account()
    assert "SEGREDO" not in str(info.value)


def test_container_com_erro_ou_que_nao_fica_pronto():
    c, _ = client([(200, {"status_code": "ERROR"})])
    with pytest.raises(GraphError):
        c.wait_ready("c1")
    c, _ = client([(200, {"status_code": "IN_PROGRESS"})] * 3)
    with pytest.raises(GraphError) as info:
        c.wait_ready("c1", timeout=10, interval=5)
    assert info.value.transient


def test_refresh_token_sem_versao_na_url_e_troca_o_token_em_uso():
    c, t = client([(200, {"access_token": "NOVO", "token_type": "bearer", "expires_in": 5184000}), (200, {"id": "1", "username": "triangula"})])
    info = c.refresh_token()
    assert info.access_token == "NOVO" and info.expires_in == 5184000
    assert t.calls[0][1] == "https://graph.instagram.com/refresh_access_token"
    assert t.calls[0][2]["grant_type"] == "ig_refresh_token"
    assert t.calls[0][2]["access_token"] == "SEGREDO" and t.tokens[0] == ""  # a renovação exige o token na query
    c.account()
    assert t.tokens[1] == "NOVO"


def test_host_do_facebook_login():
    c, t = client([(200, {"data": [{"quota_usage": 3, "config": {"quota_total": 50}}]})], host="graph.facebook.com", version="v24.0")
    assert c.publishing_quota() == (3, 50)
    assert t.calls[0][1] == "https://graph.facebook.com/v24.0/17841400000000000/content_publishing_limit"


def test_publish_nao_repete_as_cegas_e_confere_o_container():
    # media_publish falha com 5xx, mas o container já estava publicado: não publica de novo.
    c, t = client([
        (500, {"error": {"message": "erro", "code": 2, "is_transient": True}}),
        (200, {"status_code": "PUBLISHED"}),
        (200, {"data": [{"id": "m7"}]}),
    ])
    assert c.publish("c1") == "m7"
    assert [call[1].rsplit("/", 1)[-1] for call in t.calls] == ["media_publish", "c1", "media"]

    # Story publicado: não há lista para consultar; fica o id do container.
    c, t = client([(502, {"error": {"message": "erro"}}), (200, {"status_code": "PUBLISHED"})])
    assert c.publish("c2", feed=False) == "c2"

    # Container ainda pronto (FINISHED): tenta publicar mais uma vez.
    c, t = client([(503, {"error": {"message": "erro"}}), (200, {"status_code": "FINISHED"}), (200, {"id": "m8"})])
    assert c.publish("c3") == "m8"
    assert sum(1 for call in t.calls if call[1].endswith("media_publish")) == 2
