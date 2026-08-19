from datetime import datetime, timezone
from pathlib import Path

import pytest
import httpx

from app.api import create_app
from app.config import AppConfig, GroupConfig, LLMEndpoint, SMTPConfig, SummaryPolicy
from app.digest.service import DigestService
from app.ingestion.service import IngestionService
from app.llm.failover import FailoverLLM
from app.models import GroupType
from app.storage.database import Database
from app.summarizer.service import SummaryService


class ScenarioProvider:
    name = "fake"
    model = "fake-model"

    async def generate_json(self, *, system_prompt, user_content):
        if "课程群" in system_prompt:
            return {
                "notifications": [{
                    "title": "作业截止", "original_text": "作业周三交", "source_message_id": "course-1",
                    "dedup_key": "机器人学作业截止", "update_text": None,
                }],
                "qa_summary": "",
            }
        if "学术群" in system_prompt:
            return {
                "overview": "讨论 VLA", "member_views": [{"member": "乙", "view": "支持 action chunking"}],
                "disagreements": [], "consensus": [], "unresolved_questions": [],
                "knowledge_tags": ["VLA", "Action Chunking"],
            }
        return {
            "overview": "周末组织羽毛球", "noteworthy": [], "plans": ["周六十点集合"], "resources": [],
        }


class CapturingMailer:
    def __init__(self):
        self.message = None

    def send(self, **message):
        self.message = message


def config_for(tmp_path):
    policies = {kind: SummaryPolicy(1, 30, 2) for kind in GroupType}
    groups = (
        GroupConfig("100", "课程群", GroupType.COURSE),
        GroupConfig("200", "VLA 学术群", GroupType.ACADEMIC),
        GroupConfig("300", "同学群", GroupType.CASUAL),
    )
    return AppConfig(
        timezone="Asia/Shanghai", daily_email_time="22:30", host="127.0.0.1", port=8765,
        napcat_webui_url="http://127.0.0.1:6099/webui",
        database=tmp_path / "db.sqlite", raw_message_retention_days=14,
        log_directory=tmp_path / "logs", log_retention_days=14,
        primary_llm=LLMEndpoint("fake", "fake", "https://example.com", "KEY", 0),
        fallback_llm=None, summary_policies=policies, groups=groups,
        smtp=SMTPConfig("smtp.example.com", 465, True, False, "a@example.com", "a@example.com"),
        ingest_token="secret", env={"KEY": "secret"},
    )


@pytest.mark.asyncio
async def test_realistic_flow_from_http_ingestion_to_one_daily_email(tmp_path):
    config = config_for(tmp_path)
    database = Database(config.database)
    database.migrate()
    database.sync_groups(config.groups)
    ingestion = IngestionService(database)
    app = create_app(database=database, ingestion=ingestion, ingest_token="secret")
    headers = {"Authorization": "Bearer secret"}
    messages = (
        ("course-1", "100", "老师", "作业周三交"),
        ("academic-1", "200", "乙", "我认为 action chunking 更合适"),
        ("casual-1", "300", "丙", "周六十点打羽毛球"),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for message_id, group_id, sender, text in messages:
            response = await client.post("/api/v1/messages", headers=headers, json={
                "qq_message_id": message_id, "qq_group_id": group_id,
                "sender_id": sender, "sender_name": sender,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "segments": [{"type": "text", "data": {"text": text}}],
            })
            assert response.status_code == 200

    summarizer = SummaryService(
        database, config, FailoverLLM(ScenarioProvider(), primary_retries=0), Path("prompts")
    )
    assert len(await summarizer.summarize_due_groups(now=datetime.now(timezone.utc))) == 3
    summaries = database.summary_feed(group_type="academic", qq_group_id=None, limit=10)
    messages, total = database.message_feed(qq_group_id=None, query="action chunking", limit=10, offset=0)
    assert summaries[0]["summary"]["member_views"][0]["member"] == "乙"
    assert total == 1 and messages[0]["qq_message_id"] == "academic-1"
    mailer = CapturingMailer()
    result = await DigestService(database, config, summarizer, mailer).deliver(
        now=datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc)
    )
    assert result.sent is True
    assert "第一部分：重要通知" in mailer.message["text"]
    assert "第二部分：学术群内容" in mailer.message["text"]
    assert "第三部分：闲聊群总结" in mailer.message["text"]
    assert "乙：支持 action chunking" in mailer.message["text"]
    assert database.pending_digest() == ([], [])
