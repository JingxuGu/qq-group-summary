from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import SMTPConfig


class MailSender(Protocol):
    def send(self, *, subject: str, text: str, html: str, delivery_key: str, to_address: str | None = None) -> None: ...


class SMTPMailSender:
    def __init__(self, config: SMTPConfig, *, username: str, password: str, timeout: float = 30.0):
        self.config = config
        self.username = username
        self.password = password
        self.timeout = timeout

    def send(
        self, *, subject: str, text: str, html: str, delivery_key: str, to_address: str | None = None
    ) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.from_address
        message["To"] = to_address or self.config.to_address
        message["X-QQ-Daily-Delivery-ID"] = delivery_key
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        smtp_class = smtplib.SMTP_SSL if self.config.use_ssl else smtplib.SMTP
        with smtp_class(self.config.host, self.config.port, timeout=self.timeout) as client:
            if self.config.starttls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)
