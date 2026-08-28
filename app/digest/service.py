from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import AppConfig
from app.digest.renderer import render_digest
from app.mail.sender import MailSender
from app.storage.database import Database
from app.summarizer.service import SummaryService


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    sent: bool
    delivery_id: int | None
    reason: str


class DigestService:
    def __init__(self, database: Database, config: AppConfig, summarizer: SummaryService, mailer: MailSender):
        self.database = database
        self.config = config
        self.summarizer = summarizer
        self.mailer = mailer

    async def deliver(self, *, now: datetime | None = None) -> DeliveryResult:
        now = now or datetime.now(timezone.utc)
        await self.summarizer.summarize_due_groups(now=now, force=True)
        batches, notifications = self.database.pending_digest()
        rendered = render_digest(batches, notifications)
        if not rendered.has_content:
            return DeliveryResult(False, None, "no_content")
        window_end = now.astimezone(timezone.utc).isoformat()
        window_start = self.database.get_setting("last_successful_delivery_at")
        local_date = now.astimezone(self.config.tzinfo).date().isoformat()
        subject = f"QQ 消息日报 {local_date}"
        delivery_key = hashlib.sha256(f"{window_start}|{window_end}".encode()).hexdigest()[:24]
        delivery_id = self.database.create_delivery(
            delivery_key=delivery_key, window_start=window_start, window_end=window_end, subject=subject
        )
        try:
            default_recipient = getattr(
                self.config, "email_default_to_address", self.config.smtp.to_address
            )
            recipient = self.database.get_setting("subscription_email") or default_recipient
            await asyncio.to_thread(
                self.mailer.send, subject=subject, text=rendered.text, html=rendered.html,
                delivery_key=delivery_key, to_address=recipient,
            )
        except Exception as exc:
            self.database.fail_delivery(delivery_id, f"{type(exc).__name__}: {exc}")
            raise
        self.database.complete_delivery(
            delivery_id,
            window_end=window_end,
            batch_ids=[row["id"] for row in batches],
            notification_ids=[row["id"] for row in notifications],
        )
        return DeliveryResult(True, delivery_id, "sent")
