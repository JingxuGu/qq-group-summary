from __future__ import annotations

from app.config import AppConfig
from app.mail.mailjet_sender import MailjetMailSender
from app.mail.sender import MailSender
from app.mail.smtp_sender import SMTPMailSender


def mail_sender_from_config(config: AppConfig) -> MailSender:
    if config.email_provider == "mailjet":
        return MailjetMailSender(
            config.mailjet,
            api_key=config.mailjet_api_key,
            secret_key=config.mailjet_secret_key,
        )
    return SMTPMailSender(
        config.smtp,
        username=config.smtp_username,
        password=config.smtp_password,
    )
