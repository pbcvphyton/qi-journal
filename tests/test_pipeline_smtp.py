"""Testes do envio SMTP (``qijournal.deliver.smtp``) com servidor falso."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

from qijournal.deliver.smtp import (
    EmailDeliveryError,
    SMTPSettings,
    build_message,
    send_email,
    smtp_settings_from_env,
)

BASE_ENV = {"SMTP_USER": "robo@gmail.com", "SMTP_PASSWORD": "segredo-123", "EMAIL_TO": "leitor@example.com"}


class FakeServer:
    """Imita ``smtplib.SMTP``/``SMTP_SSL`` registrando as chamadas."""

    def __init__(self, registry: Registry, kind: str, host: str, port: int, timeout: float, context=None) -> None:
        self.kind, self.host, self.port, self.timeout, self.context = kind, host, port, timeout, context
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.sent: list[tuple[EmailMessage, str, list[str]]] = []
        self.registry = registry
        registry.servers.append(self)

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context=None) -> None:
        self.calls.append("starttls")
        if self.registry.starttls_error:
            raise self.registry.starttls_error
        self.context = context

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        if self.registry.login_error:
            raise self.registry.login_error
        self.login_args = (user, password)

    def send_message(self, msg: EmailMessage, from_addr: str, to_addrs: list[str]) -> dict:
        self.calls.append("send")
        if self.registry.send_error:
            raise self.registry.send_error
        self.sent.append((msg, from_addr, list(to_addrs)))
        return self.registry.refused

    def close(self) -> None:
        self.calls.append("close")

    def __enter__(self) -> FakeServer:
        return self

    def __exit__(self, *exc) -> None:
        self.calls.append("quit")


class Registry:
    def __init__(self) -> None:
        self.servers: list[FakeServer] = []
        self.connect_error: Exception | None = None
        self.starttls_error: Exception | None = None
        self.login_error: Exception | None = None
        self.send_error: Exception | None = None
        self.refused: dict = {}

    def factory(self, kind: str):
        def create(host, port, timeout=None, context=None, **_):
            if self.connect_error:
                raise self.connect_error
            return FakeServer(self, kind, host, port, timeout, context)

        return create

    @property
    def server(self) -> FakeServer:
        assert len(self.servers) == 1
        return self.servers[0]


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> Registry:
    registry = Registry()
    monkeypatch.setattr(smtplib, "SMTP", registry.factory("plain"))
    monkeypatch.setattr(smtplib, "SMTP_SSL", registry.factory("ssl"))
    return registry


def settings(**overrides) -> SMTPSettings:
    base = dict(
        host="smtp.gmail.com",
        port=465,
        user="robo@gmail.com",
        password="segredo-123",
        sender="robo@gmail.com",
        to=["leitor@example.com", "outro@example.com"],
        sender_name="QI Journal",
    )
    base.update(overrides)
    return SMTPSettings(**base)


# ── smtp_settings_from_env ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "env, default_to",
    [
        ({}, ["leitor@example.com"]),
        ({"SMTP_USER": "robo@gmail.com"}, ["leitor@example.com"]),
        ({"SMTP_PASSWORD": "x"}, ["leitor@example.com"]),
        ({"SMTP_USER": "robo@gmail.com", "SMTP_PASSWORD": "x"}, []),
        ({"SMTP_USER": "  ", "SMTP_PASSWORD": "x", "EMAIL_TO": "a@b.com"}, []),
    ],
)
def test_settings_are_none_when_not_configured(env, default_to):
    assert smtp_settings_from_env(env, default_to) is None


def test_settings_defaults_to_gmail_ssl_and_user_as_sender():
    s = smtp_settings_from_env(
        {"SMTP_USER": "robo@gmail.com", "SMTP_PASSWORD": "x"}, ["leitor@example.com"], sender_name="QI Journal"
    )
    assert s == SMTPSettings(
        host="smtp.gmail.com",
        port=465,
        user="robo@gmail.com",
        password="x",
        sender="robo@gmail.com",
        to=["leitor@example.com"],
        sender_name="QI Journal",
    )


def test_empty_github_secrets_count_as_missing():
    # No Actions, segredo não cadastrado chega como string vazia.
    env = {**BASE_ENV, "SMTP_HOST": "", "SMTP_PORT": "", "EMAIL_FROM": "", "EMAIL_TO": ""}
    s = smtp_settings_from_env(env, ["padrao@example.com"])
    assert (s.host, s.port, s.sender, s.to) == ("smtp.gmail.com", 465, "robo@gmail.com", ["padrao@example.com"])


def test_email_to_overrides_default_and_is_parsed():
    env = {**BASE_ENV, "EMAIL_TO": " a@x.com, Fulano <b@y.com>; A@X.com ,, sem-arroba "}
    s = smtp_settings_from_env(env, ["padrao@example.com"])
    assert s.to == ["a@x.com", "b@y.com"]


def test_custom_host_port_and_sender_with_display_name():
    env = {**BASE_ENV, "SMTP_HOST": "smtp.office365.com", "SMTP_PORT": "587", "EMAIL_FROM": "Redação QI <news@qi.com>"}
    s = smtp_settings_from_env(env, [], sender_name="QI Journal")
    assert (s.host, s.port, s.sender, s.sender_name) == ("smtp.office365.com", 587, "news@qi.com", "Redação QI")


def test_invalid_email_from_falls_back_to_user():
    s = smtp_settings_from_env({**BASE_ENV, "EMAIL_FROM": "não é endereço"}, [], sender_name="QI Journal")
    assert (s.sender, s.sender_name) == ("robo@gmail.com", "QI Journal")


@pytest.mark.parametrize("port", ["abc", "0", "70000", "46 5"])
def test_invalid_port_raises_value_error(port):
    with pytest.raises(ValueError, match="SMTP_PORT"):
        smtp_settings_from_env({**BASE_ENV, "SMTP_PORT": port}, [])


def test_gmail_app_password_spaces_are_removed():
    s = smtp_settings_from_env({**BASE_ENV, "SMTP_PASSWORD": "abcd efgh ijkl mnop"}, [])
    assert s.password == "abcdefghijklmnop"


def test_other_passwords_keep_inner_spaces():
    env = {**BASE_ENV, "SMTP_HOST": "smtp.example.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop"}
    assert smtp_settings_from_env(env, []).password == "abcd efgh ijkl mnop"
    env = {**BASE_ENV, "SMTP_PASSWORD": "minha senha longa"}
    assert smtp_settings_from_env(env, []).password == "minha senha longa"


def test_repr_never_shows_password():
    assert "segredo-123" not in repr(settings())
    assert "***" in repr(settings())


# ── build_message ────────────────────────────────────────────────────────────


def test_message_is_multipart_alternative_with_all_headers():
    msg = build_message(settings(), "QI Journal — Terça-feira, 29 de setembro", "<p>Olá, ação!</p>", "Olá, ação!")
    parsed = message_from_bytes(msg.as_bytes(), policy=policy.default)  # serialização real

    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed["Subject"] == "QI Journal — Terça-feira, 29 de setembro"
    assert parsed["From"].addresses[0].display_name == "QI Journal"
    assert parsed["From"].addresses[0].addr_spec == "robo@gmail.com"
    assert parsed["To"] == "leitor@example.com, outro@example.com"
    assert parsed["Date"]
    assert parsed["Message-ID"].endswith("@gmail.com>")
    parts = [p.get_content_type() for p in parsed.iter_parts()]
    assert parts == ["text/plain", "text/html"]  # HTML por último = preferido
    assert parsed.get_body(("plain",)).get_content().strip() == "Olá, ação!"
    assert parsed.get_body(("html",)).get_content().strip() == "<p>Olá, ação!</p>"


def test_subject_line_breaks_cannot_inject_headers():
    msg = build_message(settings(), "Assunto\r\nBcc: vitima@example.com", "<p>x</p>", "x")
    parsed = message_from_bytes(msg.as_bytes(), policy=policy.default)
    assert parsed["Subject"] == "Assunto Bcc: vitima@example.com"
    assert parsed["Bcc"] is None


def test_sender_without_name_uses_plain_address():
    msg = build_message(settings(sender_name=None), "s", "<p>x</p>", "x")
    assert msg["From"] == "robo@gmail.com"


# ── send_email ───────────────────────────────────────────────────────────────


def test_send_over_ssl_on_port_465(fake_smtp: Registry):
    send_email(settings(), "Assunto", "<p>html</p>", "texto")

    server = fake_smtp.server
    assert (server.kind, server.host, server.port, server.timeout) == ("ssl", "smtp.gmail.com", 465, 30)
    assert isinstance(server.context, ssl.SSLContext)
    assert server.calls == ["login", "send", "quit"]
    assert server.login_args == ("robo@gmail.com", "segredo-123")
    msg, from_addr, to_addrs = server.sent[0]
    assert from_addr == "robo@gmail.com"
    assert to_addrs == ["leitor@example.com", "outro@example.com"]
    assert msg["Subject"] == "Assunto"


def test_send_with_starttls_on_port_587(fake_smtp: Registry):
    send_email(settings(host="smtp.office365.com", port=587), "Assunto", "<p>html</p>", "texto")

    server = fake_smtp.server
    assert (server.kind, server.port, server.timeout) == ("plain", 587, 30)
    assert server.calls == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert isinstance(server.context, ssl.SSLContext)


def test_server_without_starttls_is_closed_and_reported(fake_smtp: Registry):
    fake_smtp.starttls_error = smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
    with pytest.raises(EmailDeliveryError, match="465"):
        send_email(settings(port=587), "s", "h", "t")
    assert "login" not in fake_smtp.server.calls  # a senha nunca vai sem TLS
    assert fake_smtp.server.calls[-1] == "close"


def test_authentication_error_has_clear_message_without_password(fake_smtp: Registry):
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    with pytest.raises(EmailDeliveryError) as excinfo:
        send_email(settings(), "s", "h", "t")
    message = str(excinfo.value)
    assert "senha de app" in message and "535" in message
    assert "segredo-123" not in message
    assert fake_smtp.server.sent == []


def test_connection_failure_becomes_delivery_error(fake_smtp: Registry):
    fake_smtp.connect_error = ConnectionRefusedError(111, "Connection refused")
    with pytest.raises(EmailDeliveryError, match="falha de conexão com smtp.gmail.com:465"):
        send_email(settings(), "s", "h", "t")


def test_timeout_becomes_delivery_error(fake_smtp: Registry):
    fake_smtp.connect_error = TimeoutError("timed out")
    with pytest.raises(EmailDeliveryError, match="timed out"):
        send_email(settings(), "s", "h", "t")


def test_all_recipients_refused(fake_smtp: Registry):
    fake_smtp.send_error = smtplib.SMTPRecipientsRefused({"leitor@example.com": (550, b"no such user")})
    with pytest.raises(EmailDeliveryError, match="leitor@example.com"):
        send_email(settings(), "s", "h", "t")


def test_generic_smtp_error(fake_smtp: Registry):
    fake_smtp.send_error = smtplib.SMTPDataError(552, b"message too big")
    with pytest.raises(EmailDeliveryError, match="erro SMTP"):
        send_email(settings(), "s", "h", "t")


def test_partial_refusal_is_logged_not_raised(fake_smtp: Registry, caplog: pytest.LogCaptureFixture):
    fake_smtp.refused = {"outro@example.com": (550, b"mailbox full")}
    with caplog.at_level(logging.INFO, logger="qijournal.deliver.smtp"):
        send_email(settings(), "s", "h", "t")
    assert "outro@example.com" in caplog.text
    assert "1 destinatário(s)" in caplog.text


def test_no_recipients_is_an_error(fake_smtp: Registry):
    with pytest.raises(EmailDeliveryError, match="EMAIL_TO"):
        send_email(settings(to=[]), "s", "h", "t")
    assert fake_smtp.servers == []
