"""Generic SMTP transport; Gmail configuration is supplied only via settings."""

from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from html import escape
from typing import Any, Protocol
from urllib.parse import quote

from scout_api.core.config import Settings

logger = logging.getLogger(__name__)


class EmailTransport(Protocol):
    def send(
        self,
        *,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str,
        message_id: str,
    ) -> None: ...


class SmtpEmailTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        text_body: str,
        html_body: str,
        message_id: str,
    ) -> None:
        cfg = self.settings
        if not cfg.email_enabled or not cfg.email_from_address:
            raise RuntimeError("email_transport_disabled")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{cfg.email_from_name} <{cfg.email_from_address}>"
        message["To"] = recipient
        message["Message-ID"] = message_id
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        context = ssl.create_default_context()
        if cfg.smtp_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                cfg.smtp_host,
                cfg.smtp_port,
                timeout=cfg.smtp_timeout_seconds,
                context=context,
            )
        else:
            client = smtplib.SMTP(
                cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_seconds
            )
        try:
            client.ehlo()
            if cfg.smtp_starttls:
                client.starttls(context=context)
                client.ehlo()
            if cfg.smtp_username:
                if not (cfg.smtp_starttls or cfg.smtp_ssl):
                    raise RuntimeError("smtp_credentials_require_tls")
                client.login(cfg.smtp_username, cfg.smtp_password or "")
            client.send_message(message)
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()


def render_price_drop(
    payload: dict[str, Any], *, public_app_url: str
) -> tuple[str, str, str]:
    title = str(payload["product_title"])
    product_id = quote(str(payload["product_id"]), safe="")
    product_url = public_app_url.rstrip("/") + "/admin/produtos/" + product_id
    subject = f"Preço caiu: {title} agora por {payload['new_price_display']}"
    text_body = (
        "Um produto que você favoritou ficou mais barato.\n\n"
        f"{title}\n{payload['store']}\n\n"
        f"Antes: {payload['old_price_display']}\n"
        f"Agora: {payload['new_price_display']}\n"
        f"Economia: {payload['difference_display']} ({payload['percent_drop']}%)\n\n"
        f"Ver produto no PriceScout: {product_url}\n"
        f"Ver oferta na loja: {payload['store_url']}"
    )
    html_body = (
        "<p>Um produto que você favoritou ficou mais barato.</p>"
        f"<h1>{escape(title)}</h1><p>{escape(str(payload['store']))}</p>"
        f"<p>Antes: <s>{escape(str(payload['old_price_display']))}</s><br>"
        f"Agora: <strong>{escape(str(payload['new_price_display']))}</strong><br>"
        f"Você economiza {escape(str(payload['difference_display']))} "
        f"({escape(str(payload['percent_drop']))}%).</p>"
        f'<p><a href="{escape(product_url, quote=True)}">'
        "Ver produto no PriceScout</a></p>"
        f'<p><a href="{escape(str(payload["store_url"]), quote=True)}">'
        "Ver oferta na loja</a></p>"
    )
    return subject, text_body + "\n", html_body


def message_id_for_delivery(delivery_id: uuid.UUID, domain: str) -> str:
    safe_domain = domain.split("@")[-1].strip(" >") or "pricescout.local"
    return f"<{delivery_id}@{safe_domain}>"
