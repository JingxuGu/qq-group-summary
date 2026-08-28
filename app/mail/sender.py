from __future__ import annotations

from typing import Protocol


class MailSender(Protocol):
    def send(
        self,
        *,
        subject: str,
        text: str,
        html: str,
        delivery_key: str,
        to_address: str | None = None,
    ) -> None: ...
