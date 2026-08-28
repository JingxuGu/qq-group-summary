from __future__ import annotations

from dataclasses import dataclass
from contextlib import asynccontextmanager
import asyncio
import html
import secrets
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api import create_app
from app.config import AppConfig, load_config
from app.digest.service import DigestService
from app.ingestion.service import IngestionService
from app.jobs.scheduler import build_scheduler
from app.llm.failover import FailoverLLM
from app.llm.provider import provider_from_config
from app.mail.factory import mail_sender_from_config
from app.qq_login import NapCatWebUIGateway
from app.storage.database import Database
from app.summarizer.service import SummaryService


@dataclass(slots=True)
class Runtime:
    config: AppConfig
    database: Database
    summarizer: SummaryService
    digest: DigestService
    scheduler: AsyncIOScheduler
    app: FastAPI


def build_runtime(config_path: str | Path = "config.yaml") -> Runtime:
    config = load_config(config_path)
    database = Database(config.database)
    database.migrate()
    database.set_setting("raw_message_retention_days", str(config.raw_message_retention_days))
    database.set_setting_if_absent("subscription_email", config.email_default_to_address)
    database.set_setting_if_absent("daily_email_time", config.daily_email_time)
    primary = provider_from_config(config.primary_llm, config.api_key_for(config.primary_llm))
    fallback = None
    fallback_retries = 0
    if config.fallback_llm:
        fallback = provider_from_config(config.fallback_llm, config.api_key_for(config.fallback_llm))
        fallback_retries = config.fallback_llm.retries
    llm = FailoverLLM(
        primary,
        primary_retries=config.primary_llm.retries,
        fallback=fallback,
        fallback_retries=fallback_retries,
        retry_delay=1.0,
    )
    root = Path(config_path).resolve().parent
    code_root = Path(__file__).resolve().parents[1]
    prompt_directory = root / "prompts"
    if not prompt_directory.exists():
        prompt_directory = code_root / "prompts"
    summarizer = SummaryService(database, config, llm, prompt_directory)
    mailer = mail_sender_from_config(config)
    digest = DigestService(database, config, summarizer, mailer)
    scheduler = build_scheduler(config, database, summarizer, digest)
    ingestion = IngestionService(database)
    qq_login_gateway = NapCatWebUIGateway(
        config.napcat_webui_url, config.napcat_webui_token
    ) if config.napcat_webui_token else None
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not scheduler.running:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=False)

    def update_delivery_schedule(value: str) -> None:
        from apscheduler.triggers.cron import CronTrigger

        hour, minute = (int(part) for part in value.split(":"))
        scheduler.reschedule_job(
            "daily_digest", trigger=CronTrigger(hour=hour, minute=minute, timezone=config.tzinfo)
        )

    async def send_subscription_confirmation(email: str, daily_time: str) -> None:
        subject = "QQ 群聊日报订阅成功"
        text = (
            "你的 QQ 群聊日报订阅已生效。\n\n"
            f"收件地址：{email}\n"
            f"每日发送时间：{daily_time}（{config.timezone}）\n\n"
            "这是订阅确认邮件，不包含任何群聊内容。"
        )
        escaped_email = html.escape(email)
        escaped_time = html.escape(daily_time)
        escaped_timezone = html.escape(config.timezone)
        body = (
            "<h2>QQ 群聊日报订阅成功</h2>"
            "<p>你的日报订阅已经生效。</p>"
            f"<p><strong>收件地址：</strong>{escaped_email}<br>"
            f"<strong>每日发送时间：</strong>{escaped_time}（{escaped_timezone}）</p>"
            "<p>这是订阅确认邮件，不包含任何群聊内容。</p>"
        )
        await asyncio.to_thread(
            mailer.send,
            subject=subject,
            text=text,
            html=body,
            delivery_key=f"subscription-{secrets.token_hex(12)}",
            to_address=email,
        )

    app = create_app(
        database=database,
        ingestion=ingestion,
        ingest_token=config.ingest_token,
        scheduler_running=lambda: scheduler.running,
        lifespan=lifespan,
        web_directory=code_root / "web",
        napcat_webui_url=config.napcat_webui_url,
        qq_login_gateway=qq_login_gateway,
        allow_user_writes=config.host in {"127.0.0.1", "::1", "localhost"},
        update_delivery_schedule=update_delivery_schedule,
        summarize_now=lambda: summarizer.summarize_due_groups(force=True),
        send_subscription_confirmation=send_subscription_confirmation,
        mailjet_webhook_username=config.mailjet_webhook_username,
        mailjet_webhook_password=config.mailjet_webhook_password,
    )
    app.state.runtime = None
    runtime = Runtime(config, database, summarizer, digest, scheduler, app)
    app.state.runtime = runtime
    return runtime
