from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import AppConfig
from app.digest.service import DigestService
from app.storage.database import Database
from app.summarizer.service import SummaryService


logger = logging.getLogger(__name__)


def build_scheduler(
    config: AppConfig, database: Database, summarizer: SummaryService, digest: DigestService
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.tzinfo)
    scheduled_time = database.get_setting("daily_email_time") or config.daily_email_time
    hour, minute = (int(part) for part in scheduled_time.split(":"))

    async def summarize_job() -> None:
        try:
            await summarizer.summarize_due_groups()
        except Exception:
            logger.exception("阶段摘要任务失败，消息仍保持 pending")

    async def digest_job() -> None:
        try:
            await digest.deliver()
        except Exception:
            logger.exception("日报发送失败，发送游标未推进")

    def cleanup_job() -> None:
        before = datetime.now(timezone.utc) - timedelta(days=config.raw_message_retention_days)
        try:
            deleted = database.cleanup_summarized_messages(before=before)
            logger.info("原始消息清理完成 deleted=%s", deleted)
        except Exception:
            logger.exception("原始消息清理失败")

    scheduler.add_job(
        summarize_job, IntervalTrigger(minutes=1), id="summarize", max_instances=1, coalesce=True
    )
    scheduler.add_job(
        digest_job,
        CronTrigger(hour=hour, minute=minute, timezone=config.tzinfo),
        id="daily_digest",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        cleanup_job,
        CronTrigger(hour=3, minute=15, timezone=config.tzinfo),
        id="cleanup",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
