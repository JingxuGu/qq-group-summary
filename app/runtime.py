from __future__ import annotations

from dataclasses import dataclass
from contextlib import asynccontextmanager
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
from app.mail.smtp_sender import SMTPMailSender
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
    database.sync_groups(config.groups)
    database.set_setting("raw_message_retention_days", str(config.raw_message_retention_days))
    database.set_setting_if_absent("subscription_email", config.smtp.to_address)
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
    mailer = SMTPMailSender(
        config.smtp, username=config.smtp_username, password=config.smtp_password
    )
    digest = DigestService(database, config, summarizer, mailer)
    scheduler = build_scheduler(config, database, summarizer, digest)
    ingestion = IngestionService(database)
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

    app = create_app(
        database=database,
        ingestion=ingestion,
        ingest_token=config.ingest_token,
        scheduler_running=lambda: scheduler.running,
        lifespan=lifespan,
        web_directory=code_root / "web",
        napcat_webui_url=config.napcat_webui_url,
        allow_user_writes=config.host in {"127.0.0.1", "::1", "localhost"},
        update_delivery_schedule=update_delivery_schedule,
    )
    app.state.runtime = None
    runtime = Runtime(config, database, summarizer, digest, scheduler, app)
    app.state.runtime = runtime
    return runtime
