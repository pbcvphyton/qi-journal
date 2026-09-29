"""Envio opcional da edição por e-mail via SMTP (ex.: Gmail com senha de app).

A configuração vem inteiramente do ambiente (segredos do GitHub Actions), para
que nenhum endereço ou senha fique no repositório público:

- ``SMTP_HOST`` (padrão ``smtp.gmail.com``) e ``SMTP_PORT`` (padrão 465).
  Porta 465 usa TLS direto (``SMTP_SSL``); qualquer outra porta (ex.: 587)
  exige ``STARTTLS`` — a senha nunca trafega sem criptografia.
- ``SMTP_USER`` e ``SMTP_PASSWORD``: credenciais.
- ``EMAIL_FROM``: remetente (padrão = ``SMTP_USER``); aceita ``Nome <endereço>``.
- ``EMAIL_TO``: destinatários separados por vírgula (padrão = ``default_to``).

Variáveis vazias contam como ausentes: no Actions, um segredo não cadastrado
chega como string vazia.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Mapping

log = logging.getLogger(__name__)

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 465
SSL_PORT = 465
TIMEOUT_SECONDS = 30

# Senha de app do Google: exibida como 4 grupos de 4 letras separados por espaço.
_GMAIL_APP_PASSWORD = re.compile(r"[a-zA-Z]{4}( [a-zA-Z]{4}){3}")


class EmailDeliveryError(Exception):
    """Falha ao enviar o e-mail (conexão, autenticação ou recusa do servidor)."""


@dataclass
class SMTPSettings:
    """Parâmetros de conexão e envio. ``sender_name`` aparece no campo ``From``."""

    host: str
    port: int
    user: str
    password: str
    sender: str
    to: list[str] = field(default_factory=list)
    sender_name: str | None = None

    def __repr__(self) -> str:  # nunca expor a senha em logs/tracebacks
        return (
            f"SMTPSettings(host={self.host!r}, port={self.port}, user={self.user!r}, "
            f"password='***', sender={self.sender!r}, to={self.to!r}, sender_name={self.sender_name!r})"
        )


def _env(env: Mapping[str, str], name: str) -> str:
    """Valor da variável sem espaços nas pontas ("" se ausente)."""
    return (env.get(name) or "").strip()


def _parse_port(raw: str) -> int:
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"SMTP_PORT inválida: {raw!r} (use 465 ou 587)") from None
    if not 0 < port < 65536:
        raise ValueError(f"SMTP_PORT fora do intervalo válido: {port}")
    return port


def _parse_recipients(raw: str) -> list[str]:
    """Lista de endereços separados por vírgula ou ponto e vírgula, sem repetições."""
    recipients: list[str] = []
    for chunk in re.split(r"[,;]", raw):
        address = parseaddr(chunk.strip())[1]
        if not address:
            continue
        if "@" not in address:
            log.warning("Destinatário de e-mail ignorado (endereço inválido): %r", chunk.strip())
            continue
        if address.lower() not in {r.lower() for r in recipients}:
            recipients.append(address)
    return recipients


def _normalize_password(password: str, host: str) -> str:
    """Remove os espaços da senha de app do Gmail colada como aparece na tela do Google
    (inclusive espaço não separável ou tabulação, comuns ao copiar da página)."""
    if not host.lower().endswith("gmail.com"):
        return password
    spaced = re.sub(r"\s+", " ", password)
    if _GMAIL_APP_PASSWORD.fullmatch(spaced):
        return spaced.replace(" ", "")
    return password


def smtp_settings_from_env(
    env: Mapping[str, str], default_to: list[str], *, sender_name: str | None = None
) -> SMTPSettings | None:
    """Lê a configuração SMTP do ambiente.

    Devolve ``None`` quando o envio não está configurado (faltam usuário, senha
    ou destinatários) — situação normal quando o e-mail sai por outro canal.
    Levanta ``ValueError`` se a configuração existir mas for inválida (porta).
    """
    user = _env(env, "SMTP_USER")
    password = _env(env, "SMTP_PASSWORD")
    to = _parse_recipients(_env(env, "EMAIL_TO")) or _parse_recipients(",".join(default_to or []))
    missing = [
        name for name, value in (("SMTP_USER", user), ("SMTP_PASSWORD", password), ("EMAIL_TO", to)) if not value
    ]
    if missing:
        log.debug("SMTP não configurado (faltando: %s)", ", ".join(missing))
        return None

    host = _env(env, "SMTP_HOST") or DEFAULT_HOST
    port = _parse_port(_env(env, "SMTP_PORT"))
    from_name, from_address = parseaddr(_env(env, "EMAIL_FROM"))
    if not from_address or "@" not in from_address:
        from_name, from_address = "", user
    return SMTPSettings(
        host=host,
        port=port,
        user=user,
        password=_normalize_password(password, host),
        sender=from_address,
        to=to,
        sender_name=from_name or sender_name,
    )


def _single_line(value: str) -> str:
    """Cabeçalhos não podem ter quebras de linha (evita injeção de cabeçalho)."""
    return re.sub(r"\s+", " ", value or "").strip()


def build_message(settings: SMTPSettings, subject: str, html: str, text: str) -> EmailMessage:
    """Monta a mensagem ``multipart/alternative`` (texto puro + HTML)."""
    msg = EmailMessage()
    msg["Subject"] = _single_line(subject)
    msg["From"] = formataddr((_single_line(settings.sender_name or ""), settings.sender))
    # Vários destinatários: cada um recebe como cópia oculta (envelope), e o
    # cabeçalho To mostra só o remetente — ninguém vê o endereço dos outros.
    msg["To"] = settings.to[0] if len(settings.to) == 1 else msg["From"]
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    domain = settings.sender.rpartition("@")[2] or None
    msg["Message-ID"] = make_msgid(idstring="qijournal", domain=domain)
    msg.set_content(text or "", subtype="plain", charset="utf-8")
    msg.add_alternative(html or "", subtype="html", charset="utf-8")
    return msg


def _connect(settings: SMTPSettings) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if settings.port == SSL_PORT:
        return smtplib.SMTP_SSL(settings.host, settings.port, timeout=TIMEOUT_SECONDS, context=context)
    server = smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_SECONDS)
    try:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
    except BaseException:
        server.close()
        raise
    return server


def send_email(settings: SMTPSettings, subject: str, html: str, text: str) -> None:
    """Envia a edição. Levanta :class:`EmailDeliveryError` com mensagem clara em caso de falha."""
    if not settings.to:
        raise EmailDeliveryError("nenhum destinatário configurado (EMAIL_TO)")
    msg = build_message(settings, subject, html, text)
    target = f"{settings.host}:{settings.port}"
    try:
        with _connect(settings) as server:
            server.login(settings.user, settings.password)
            refused = server.send_message(msg, from_addr=settings.sender, to_addrs=settings.to)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError(
            f"autenticação recusada por {target} (código {exc.smtp_code}); "
            "no Gmail, use uma senha de app em SMTP_PASSWORD"
        ) from exc
    except smtplib.SMTPNotSupportedError as exc:
        raise EmailDeliveryError(f"{target} não oferece STARTTLS; use a porta 465 (SSL)") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailDeliveryError(f"{target} recusou todos os destinatários: {', '.join(exc.recipients)}") from exc
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(f"erro SMTP em {target}: {exc}") from exc
    except (UnicodeError, ValueError) as exc:  # ex.: senha com caractere não ASCII (nunca a exibe)
        raise EmailDeliveryError(
            f"configuração SMTP inválida para {target} (provavelmente caractere não ASCII em SMTP_USER/SMTP_PASSWORD): "
            f"{type(exc).__name__}"
        ) from exc
    except OSError as exc:  # DNS, conexão recusada, timeout, certificado (ssl.SSLError)
        raise EmailDeliveryError(f"falha de conexão com {target}: {exc}") from exc

    if refused:
        log.warning("Servidor recusou parte dos destinatários: %s", ", ".join(sorted(refused)))
    delivered = len(settings.to) - len(refused or {})
    log.info("E-mail enviado via %s para %d destinatário(s)", target, delivered)
