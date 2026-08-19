from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import AppConfig, GroupConfig, LLMEndpoint, SMTPConfig, SummaryPolicy
from app.ingestion.service import IngestionService
from app.llm.failover import FailoverLLM
from app.llm.provider import LLMError
from app.models import GroupType, IncomingMessage, MessageSegment
from app.storage.database import Database
from app.summarizer.service import SummaryService


class FakeProvider:
    def __init__(self, name, responses):
        self.name = name
        self.model = f"{name}-model"
        self.responses = list(responses)
        self.calls = 0

    async def generate_json(self, **_):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_config(tmp_path, group_type=GroupType.ACADEMIC):
    policies = {kind: SummaryPolicy(2, 30, 2) for kind in GroupType}
    return AppConfig(
        timezone="Asia/Shanghai", daily_email_time="22:30", host="127.0.0.1", port=8765,
        napcat_webui_url="http://127.0.0.1:6099/webui",
        database=tmp_path / "db.sqlite", raw_message_retention_days=14,
        log_directory=tmp_path / "logs", log_retention_days=14,
        primary_llm=LLMEndpoint("qwen", "qwen", "https://example.com", "KEY", 1),
        fallback_llm=LLMEndpoint("glm", "glm", "https://example.com", "KEY2", 0),
        summary_policies=policies, groups=(GroupConfig("100", "测试群", group_type),),
        smtp=SMTPConfig("smtp.example.com", 465, True, False, "a@b.com", "a@b.com"),
        ingest_token="token", env={"KEY": "x", "KEY2": "y"},
    )


def add_messages(database, count=2):
    service = IngestionService(database)
    now = datetime.now(timezone.utc)
    for index in range(count):
        service.ingest(IncomingMessage(
            qq_message_id=f"m{index}", qq_group_id="100", sender_id=str(index), sender_name=f"成员{index}",
            sent_at=now - timedelta(minutes=count-index),
            segments=(MessageSegment("text", {"text": f"观点 {index}"}),),
        ))


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    return db


@pytest.mark.asyncio
async def test_message_count_triggers_summary_and_claims_messages(tmp_path, database):
    config = make_config(tmp_path)
    database.sync_groups(config.groups)
    add_messages(database)
    primary = FakeProvider("qwen", [{
        "overview": "讨论两个观点", "member_views": [], "disagreements": [], "consensus": [],
        "unresolved_questions": [], "knowledge_tags": ["VLA"]
    }])
    service = SummaryService(database, config, FailoverLLM(primary, primary_retries=0), Path("prompts"))
    batch_id = await service.summarize_group(1, now=datetime.now(timezone.utc))
    assert batch_id == 1
    assert database.pending_messages(1) == []


@pytest.mark.asyncio
async def test_primary_retries_then_fallback(tmp_path, database):
    config = make_config(tmp_path, GroupType.CASUAL)
    database.sync_groups(config.groups)
    add_messages(database)
    primary = FakeProvider("qwen", [LLMError("down"), LLMError("down")])
    fallback = FakeProvider("glm", [{"overview": "有用信息", "noteworthy": [], "plans": [], "resources": []}])
    llm = FailoverLLM(primary, primary_retries=1, fallback=fallback, fallback_retries=0)
    batch_id = await SummaryService(database, config, llm, Path("prompts")).summarize_group(
        1, now=datetime.now(timezone.utc)
    )
    assert batch_id == 1
    assert primary.calls == 2
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_all_provider_failures_leave_messages_pending(tmp_path, database):
    config = make_config(tmp_path)
    database.sync_groups(config.groups)
    add_messages(database)
    primary = FakeProvider("qwen", [LLMError("down")])
    with pytest.raises(LLMError):
        await SummaryService(database, config, FailoverLLM(primary, primary_retries=0), Path("prompts")).summarize_group(
            1, now=datetime.now(timezone.utc)
        )
    assert len(database.pending_messages(1)) == 2


@pytest.mark.asyncio
async def test_force_flushes_below_threshold(tmp_path, database):
    config = make_config(tmp_path, GroupType.COURSE)
    database.sync_groups(config.groups)
    add_messages(database, 1)
    primary = FakeProvider("qwen", [{"notifications": [], "qa_summary": "答疑"}])
    batch_id = await SummaryService(database, config, FailoverLLM(primary, primary_retries=0), Path("prompts")).summarize_group(
        1, now=datetime.now(timezone.utc), force=True
    )
    assert batch_id == 1


@pytest.mark.asyncio
async def test_course_summary_cannot_claim_unknown_source_message(tmp_path, database):
    config = make_config(tmp_path, GroupType.COURSE)
    database.sync_groups(config.groups)
    add_messages(database, 1)
    primary = FakeProvider("qwen", [{
        "notifications": [{
            "title": "通知", "original_text": "不存在", "source_message_id": "not-real",
            "dedup_key": "通知", "update_text": None,
        }],
        "qa_summary": "",
    }])
    service = SummaryService(database, config, FailoverLLM(primary, primary_retries=0), Path("prompts"))
    with pytest.raises(ValueError, match="unknown messages"):
        await service.summarize_group(1, now=datetime.now(timezone.utc), force=True)
    assert len(database.pending_messages(1)) == 1
