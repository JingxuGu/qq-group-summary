import json
from datetime import datetime, timedelta, timezone

import pytest

from app.digest.service import DigestService
from app.digest.renderer import render_digest
from app.storage.database import Database


class Row(dict):
    pass


def test_renderer_hides_empty_sections_and_preserves_member_views():
    academic = Row(
        id=1, group_name="VLA 学术群", group_type="academic",
        summary_json=json.dumps({
            "overview": "讨论策略", "member_views": [{"member": "甲", "view": "支持 A"}],
            "disagreements": ["A 与 B"], "consensus": [], "unresolved_questions": [],
            "knowledge_tags": ["VLA", "Diffusion Policy"],
        }, ensure_ascii=False),
    )
    rendered = render_digest([academic], [])
    assert "第二部分：学术群内容" in rendered.text
    assert "甲：支持 A" in rendered.text
    assert "第一部分" not in rendered.text
    assert "第三部分" not in rendered.text


def test_notification_update_keeps_earliest_original(tmp_path):
    from app.config import GroupConfig
    from app.ingestion.service import IngestionService
    from app.models import GroupType, IncomingMessage, MessageSegment

    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    db.sync_groups((GroupConfig("100", "课程群", GroupType.COURSE),))
    ingestion = IngestionService(db)
    now = datetime.now(timezone.utc)
    for message_id, text in (("m1", "周三交作业"), ("m2", "改为周五交作业")):
        ingestion.ingest(IncomingMessage(
            message_id, "100", "u", "老师", now,
            (MessageSegment("text", {"text": text}),),
        ))
    first = db.upsert_notification(
        group_id=1, source_qq_message_id="m1", title="作业截止", original_text="周三交作业",
        dedup_key="作业截止", update_text=None,
    )
    second = db.upsert_notification(
        group_id=1, source_qq_message_id="m2", title="作业截止", original_text="改为周五交作业",
        dedup_key="作业截止", update_text="截止时间改为周五",
    )
    assert first == second
    _, notifications = db.pending_digest()
    assert len(notifications) == 1
    assert notifications[0]["original_text"] == "周三交作业"
    assert notifications[0]["latest_update_text"] == "截止时间改为周五"


def test_update_after_delivery_becomes_pending_but_plain_duplicate_does_not(tmp_path):
    from app.config import GroupConfig
    from app.ingestion.service import IngestionService
    from app.models import GroupType, IncomingMessage, MessageSegment

    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    db.sync_groups((GroupConfig("100", "课程群", GroupType.COURSE),))
    now = datetime.now(timezone.utc)
    ingestion = IngestionService(db)
    for message_id, text in (("m1", "周三交"), ("m2", "转发周三交"), ("m3", "改为周五交")):
        ingestion.ingest(IncomingMessage(message_id, "100", "u", "老师", now, (MessageSegment("text", {"text": text}),)))
    notification_id = db.upsert_notification(
        group_id=1, source_qq_message_id="m1", title="截止", original_text="周三交",
        dedup_key="作业截止", update_text=None,
    )
    delivery_id = db.create_delivery(
        delivery_key="first", window_start=None, window_end=now.isoformat(), subject="日报"
    )
    db.complete_delivery(delivery_id, window_end=now.isoformat(), batch_ids=[], notification_ids=[notification_id])
    db.upsert_notification(
        group_id=1, source_qq_message_id="m2", title="截止", original_text="转发周三交",
        dedup_key="作业截止", update_text=None,
    )
    assert db.pending_digest()[1] == []
    db.upsert_notification(
        group_id=1, source_qq_message_id="m3", title="截止", original_text="改为周五交",
        dedup_key="作业截止", update_text="截止时间改为周五",
    )
    assert len(db.pending_digest()[1]) == 1


def test_cleanup_never_deletes_unsummarized_messages(tmp_path):
    from app.config import GroupConfig
    from app.ingestion.service import IngestionService
    from app.models import GroupType, IncomingMessage, MessageSegment

    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    db.sync_groups((GroupConfig("100", "群", GroupType.CASUAL),))
    old = datetime.now(timezone.utc) - timedelta(days=30)
    IngestionService(db).ingest(IncomingMessage("m1", "100", "u", "人", old, (MessageSegment("text", {"text": "重要"}),)))
    assert db.cleanup_summarized_messages(before=datetime.now(timezone.utc) - timedelta(days=14)) == 0
    assert len(db.pending_messages(1)) == 1


class NoopSummarizer:
    async def summarize_due_groups(self, **_):
        return []


class FakeMailer:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0
        self.last_message = None

    def send(self, **_):
        self.calls += 1
        self.last_message = _
        if self.error:
            raise self.error


class DigestConfig:
    from zoneinfo import ZoneInfo
    tzinfo = ZoneInfo("Asia/Shanghai")
    class smtp:
        to_address = "fallback@example.com"


def seed_academic_batch(db):
    from app.config import GroupConfig
    from app.ingestion.service import IngestionService
    from app.models import GroupType, IncomingMessage, MessageSegment

    db.sync_groups((GroupConfig("100", "学术群", GroupType.ACADEMIC),))
    now = datetime.now(timezone.utc)
    IngestionService(db).ingest(IncomingMessage(
        "m1", "100", "u", "甲", now, (MessageSegment("text", {"text": "讨论 VLA"}),)
    ))
    messages = db.pending_messages(1)
    return db.save_summary_batch(
        group_id=1, messages=messages,
        summary_json=json.dumps({
            "overview": "讨论 VLA", "member_views": [], "disagreements": [], "consensus": [],
            "unresolved_questions": [], "knowledge_tags": ["VLA"],
        }, ensure_ascii=False),
        tags_json='["VLA"]', provider="fake", model="fake", attempts=1,
    )


@pytest.mark.asyncio
async def test_smtp_success_marks_data_sent_and_advances_cursor(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    seed_academic_batch(db)
    db.set_setting("subscription_email", "reader@example.com")
    mailer = FakeMailer()
    service = DigestService(db, DigestConfig(), NoopSummarizer(), mailer)
    result = await service.deliver(now=datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc))
    assert result.sent is True
    assert db.get_setting("last_successful_delivery_at") == "2026-08-19T14:30:00+00:00"
    assert db.pending_digest() == ([], [])
    assert mailer.last_message["to_address"] == "reader@example.com"


@pytest.mark.asyncio
async def test_smtp_failure_keeps_data_pending_and_cursor_unchanged(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    seed_academic_batch(db)
    service = DigestService(db, DigestConfig(), NoopSummarizer(), FakeMailer(RuntimeError("bad password")))
    with pytest.raises(RuntimeError, match="bad password"):
        await service.deliver(now=datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc))
    assert db.get_setting("last_successful_delivery_at") is None
    assert len(db.pending_digest()[0]) == 1
